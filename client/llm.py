# llm.py
import os, json, re
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError  
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

MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite"
]
# --- Utilidad: limpia ```json ... ``` ---
def _clean_json_block(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"```$", "", s)
    return s.strip()

# --- MODELOS ---
MODEL = "gemini-2.5-flash"

# --- PLANNER: detecta intención y propone acciones MCP ---
# --- PLANNER: detecta intención y propone acciones MCP ---
PLANNER_CFG = types.GenerateContentConfig(
    system_instruction=(
        """Eres un planner para un host MCP (Filesystem, Git y Spotify).
            Devuelve SOLO JSON válido sin backticks con el formato:
            {
            "reply_preview": string,
            "thought": string,
            "actions": [ { "server": "filesystem" | "git" | "spotify", "tool": string, "args": object } ]
            }

            REGLAS ESTRICTAS DE ORDEN Y PRECONDICIONES (Filesystem/Git):
            1) SI vas a usar git sobre <repo_path>, DEBES incluir antes:
            filesystem.create_directory { path: <repo_path> }.
            2) SI vas a escribir un archivo <repo_path>/X, DEBES garantizar que existe <repo_path>
            con filesystem.create_directory ANTES del write_file.
            3) Orden canónico: create_directory → write_file(s) → git_init → git_add → git_commit.

            Herramientas permitidas:
            - filesystem.create_directory { path }
            - filesystem.write_file { path, content }
            - git.git_init { repo_path }
            - git.git_add { repo_path, files: [..] }
            - git.git_commit { repo_path, message }
            Nunca inventes resultados; no ejecutes nada, solo planea.

            === Spotify ===
            También puedes planificar acciones para el servidor 'spotify'. En cada acción usa:
            { "server": "spotify", "tool": <nombre>, "args": { ... } } y NO antepongas 'spotify.' al campo tool.

            Herramientas Spotify (sin prefijo):
            - whoami { }
            - auth_begin { }
            - auth_complete { redirect_url }
            - search_track { query, market?, limit? }
            - analyze_mood { prompt }
            - get_recommendations { seed_tracks?, mood?, energy?, valence?, danceability?, tempo?, limit? }
            - explain_selection { tracks, context }
            - build_playlist_from_profile { mood_prompt, name?, public?, limit? }
            - create_playlist { name, description?, public? }
            - create_public_mix { mood_prompt, name?, limit? }
            - add_to_playlist { playlist_id, track_ids }
            - ensure_device_ready { }
            - play_playlist { playlist_id, device_id? }

            Política de elección (Spotify):
            • Si piden buscar canciones por texto o idea: usa search_track.
            • Si piden recomendaciones con seeds y/o un mood: usa get_recommendations.
            • Si piden crear una playlist completa desde su perfil/gustos: usa build_playlist_from_profile.
            • Si piden reproducir una playlist: usa ensure_device_ready → play_playlist.
            Regla de formato: el campo "tool" NO debe incluir el nombre del servidor (usa "get_recommendations", NO "spotify.get_recommendations").

            Criterios de resultado (música):
            • Si piden 'N canciones de <género>' (p.ej. rock), planifica get_recommendations o search_track; apunta a VARIEDAD:
            máximo 1 tema por artista, mezcla de épocas (clásico/90s/moderno) y subestilos.
            • Si la intención es ambigua (solo 'rock'), no pidas confirmación: asume mezcla variada y la cantidad solicitada.
            • Si una tool Spotify falla, igualmente prepara un reply_preview útil (fallback) y propone seguir explorando.
            • En reply_preview incluye SIEMPRE 2–3 follow-ups (p.ej. “¿Más clásico?”, “¿Más moderno?”, “¿Solo instrumentales?”).
            • Si el servidor está en modo bot (no requiere login del usuario y creará la playlist en una cuenta central), usa create_public_mix en lugar de build_playlist_from_profile.

            REGLAS DE LOGIN SPOTIFY:
            • Antes de usar create_playlist / add_to_playlist / build_playlist_from_profile / ensure_device_ready / play_playlist planifica:
            1) {"server":"spotify","tool":"whoami","args":{}}
            2) Si no hay sesión, planifica {"server":"spotify","tool":"auth_begin","args":{}} en lugar de los pasos que requieren OAuth.
            • Si el usuario pega una URL con 'code=' (callback), planifica:
            {"server":"spotify","tool":"auth_complete","args":{"redirect_url":"<esa URL>"}}.
            • Si el usuario dice “con qué link”, “conectar spotify”, “login spotify”:
            planifica {"server":"spotify","tool":"auth_begin","args":{}}.

            PATRÓN: ONBOARDING DE GÉNERO
            (cuando el usuario dice: "adentrarme / entrar a / por dónde empezar / bandas para empezar" + nombre de género)
            → Devuelve de 6 a 8 acciones 'spotify.search_track' (limit=1 cada una) con queries de bandas icónicas del género y una canción representativa.
            El texto final lo redactará el asistente, pero DEBE haber esas tool calls.
            • market y limit son opcionales; usa limit=1 por banda.

            EJEMPLO (solo referencia, no lo imprimas):
            Usuario: "Si busco adentrarme en el mundo o el genero del Rock, que bandas me recomendarías"
            → actions: [
            {"server":"spotify","tool":"search_track","args":{"query":"Queen Bohemian Rhapsody","limit":1}},
            {"server":"spotify","tool":"search_track","args":{"query":"Led Zeppelin Stairway to Heaven","limit":1}},
            {"server":"spotify","tool":"search_track","args":{"query":"Pink Floyd Comfortably Numb","limit":1}},
            {"server":"spotify","tool":"search_track","args":{"query":"The Beatles Let It Be","limit":1}},
            {"server":"spotify","tool":"search_track","args":{"query":"AC/DC Back In Black","limit":1}},
            {"server":"spotify","tool":"search_track","args":{"query":"Nirvana Smells Like Teen Spirit","limit":1}}
            ]
            """
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

# --- FINALIZER: redacta respuesta natural tras ejecutar herramientas ---
FINALIZER_CFG = types.GenerateContentConfig(
    system_instruction=(
        """Eres el asistente del usuario. Responde en español, claro y conciso.
Usa SOLO los 'execution_results' que te paso para redactar una respuesta natural (sin JSON ni backticks).

REGLAS GENERALES:
• No reveles ni menciones JSON interno.
• Si hubo errores, indícalo breve y sugiere el siguiente paso inmediato.
• Evita repetir lo mismo dos veces; prioriza lo más útil.

FLUJO DE AUTENTICACIÓN SPOTIFY (muy importante):
1) Si aparece una acción spotify.whoami con authed=false O cualquier intento a create_playlist/add_to_playlist/... falló por OAuth:
   - Si existe un resultado de spotify.auth_begin con 'authorize_url', muéstralo como enlace:
     “Para conectar tu cuenta de Spotify, entra aquí: <authorize_url>”.
   - Explica el paso 2 en una sola línea:
     “Cuando Spotify te redirija a http://127.0.0.1/..., copia y pega aquí la URL completa para completar el login.”
   - Cierra con 2–3 follow-ups breves (ej.: “¿Te espero mientras haces login?”, “¿Prefieres que primero armemos la lista fuera de tu cuenta?”).

2) Si existe un resultado de spotify.auth_complete con ok=true:
   - Confirma conexión: “¡Listo! Conectado como <display_name|id>.”
   - Ofrece continuar la acción original (p. ej., crear la playlist pedida) en la misma respuesta.

3) Si existe spotify.whoami con authed=true:
   - Da por conectada la cuenta y continúa con lo solicitado (crear playlist, añadir temas, reproducir, etc.).

CREACIÓN Y GESTIÓN DE PLAYLISTS:
• Si build_playlist_from_profile devolvió playlist_id y/o url:
  - Confirma creación y muestra el enlace: “Creé tu playlist: <url>”.
• Si create_playlist devolvió playlist_id/url y luego add_to_playlist devolvió added=N:
  - “Creé la playlist y agregué N canciones: <url>”.
• Si ensure_device_ready / play_playlist devolvieron estados:
  - "ready": “Tu dispositivo está listo.”
  - "playing": “Reproduciendo tu playlist ahora.”
  - "not_premium": “No puedo iniciar la reproducción (requiere Premium).”
  - "no_devices": “No encontré dispositivos activos de Spotify Connect.”

RECOMENDACIONES / BÚSQUEDAS MUSICALES:
• Si hay resultados de get_recommendations o search_track:
  - Lista exactamente la cantidad pedida si el usuario la indicó, o 10 por defecto.
  - Máximo 1 canción por artista (variedad). Quita duplicados evidentes.
  - Si el usuario pidió por GÉNERO (p. ej., “rock alternativo”):
    intenta filtrar mentalmente para que encaje el género; si algún tema claramente no encaja, no lo incluyas.
• Si no hubo resultados útiles:
  - Devuelve una lista CURADA breve basada en conocimiento general (mezcla clásico/90s/moderno) acorde al pedido.

MENSAJES DE AYUDA / ERRORES FRECUENTES:
• Si detectas texto como “illegal scope”, “invalid_client” o errores de permisos:
  - Indica: “Parece haber un problema de permisos o credenciales de Spotify. Reintenta el login con el enlace de conexión y verifica que el redirect URI y los scopes sean los correctos.”

CIERRE SIEMPRE CON FOLLOW-UPS (elige 2–3):
• “¿Te paso 5 más del mismo estilo?”
• “¿Más clásico o más moderno?”
• “¿Quieres que la reproduzca en tu dispositivo?”
• “¿La hago pública o la dejo privada?”
"""
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

# === Fallback planner sin LLM ===
def _extract_count(text: str, default_n: int = 10) -> int:
    m = re.search(r"(\d+)", text)
    n = int(m.group(1)) if m else default_n
    return max(1, min(25, n))

def fallback_plan(user_msg: str) -> dict:
    text = (user_msg or "").lower()
    n = _extract_count(text, 10)

    # Heurística mínima por género “rock and roll”
    if "rock and roll" in text or "rock" in text:
        classics = [
            "Chuck Berry Johnny B. Goode",
            "Elvis Presley Jailhouse Rock",
            "Little Richard Tutti Frutti",
            "Jerry Lee Lewis Great Balls of Fire",
            "Bill Haley Rock Around the Clock",
            "Buddy Holly Peggy Sue",
            "The Beatles Twist and Shout",
            "The Rolling Stones (I Can't Get No) Satisfaction",
        ]
        actions = [
            {"server":"spotify","tool":"search_track","args":{"query":q,"limit":1}}
            for q in classics[:n]
        ]
        return {
            "reply_preview": f"Te paso {n} clásicos de rock and roll. ¿Más 50s o más 60s? ¿Quieres que lo arme en una playlist pública?",
            "thought": "Planner local por 503/overload del LLM.",
            "actions": actions
        }

    # fallback genérico: recomendaciones por mood
    actions = [{
        "server": "spotify",
        "tool": "get_recommendations",
        "args": {"mood":"party", "limit": n}
    }]
    return {
        "reply_preview": f"Te paso {n} recomendaciones rápidas. ¿Quieres otro mood o género?",
        "thought": "Planner local genérico por 503/overload del LLM.",
        "actions": actions
    }


def plan_llm(user_msg: str, history_msgs: list[dict]) -> dict:
    # arma transcripción corta con rol para que entienda co-referencias tipo "pública"
    transcript = "\n".join(
        f"{m.get('role','user')}: {m.get('content','')}"
        for m in (history_msgs or [])[-12:]
        if isinstance(m, dict) and m.get("content")
    )
    prompt = (
        f"Transcripción (últimos turnos):\n{transcript}\n\n"
        f"Orden actual del usuario:\n{user_msg}\n\n"
        "Genera el JSON del plan."
    )

    last_err = None
    for mdl in MODEL_CANDIDATES:
        try:
            resp = client.models.generate_content(model=mdl, contents=prompt, config=PLANNER_CFG)
            raw = (resp.text or "").strip()
            cleaned = _clean_json_block(raw)
            data = json.loads(cleaned)
            reply_preview = (data.get("reply_preview") or "").strip()
            thought = (data.get("thought") or "").strip()
            actions = data.get("actions") or []
            if not isinstance(actions, list):
                actions = []
            return {"reply_preview": reply_preview, "thought": thought, "actions": actions}
        except (ServerError, ClientError, json.JSONDecodeError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    fb = fallback_plan(user_msg)
    fb["thought"] += f" (motivo: {type(last_err).__name__})"
    return fb


# --- FINALIZER: redacta respuesta natural tras ejecutar herramientas ---
FINALIZER_CFG = types.GenerateContentConfig(
    system_instruction=(
        "Eres el asistente del usuario. Con base en los 'execution_results' que te paso, "
        "redacta SOLO una respuesta natural y útil (sin JSON, sin backticks)."
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

def _collect_tracks(execution_results: list[dict]) -> list[dict]:
    out = []
    for r in execution_results or []:
        if r.get("server") != "spotify" or not r.get("ok"):
            continue
        res = r.get("result") or {}
        # Convención de tu cliente: a veces llega en structuredContent.result
        sc = (res.get("structuredContent") or {}).get("result")
        items = sc if isinstance(sc, list) else None
        if not items and isinstance(res, dict) and "content" in res:
            # ya viene “normalizado” por mcp; ignora
            pass
        if isinstance(items, list):
            for t in items:
                if isinstance(t, dict) and t.get("name") and t.get("artists"):
                    out.append(t)
    return out

def _local_finalize(user_msg: str, execution_results: list[dict]) -> str:
    tracks = _collect_tracks(execution_results)
    if tracks:
        uniq = []
        seen_artists = set()
        for t in tracks:
            artists = ", ".join(a.get("name","") for a in t.get("artists", []))
            key = artists.lower()
            if key in seen_artists:
                continue
            seen_artists.add(key)
            uniq.append(f"- {t.get('name')} — {artists}")
        body = "\n".join(uniq[:10]) if uniq else "No encontré resultados."
        return f"Aquí tienes:\n{body}\n\n¿Te paso 5 más del mismo estilo? · ¿Más clásico o más moderno? · ¿La convierto en playlist pública?"
    # Sin tracks: mensaje mínimo
    return "Listo. ¿Quieres que lo convierta en playlist o cambiamos el mood?"

def finalize_llm(user_msg: str, execution_results: list[dict]) -> str:
    try:
        jsonable_results = _to_jsonable(execution_results)
        prompt = (
            "Usuario pidió:\n"
            f"{user_msg}\n\n"
            "Resultados de ejecución (JSON):\n"
            f"{json.dumps(jsonable_results, ensure_ascii=False, indent=2)}\n\n"
            "Redacta una respuesta natural para el usuario (sin JSON)."
        )
        # intenta con modelos en cascada
        last_err = None
        for mdl in MODEL_CANDIDATES:
            try:
                resp = client.models.generate_content(model=mdl, contents=prompt, config=FINALIZER_CFG)
                return (resp.text or "").strip()
            except (ServerError, ClientError) as e:
                last_err = e
                continue
        # si todos fallan
        return _local_finalize(user_msg, execution_results)
    except Exception:
        return _local_finalize(user_msg, execution_results)
QA_CFG = types.GenerateContentConfig(
    system_instruction=(
        "Eres un asistente de conocimiento general. Responde en español, "
        "claro y conciso. Para biografías, da 5–7 puntos clave y 1 línea final "
        "de por qué es importante."
    ),
    thinking_config=types.ThinkingConfig(thinking_budget=0),
)

MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

def ask_llm(question: str) -> str:
    q = (question or "").strip()
    if not q:
        return "¿Qué te gustaría saber?"
    last_err = None
    for mdl in MODEL_CANDIDATES:
        try:
            resp = client.models.generate_content(model=mdl, contents=q, config=QA_CFG)
            txt = (resp.text or "").strip()
            if txt:
                return txt
        except (ServerError, ClientError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    return "No pude consultar el modelo ahora mismo. Inténtalo de nuevo en unos segundos."