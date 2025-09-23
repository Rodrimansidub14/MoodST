# mcp_minimal.py — bucle MCP por stdio (mínimo)
import sys, json, re
from typing import Dict, Any, Optional, Callable
ENC = "utf-8"

def _respond(obj: Dict[str, Any]) -> None:
    body = json.dumps(obj).encode(ENC)
    hdr  = f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode(ENC)
    sys.stdout.buffer.write(hdr); sys.stdout.buffer.write(body); sys.stdout.flush()

def _read_request() -> Optional[Dict[str, Any]]:
    headers = b""
    while b"\r\n\r\n" not in headers:
        ch = sys.stdin.buffer.read(1)
        if not ch: return None
        headers += ch
    m = re.search(rb"Content-Length:\s*(\d+)", headers, re.I)
    if not m: return None
    length = int(m.group(1))
    body = sys.stdin.buffer.read(length)
    try: return json.loads(body.decode(ENC, errors="ignore"))
    except Exception: return None

def serve(tools: Dict[str, Callable[..., Any]], server_name="mcp-minimal", version="0.1.0") -> None:
    while True:
        req = _read_request()
        if not req: return
        mid = req.get("id"); method = req.get("method")
        if method == "initialize":
            _respond({"jsonrpc":"2.0","id":mid,"result":{
                "protocolVersion":"2025-06-18",
                "serverInfo":{"name":server_name,"version":version},
                "capabilities":{"tools":{"listChanged": False}}
            }})
        elif method == "tools/list":
            _respond({"jsonrpc":"2.0","id":mid,"result":{
                "tools":[{"name":k,"description":(v.__doc__ or "")} for k,v in tools.items()]
            }})
        elif method == "tools/call":
            p = req.get("params",{}); name = p.get("name"); args = p.get("arguments") or {}
            if name not in tools:
                _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":f"Herramienta no encontrada: {name}"}})
            else:
                try:
                    out = tools[name](**args) if isinstance(args, dict) else tools[name]()
                    _respond({"jsonrpc":"2.0","id":mid,"result":{"content": out}})
                except Exception as e:
                    _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32000,"message":str(e)}})
        else:
            _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":f"Método desconocido: {method}"}})
