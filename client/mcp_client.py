# client/mcp_client.py
import asyncio
from typing import List, Dict, Any
from contextlib import AsyncExitStack
import sys, os, subprocess

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from pydantic import BaseModel as _BM
except Exception:
    _BM = None

def _dump_result(res_obj):
    if _BM and isinstance(res_obj, _BM):
        data = res_obj.model_dump()
    elif isinstance(res_obj, dict):
        data = res_obj
    else:
        try:
            # objetos simples
            from dataclasses import asdict
            return asdict(res_obj)  # por si acaso
        except Exception:
            data = {"_repr": repr(res_obj)}
    # normaliza bandera de error
    is_err = bool(data.get("isError"))
    # extrae texto si existe
    text_chunks = []
    for item in (data.get("content") or []):
        t = item.get("text")
        if t:
            text_chunks.append(t)
    if text_chunks:
        data["_text"] = "\n".join(text_chunks)
    return data, is_err

def _abspath(p: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))

def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))

def _git_cli_init(path: str) -> str:
    # Inicializa con git CLI para que el server MCP pueda arrancar
    cp = subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=False,
    )
    return cp.stdout

def _nearest_existing_dir(p: str) -> str:
    """Devuelve el ancestro existente más cercano de p (o la raíz)."""
    p = _abspath(p)
    while not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:  # llegamos a raíz
            break
        p = parent
    return p


def _collect_target_paths(actions: List[Dict[str, Any]]) -> List[str]:
    """
    Recolecta todos los campos 'path' y 'repo_path' de acciones para calcular
    directorios permitidos en Filesystem MCP.
    """
    paths = []
    for a in actions:
        args = a.get("args", {})
        for k in ("path", "repo_path"):
            v = args.get(k)
            if v:
                paths.append(v)
    return paths


async def _fs_call(session: ClientSession, tool: str, args: Dict[str, Any]):
    if tool == "create_directory":
        return await session.call_tool("create_directory", {"path": _abspath(args["path"])})
    if tool == "write_file":
        return await session.call_tool("write_file", {"path": _abspath(args["path"]), "content": args["content"]})
    raise ValueError(f"Filesystem tool no soportada: {tool}")


async def _git_call(session: ClientSession, tool: str, args: Dict[str, Any]):
    rp = _abspath(args.get("repo_path", "."))
    if tool == "git_init":
        return await session.call_tool("git_init", {"repo_path": rp})
    if tool == "git_add":
        files = [_abspath(f) for f in args["files"]]
        return await session.call_tool("git_add", {"repo_path": rp, "files": files})
    if tool == "git_commit":
        return await session.call_tool("git_commit", {"repo_path": rp, "message": args["message"]})
    raise ValueError(f"Git tool no soportada: {tool}")


async def execute_plan(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ejecuta acciones MCP y devuelve [{server, tool, args, ok, result|error}, ...].
    - Filesystem se abre una vez con dirs permitidos “sanos” (ancestros existentes).
    - Git se abre LAZY por repo cuando llega la primera acción git_* para ese repo.
    """
    results: List[Dict[str, Any]] = []

    # --- Calcular dirs permitidos para Filesystem ---
    targets = _collect_target_paths(actions)
    allowed_dirs = sorted({ _nearest_existing_dir(os.path.dirname(t)) for t in targets if t })

    async with AsyncExitStack() as stack:
        # ------- Filesystem (npx / npx.cmd en Windows) -------
        fs_session = None
        if allowed_dirs:
            npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
            fs_params = StdioServerParameters(
                command=npx_cmd,
                args=["-y", "@modelcontextprotocol/server-filesystem", *allowed_dirs],
                env={**os.environ, "DEBUG": "mcp*,*", "MCP_LOG_LEVEL": "debug", "NO_COLOR": "1"},
            )
            try:
                fs_read, fs_write = await stack.enter_async_context(stdio_client(fs_params))
                fs_session = ClientSession(fs_read, fs_write)
                await stack.enter_async_context(fs_session)
                await fs_session.initialize()
            except Exception as e:
                results.append({
                    "server": "filesystem", "tool": "init",
                    "args": {"allowed_dirs": allowed_dirs}, "ok": False,
                    "error": f"No se pudo iniciar Filesystem MCP: {e}"
                })
                fs_session = None  # marca como no disponible

        # ------- Git (lazy por repo) -------
        git_sessions: Dict[str, ClientSession] = {}

        async def ensure_git_session(repo_path: str) -> ClientSession:
            rp = _abspath(repo_path)
            os.makedirs(rp, exist_ok=True)          # 1) garantiza carpeta

            if not _is_git_repo(rp):                # 2) si no es repo, init con CLI
                _git_cli_init(rp)

            git_params = StdioServerParameters(     # 3) usa el MISMO Python del app
                command=sys.executable,
                args=["-m", "mcp_server_git", "--repository", rp],
                env={**os.environ, "NO_COLOR": "1"},
            )
            g_read, g_write = await stack.enter_async_context(stdio_client(git_params))
            g_sess = ClientSession(g_read, g_write)
            await stack.enter_async_context(g_sess)
            await g_sess.initialize()
            git_sessions[rp] = g_sess
            return g_sess

        # ------- Ejecutar acciones en orden -------
        for a in actions:
            server = a.get("server")
            tool   = a.get("tool")
            args   = a.get("args", {})
            try:
                if server == "filesystem":
                    if not fs_session:
                        raise RuntimeError("Filesystem MCP no disponible.")
                    # 👇 Si es write_file y el parent no existe, créalo antes
                    if tool == "write_file":
                        parent = os.path.dirname(_abspath(args["path"]))
                        # crea el directorio del repo (un nivel) con el server FS
                        await _fs_call(fs_session, "create_directory", {"path": parent})
                    res = await _fs_call(fs_session, tool, args)
                    res_json, is_err = _dump_result(res)
                    if is_err:
                        results.append({"server": server, "tool": tool, "args": args, "ok": False,
                                        "error": res_json.get("_text") or "Tool returned isError", "result": res_json})
                    else:
                        results.append({"server": server, "tool": tool, "args": args, "ok": True,
                                        "result": res_json})

                    
                elif server == "git":
                    rp = args.get("repo_path", ".")
                    # Lazy start: asegura que la carpeta exista si el plan lo omitió
                    repo_dir = _abspath(rp)
                    parent = os.path.dirname(repo_dir)
                    if not os.path.isdir(repo_dir):
                        # si el primer paso git es git_init, la carpeta puede no existir todavía;
                        # creamos el parent por si acaso (la carpeta del repo la crea filesystem.create_directory)
                        os.makedirs(parent, exist_ok=True)
                    g = await ensure_git_session(repo_dir)
                    res = await _git_call(g, tool, args)
                    res_json, is_err = _dump_result(res)
                    if is_err:
                        results.append({"server": server, "tool": tool, "args": args, "ok": False,
                                        "error": res_json.get("_text") or "Tool returned isError", "result": res_json})
                    else:
                        results.append({"server": server, "tool": tool, "args": args, "ok": True,
                                        "result": res_json})

                else:
                    raise ValueError(f"Servidor no soportado: {server}")

                results.append({"server": server, "tool": tool, "args": args, "ok": True, "result": res})
            except Exception as e:
                results.append({"server": server, "tool": tool, "args": args, "ok": False, "error": str(e)})

    return results


def fix_plan(actions: list[dict]) -> list[dict]:
    out = []
    seen_repo_dirs = set()
    for a in actions:
        if a["server"] == "git":
            rp = a["args"]["repo_path"]
            repo_dir = _abspath(rp)
            # inserta create_directory antes de la primera acción git para ese repo
            if repo_dir not in seen_repo_dirs:
                out.append({"server":"filesystem","tool":"create_directory","args":{"path": repo_dir}})
                seen_repo_dirs.add(repo_dir)
        if a["server"] == "filesystem" and a["tool"] == "write_file":
            parent = os.path.dirname(_abspath(a["args"]["path"]))
            # fuerza create_directory antes del write_file
            out.append({"server":"filesystem","tool":"create_directory","args":{"path": parent}})
        out.append(a)
    return out


def execute_plan_blocking(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return asyncio.run(execute_plan(actions))

