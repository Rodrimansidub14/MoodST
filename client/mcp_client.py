# client/mcp_client.py
import asyncio
from typing import List, Dict, Any, Optional
from contextlib import AsyncExitStack
import sys, os, subprocess

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import json as _json

try:
    from pydantic import BaseModel as _BM
except Exception:
    _BM = None


# ====== Configuración del server MCP de Spotify ======
# Puedes sobreescribir por env:
#  - MCP_SPOTIFY_ENTRY="mcp_server_spotify"        → python -m mcp_server_spotify
#  - MCP_SPOTIFY_ENTRY="path/a/server_spotify.py"  → python path/a/server_spotify.py
_MSP_ENTRY = os.environ.get("MCP_SPOTIFY_ENTRY", r"C:\Users\rodri\Documents\Redes\MoodST\mcp\spotify\server.py").strip()

if _MSP_ENTRY:
    if _MSP_ENTRY.endswith(".py"):
        SPOTIFY_SERVER_CMD = sys.executable
        SPOTIFY_SERVER_ARGS = [os.path.abspath(_MSP_ENTRY)]
    else:
        SPOTIFY_SERVER_CMD = sys.executable
        SPOTIFY_SERVER_ARGS = ["-m", _MSP_ENTRY]
else:
    # Valor por defecto: módulo instalable de tu server
    SPOTIFY_SERVER_CMD = sys.executable
    SPOTIFY_SERVER_ARGS = ["-m", "mcp.spotify.server"]  # <-- ajusta si tu módulo se llama distinto


def _dump_result(res_obj):
    """Normaliza la respuesta de call_tool a dict y marca isError si corresponde."""
    if _BM and isinstance(res_obj, _BM):
        data = res_obj.model_dump()
    elif isinstance(res_obj, dict):
        data = res_obj
    else:
        try:
            from dataclasses import asdict
            data = asdict(res_obj)  # por si fuese dataclass
        except Exception:
            data = {"_repr": repr(res_obj)}

    # bandera de error (protocolo MCP)
    is_err = bool(data.get("isError"))

    # extrae texto (si lo hay) de 'content'
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
    """Inicializa un repo Git con la CLI para que el server MCP pueda abrirlo sin quejarse."""
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
        if parent == p:
            break
        p = parent
    return p


def _collect_target_paths(actions: List[Dict[str, Any]]) -> List[str]:
    """
    Recolecta todos los 'path' y 'repo_path' para calcular dirs permitidos (Filesystem MCP).
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

    - Filesystem: se abre una vez con dirs permitidos “sanos” (ancestros existentes).
    - Git: sesión LAZY por repo cuando llega la primera acción git_* para ese repo.
    - Spotify: sesión LAZY cuando aparece la primera acción del server "spotify" o "spotify-context".
    """
    results: List[Dict[str, Any]] = []

    # --- Calcular dirs permitidos para Filesystem ---
    targets = _collect_target_paths(actions)
    allowed_dirs = sorted({_nearest_existing_dir(os.path.dirname(t)) for t in targets if t})

    async with AsyncExitStack() as stack:
        # ------- Filesystem (npx / npx.cmd en Windows) -------
        fs_session: Optional[ClientSession] = None
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
                fs_session = None

        # ------- Git (lazy por repo) -------
        git_sessions: Dict[str, ClientSession] = {}

        async def ensure_git_session(repo_path: str) -> ClientSession:
            rp = _abspath(repo_path)
            if rp in git_sessions:
                return git_sessions[rp]

            os.makedirs(rp, exist_ok=True)  # 1) garantiza carpeta
            if not _is_git_repo(rp):        # 2) si no es repo, init con CLI
                _git_cli_init(rp)

            git_params = StdioServerParameters(  # 3) usa el MISMO Python del app
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

        # ------- Spotify (lazy global) -------
        spotify_session: Optional[ClientSession] = None
        PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # ajusta si hace falta
        BASE_ENV = {
            **os.environ,
            "NO_COLOR": "1",
            "PYTHONPATH": os.pathsep.join([os.environ.get("PYTHONPATH", ""), PROJECT_ROOT]),
        }

        params = StdioServerParameters(
            command=SPOTIFY_SERVER_CMD,
            args=SPOTIFY_SERVER_ARGS,
            env=BASE_ENV,
        )

        async def ensure_spotify_session() -> ClientSession:
            nonlocal spotify_session
            if spotify_session:
                return spotify_session
            params = StdioServerParameters(
                command=SPOTIFY_SERVER_CMD,
                args=SPOTIFY_SERVER_ARGS,
                env=BASE_ENV,  # ← en vez de {**os.environ, "NO_COLOR":"1"}
            )

            try:
                sp_read, sp_write = await stack.enter_async_context(stdio_client(params))
                spotify_session = ClientSession(sp_read, sp_write)
                await stack.enter_async_context(spotify_session)
                await spotify_session.initialize()
                return spotify_session
            except Exception as e:
                # Propaga un error legible para el finalizer
                raise RuntimeError(f"No pude iniciar el servidor MCP de Spotify. CMD={SPOTIFY_SERVER_CMD} ARGS={SPOTIFY_SERVER_ARGS} ERROR={e}")

        # ------- Ejecutar acciones en orden -------
        for a in actions:
            server = a.get("server")
            tool   = a.get("tool")
            args   = a.get("args", {}) or {}

            try:
                if server == "filesystem":
                    if not fs_session:
                        raise RuntimeError("Filesystem MCP no disponible.")
                    if tool == "write_file":
                        parent = os.path.dirname(_abspath(args["path"]))
                        await _fs_call(fs_session, "create_directory", {"path": parent})
                    res = await _fs_call(fs_session, tool, args)
                    res_json, is_err = _dump_result(res)
                    results.append({
                        "server": server, "tool": tool, "args": args,
                        "ok": not is_err,
                        "result": None if is_err else res_json,
                        "error": (res_json.get("_text") or "Tool returned isError") if is_err else None,
                    })

                elif server == "git":
                    rp = args.get("repo_path", ".")
                    repo_dir = _abspath(rp)
                    parent = os.path.dirname(repo_dir)
                    if not os.path.isdir(repo_dir):
                        os.makedirs(parent, exist_ok=True)
                    g = await ensure_git_session(repo_dir)
                    res = await _git_call(g, tool, args)
                    res_json, is_err = _dump_result(res)
                    results.append({
                        "server": server, "tool": tool, "args": args,
                        "ok": not is_err,
                        "result": None if is_err else res_json,
                        "error": (res_json.get("_text") or "Tool returned isError") if is_err else None,
                    })

                elif server == "spotify":
                    if isinstance(tool, str) and tool.startswith("spotify."):
                        tool = tool.split(".", 1)[1]
                    sp = await ensure_spotify_session()

                    # ==== tools soportadas ====
                    if tool == "whoami":
                        res = await sp.call_tool("whoami", {})
                    elif tool == "auth_begin":
                        res = await sp.call_tool("auth_begin", {})
                    elif tool == "auth_complete":
                        payload = {}
                        if "code" in args and args["code"]:
                            payload["code"] = args["code"]
                        elif "redirect_url" in args and args["redirect_url"]:
                            payload["redirect_url"] = args["redirect_url"]
                        else:
                            raise ValueError("auth_complete requiere 'code' o 'redirect_url'.")
                        res = await sp.call_tool("auth_complete", payload)


                    elif tool == "search_track":
                        res = await sp.call_tool("search_track", {
                            "query": args["query"],
                            "market": args.get("market"),
                            "limit": int(args.get("limit", 10)),
                        })
                    elif tool == "analyze_mood":
                        res = await sp.call_tool("analyze_mood", {"prompt": args["prompt"]})
                    elif tool == "get_recommendations":
                        res = await sp.call_tool("get_recommendations", {
                            "seed_tracks": args.get("seed_tracks") or [],
                            "mood": args.get("mood"),
                            "energy": args.get("energy"),
                            "valence": args.get("valence"),
                            "danceability": args.get("danceability"),
                            "tempo": args.get("tempo"),
                            "limit": int(args.get("limit", 20)),
                        })
                    elif tool == "create_playlist_with_tracks":
                        res = await sp.call_tool("create_playlist_with_tracks", {
                            "name": args["name"],
                            "track_ids": args.get("track_ids") or [],
                            "description": args.get("description", ""),
                            "public": bool(args.get("public", False)),
                        })


                    elif tool == "explain_selection":
                        res = await sp.call_tool("explain_selection", {
                            "tracks": args["tracks"],
                            "context": args["context"],
                        })
                    elif tool == "build_playlist_from_profile":
                        res = await sp.call_tool("build_playlist_from_profile", {
                            "mood_prompt": args["mood_prompt"],
                            "name": args.get("name"),
                            "public": bool(args.get("public", False)),
                            "limit": int(args.get("limit", 25)),
                        })
                    elif tool == "create_playlist":
                        res = await sp.call_tool("create_playlist", {
                            "name": args["name"],
                            "description": args.get("description", ""),
                            "public": bool(args.get("public", False)),
                        })
                    elif tool == "add_to_playlist":
                        res = await sp.call_tool("add_to_playlist", {
                            "playlist_id": args["playlist_id"],
                            "track_ids": args["track_ids"],
                        })
                    elif tool == "ensure_device_ready":
                        res = await sp.call_tool("ensure_device_ready", {})
                    elif tool == "play_playlist":
                        res = await sp.call_tool("play_playlist", {
                            "playlist_id": args["playlist_id"],
                            "device_id": args.get("device_id"),
                        })
                    elif tool == "create_public_mix":
                        res = await sp.call_tool("create_public_mix", {
                            "mood_prompt": args["mood_prompt"],
                            "name": args.get("name", "Bot Mix"),
                            "limit": int(args.get("limit", 20)),
                        })

                    else:
                        raise ValueError(f"Herramienta spotify no soportada: {tool}")

                    res_json, is_err = _dump_result(res)
                    if not is_err and "_text" in res_json and isinstance(res_json.get("_text"), str):
                        try:
                            parsed = _json.loads(res_json["_text"])
                            res_json["parsed"] = parsed
                        except Exception:
                            pass
                    if is_err and ("OAuth" in (res_json.get("_text","")) or "login" in (res_json.get("_text",""))):
                        try:
                            ab = await sp.call_tool("auth_begin", {})
                            ab_json, _ = _dump_result(ab)
                            results.append({
                                "server": "spotify", "tool": "auth_begin", "args": {},
                                "ok": True, "result": ab_json, "error": None
                            })
                        except Exception as e2:
                            results.append({
                                "server":"spotify","tool":"auth_begin","args":{},"ok":False,"error":str(e2)
                            })

                    results.append({
                        "server": server, "tool": tool, "args": args,
                        "ok": not is_err,
                        "result": res_json if not is_err else None,
                        "error": res_json.get("_text") if is_err else None
                    })

            except Exception as e:
                results.append({
                    "server": server, "tool": tool, "args": args,
                    "ok": False,
                    "result": None,
                    "error": str(e)
                })

    return results


def fix_plan(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Inserta precondiciones: create_directory antes de git y antes de write_file."""
    out: List[Dict[str, Any]] = []
    seen_repo_dirs = set()

    for a in actions:
        srv = a.get("server")
        tl  = a.get("tool")
        args = a.get("args", {}) or {}

        if srv == "git":
            rp = args.get("repo_path", ".")
            repo_dir = _abspath(rp)
            if repo_dir not in seen_repo_dirs:
                out.append({"server": "filesystem", "tool": "create_directory", "args": {"path": repo_dir}})
                seen_repo_dirs.add(repo_dir)

        if srv == "filesystem" and tl == "write_file":
            parent = os.path.dirname(_abspath(args["path"]))
            out.append({"server": "filesystem", "tool": "create_directory", "args": {"path": parent}})

        out.append(a)

    return out


def execute_plan_blocking(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return asyncio.run(execute_plan(actions))
