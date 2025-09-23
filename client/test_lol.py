import json, os
from mcp_client import execute_plan_blocking

# (opcional) si moviste el server:
os.environ["MCP_LOL_ENTRY"] = r"C:\Users\rodri\Documents\Redes\MoodST\mcp\lol\server.py"

actions = [
  {"server":"lol","tool":"fetch_static_data","args":{"ddragon_version":"latest","lang":"en_US"}},
  {"server":"lol","tool":"plan_build","args":{
      "ally_champion":"gnar","ally_characteristic":"TANK",
      "enemy_team":["morgana","lulu","ryze","garen","aatrox"]
  }}
]

res = execute_plan_blocking(actions)
print(json.dumps(res, indent=2, ensure_ascii=False))
