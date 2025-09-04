# llm.py
import os, json
from dotenv import load_dotenv
from google import genai
from google.genai import types
import re

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("Falta GEMINI_API_KEY en .env")

client = genai.Client(api_key=API_KEY)

CFG = types.GenerateContentConfig(
    system_instruction="Responde en JSON con {\"reply\": string, \"thought\": string}",
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

def ask_llm(user_msg: str, history: list[str]) -> dict:
    context = "\n".join(history[-10:]) if history else ""
    prompt = f"Contexto:\n{context}\n\nPregunta:\n{user_msg}"

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=CFG,
    )
    raw = resp.text or ""

    # 🔹 Limpiar bloques tipo ```json ... ```
    cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip())

    try:
        data = json.loads(cleaned)
        reply = data.get("reply", "").strip()
        thought = data.get("thought", "").strip()
    except Exception:
        # fallback: si no se puede parsear, usamos el texto crudo como reply
        reply, thought = raw.strip(), ""

    return {"reply": reply or "(sin respuesta)", "thought": thought}