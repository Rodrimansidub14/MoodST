import json, os
from mcp_client import execute_plan_blocking

# Asegúrate que tu FastAPI esté corriendo:
# uvicorn main:app --port 8000
# os.environ["MCP_MOVIES_HTTP_URL"] = "http://127.0.0.1:8000/mcp/jsonrpc"

actions = [
  {"server":"movies","tool":"search_movie","args":{"title":"Inception"}},
  {"server":"movies","tool":"get_movie_recommendations","args":{"genres":["878","28"],"min_rating":7.5}},
  {"server":"movies","tool":"get_random_movie","args":{}},
]
res = execute_plan_blocking(actions)
print(json.dumps(res, indent=2, ensure_ascii=False))
