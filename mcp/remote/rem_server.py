from fastapi import FastAPI, Request
import datetime

app = FastAPI()

@app.post("/mcp/jsonrpc")
async def mcp_handler(req: Request):
    data = await req.json()
    method = data.get("method")
    req_id = data.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"status": "ok"}}

    if method == "tools/call":
        name = data["params"]["name"]
        if name == "current_time":
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            return {"jsonrpc":"2.0","id":req_id,
                    "result":{"result": now}}

        return {"jsonrpc":"2.0","id":req_id,
                "error":{"code":-32601,"message":f"Tool {name} not found"}}

    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": "Unknown method"}}
