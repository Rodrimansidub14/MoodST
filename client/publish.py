# publishing.py
import os, subprocess

def _abs(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))

def _run(cmd: list[str], cwd: str) -> dict:
    cp = subprocess.run(
        cmd, cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False
    )
    return {"ok": cp.returncode == 0, "cmd": " ".join(cmd), "output": cp.stdout.strip()}

def publish_repo(repo_path: str, remote_url: str, commit_msg: str | None = None, add_all: bool = True) -> list[dict]:
    """Publica un repo local en GitHub (HTTPS). Devuelve una lista de pasos {ok, cmd, output}."""
    repo_path = _abs(repo_path)
    steps: list[dict] = []

    # 1) add/commit (tolerante a 'nothing to commit')
    if add_all:
        steps.append({**_run(["git", "add", "-A"], repo_path), "step": "git add -A"})

    if commit_msg:
        res = _run(["git", "commit", "-m", commit_msg], repo_path)
        if "nothing to commit" in res["output"].lower():
            res["ok"] = True
        res["step"] = "git commit"
        steps.append(res)

    # 2) determinar rama actual (main/master)
    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    branch = r["output"] if r["ok"] else "main"
    steps.append({**r, "step": "detect branch"})

    # 3) origin (si existe, set-url)
    r = _run(["git", "remote", "add", "origin", remote_url], repo_path)
    if (not r["ok"]) and "already exists" in r["output"].lower():
        r = _run(["git", "remote", "set-url", "origin", remote_url], repo_path)
    r["step"] = "set origin"
    steps.append(r)

    # 4) push
    steps.append({**_run(["git", "push", "-u", "origin", branch], repo_path), "step": "git push"})
    return steps
