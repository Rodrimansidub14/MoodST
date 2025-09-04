# llm.py
import os, json, re
from dotenv import load_dotenv
from google import genai
from google.genai import types
# llm.py (añade arriba)
try:
    from pydantic import BaseModel as _PydBase
except Exception:
    _PydBase = None

def _to_jsonable(obj):
    # Maneja recursivo: BaseModel, listas, dicts...
    if _PydBase and isinstance(obj, _PydBase):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("Falta GEMINI_API_KEY en .env")

client = genai.Client(api_key=API_KEY)

# --- Utilidad: limpia ```json ... ``` ---
def _clean_json_block(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"```$", "", s)
    return s.strip()

# --- MODELOS ---
MODEL = "gemini-2.5-flash"

# --- PLANNER: detecta intención y propone acciones MCP ---
PLANNER_CFG = types.GenerateContentConfig(
    system_instruction=(
        "Eres un planner para un host MCP (Filesystem y Git). "
        "Devuelve SOLO JSON válido sin backticks con el formato:\n"
        "{\n"
        '  "reply_preview": string,\n'
        '  "thought": string,\n'
        '  "actions": [ { "server": "filesystem" | "git", "tool": string, "args": object } ]\n'
        "}\n\n"
        "REGLAS ESTRICTAS DE ORDEN Y PRECONDICIONES:\n"
        "1) SI vas a usar git sobre <repo_path>, DEBES incluir antes: "
        "`filesystem.create_directory { path: <repo_path> }`.\n"
        "2) SI vas a escribir un archivo <repo_path>/X, DEBES garantizar que existe <repo_path> "
        "con `filesystem.create_directory` ANTES del `write_file`.\n"
        "3) Orden canónico: create_directory → write_file(s) → git_init → git_add → git_commit.\n"
        "Herramientas permitidas:\n"
        "- filesystem.create_directory { path }\n"
        "- filesystem.write_file { path, content }\n"
        "- git.git_init { repo_path }\n"
        "- git.git_add { repo_path, files: [..] }\n"
        "- git.git_commit { repo_path, message }\n"
        "Nunca inventes resultados; no ejecutes nada, solo planea."
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)


def plan_llm(user_msg: str, history: list[str]) -> dict:
    context = "\n".join(history[-10:]) if history else ""
    prompt = (
        f"Contexto (últimos mensajes del usuario):\n{context}\n\n"
        f"Orden actual del usuario:\n{user_msg}\n\n"
        "Genera el JSON del plan."
    )
    resp = client.models.generate_content(model=MODEL, contents=prompt, config=PLANNER_CFG)
    raw = resp.text or ""
    cleaned = _clean_json_block(raw)
    try:
        data = json.loads(cleaned)
        reply_preview = (data.get("reply_preview") or "").strip()
        thought = (data.get("thought") or "").strip()
        actions = data.get("actions") or []
        # validación mínima
        if not isinstance(actions, list):
            actions = []
        return {"reply_preview": reply_preview, "thought": thought, "actions": actions}
    except Exception:
        # Fallback sin acciones
        return {"reply_preview": raw.strip() or "Procesando...", "thought": "", "actions": []}

# --- FINALIZER: redacta respuesta natural tras ejecutar herramientas ---
FINALIZER_CFG = types.GenerateContentConfig(
    system_instruction=(
        "Eres el asistente del usuario. Con base en los 'execution_results' que te paso, "
        "redacta SOLO una respuesta natural y útil (sin JSON, sin backticks)."
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

def finalize_llm(user_msg: str, execution_results: list[dict]) -> str:
    jsonable_results = _to_jsonable(execution_results)  # 👈 sanitiza
    prompt = (
        "Usuario pidió:\n"
        f"{user_msg}\n\n"
        "Resultados de ejecución (JSON):\n"
        f"{json.dumps(jsonable_results, ensure_ascii=False, indent=2)}\n\n"
        "Redacta una respuesta natural para el usuario (sin JSON)."
    )
    resp = client.models.generate_content(model=MODEL, contents=prompt, config=FINALIZER_CFG)
    return (resp.text or "").strip()
