import json, os, datetime

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"mcp-{datetime.date.today().isoformat()}.jsonl")

def log_mcp(entry: dict):
    entry["ts"] = datetime.datetime.now().isoformat()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
