# mcp_client.py
import asyncio
import os
from typing import Iterable, Tuple

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# ---------- Helpers de conexión ----------

def _norm(path: str) -> str:
    return os.path.normpath(path)

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

async def _connect_stdio(command: str, args: Iterable[str]) -> ClientSession:
    """
    Abre una sesión MCP por STDIO con el comando dado (p.ej. 'npx' o 'python -m ...').
    Devuelve una ClientSession inicializada (debes cerrarla con 'async with').
    """
    params = StdioServerParameters(command=command, args=list(args))
    read, write = await stdio_client(params).__aenter__()
    session = ClientSession(read, write)
    await session.initialize()
    return session

# ---------- Conexiones a servidores ----------

async def connect_filesystem(allowed_dirs: Iterable[str]) -> ClientSession:
    """
    Filesystem server oficial:
    npx -y @modelcontextprotocol/server-filesystem <dir1> <dir2> ...
    """
    args = ["-y", "@modelcontextprotocol/server-filesystem", *allowed_dirs]
    return await _connect_stdio("npx", args)

async def connect_git(repo_path: str) -> ClientSession:
    """
    Git MCP (local) vía pip:
    python -m mcp_server_git --repository <repo_path>
    """
    repo = _norm(repo_path)
    return await _connect_stdio("python", ["-m", "mcp_server_git", "--repository", repo])

# ---------- Wrappers de herramientas ----------

async def fs_create_directory(fs: ClientSession, path: str):
    return await fs.call_tool("create_directory", {"path": _norm(path)})

async def fs_write_file(fs: ClientSession, path: str, content: str):
    return await fs.call_tool("write_file", {"path": _norm(path), "content": content})

async def git_init(git: ClientSession, repo_path: str):
    return await git.call_tool("git_init", {"repo_path": _norm(repo_path)})

async def git_add(git: ClientSession, repo_path: str, files: Iterable[str]):
    files = [ _norm(f) for f in files ]
    return await git.call_tool("git_add", {"repo_path": _norm(repo_path), "files": files})

async def git_commit(git: ClientSession, repo_path: str, message: str):
    return await git.call_tool("git_commit", {"repo_path": _norm(repo_path), "message": message})

# ---------- Flujo DEMO pedido por el enunciado ----------

async def demo_init_repo_flow(base_dir: str, repo_name: str, readme_text: str) -> Tuple[str, str]:
    """
    1) crea carpeta
    2) escribe README.md
    3) git init
    4) git add README.md
    5) git commit -m "Initial commit"

    Devuelve (repo_path, readme_path)
    """
    base_dir = _norm(base_dir)
    repo_path = _norm(os.path.join(base_dir, repo_name))
    readme_path = _norm(os.path.join(repo_path, "README.md"))

    _ensure_dir(base_dir)

    # 1. Filesystem server con acceso al base_dir
    async with (await connect_filesystem([base_dir])) as fs:
        # Asegura carpeta repo y README
        await fs_create_directory(fs, repo_path)
        await fs_write_file(fs, readme_path, readme_text)

    # 2. Git server apuntando al repo
    async with (await connect_git(repo_path)) as git:
        await git_init(git, repo_path)
        await git_add(git, repo_path, ["README.md"])
        await git_commit(git, repo_path, "Initial commit")

    return repo_path, readme_path

def run_demo_blocking(base_dir: str, repo_name: str, readme_text: str) -> Tuple[str, str]:
    """Conveniencia para entornos síncronos (Streamlit/CLI)."""
    return asyncio.run(demo_init_repo_flow(base_dir, repo_name, readme_text))
