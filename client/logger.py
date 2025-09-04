# logger.py
import json, os, datetime

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"mcp-{datetime.date.today().isoformat()}.jsonl")

try:
    from pydantic import BaseModel
except Exception:
    BaseModel = None

def _json_default(o):
    # Modelos Pydantic (MCP usa Pydantic v2)
    if BaseModel and isinstance(o, BaseModel):
        return o.model_dump()
    # bytes -> texto
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", errors="replace")
    # último recurso
    return repr(o)

def log_mcp(entry: dict):
    entry["ts"] = datetime.datetime.now().isoformat()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=_json_default, ensure_ascii=False) + "\n")
