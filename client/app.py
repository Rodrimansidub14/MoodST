# app.py — Retro CRT + Show Reasoning + Typewriter
import streamlit as st
from datetime import datetime
from mcp_client import execute_plan_blocking, fix_plan
import time
import urllib.parse

from typing import Optional
import re
from logger import log_mcp
from llm import plan_llm, finalize_llm, fallback_plan
try:
    from llm import ask_llm
except Exception:
    ask_llm = None

from dotenv import load_dotenv
import json

load_dotenv()
HOST = "MoodST"
st.set_page_config(page_title=HOST, layout="wide")

# ===== CSS CRT =====
st.markdown(r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=VT323&display=swap');
:root{ --crt-green:#2cff76; --crt-bg:#000000; --crt-bright:#b9ffc9; }
html, body, [data-testid="stAppViewContainer"], * {
  background:var(--crt-bg)!important; color:var(--crt-green)!important;
  font-family:'VT323', ui-monospace, Menlo, Monaco, 'Courier New', monospace !important;
}
.main .block-container{
  border:2px solid var(--crt-green); border-radius:8px; padding:18px 20px 10px;
  box-shadow:0 0 12px rgba(44,255,118,0.18), inset 0 0 24px rgba(44,255,118,0.10);
  background:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,0.03)),
    repeating-linear-gradient(180deg,rgba(44,255,118,0.07)0,rgba(44,255,118,0.07)1px,transparent 2px,transparent 3px);
}
.crt-header{display:flex;gap:10px;align-items:center;margin-bottom:8px;font-size:32px;
  text-shadow:0 0 6px var(--crt-green),0 0 16px rgba(44,255,118,0.5)}
.crt-header .host{color:var(--crt-bright)} .crt-header .blink{animation:flick 1.2s steps(2,end) infinite}
@keyframes flick{50%{opacity:.35}}
.line{white-space:pre-wrap;line-height:1.25;font-size:20px;margin:6px 0;text-shadow:0 0 4px rgba(44,255,118,.65)}
.tag{color:var(--crt-bright);margin-right:6px} .time{color:#5cff9e;opacity:.6;margin-left:6px;font-size:18px}
[data-testid="stChatInput"] textarea, textarea, input[type="text"]{
  background:#000!important;color:var(--crt-green)!important;border:1px solid var(--crt-green)!important;
  border-radius:6px!important;box-shadow:inset 0 0 10px rgba(44,255,118,.2)!important;font-family:"VT323",monospace!important;font-size:20px!important;
}
button[kind="secondary"], button[kind="primary"]{
  background:#001a00!important;border:1px solid var(--crt-green)!important;color:var(--crt-green)!important;text-shadow:0 0 4px rgba(44,255,118,.65)
}
header, [data-testid="stToolbar"]{visibility:hidden;height:0}
.reasoning-expander > div{border:1px dashed var(--crt-green); padding:8px}
</style>
""", unsafe_allow_html=True)

# ===== Header =====
st.markdown(f"""
<div class="crt-header">
  <span class="host">[{HOST}]</span> <span class="blink">▮</span>
</div>
""", unsafe_allow_html=True)

# ===== Estado =====
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role":"assistant","content":"Bienvenido. ¿Qué quieres hacer hoy?","thought":"","ts":datetime.now()},
    ]
if "music_ctx" not in st.session_state:
    st.session_state.music_ctx = {
        "basket_track_ids": [],
        "last_playlist_name": None,
        "last_public": None,
    }

def fmt_time(ts: datetime) -> str:
    return ts.strftime("%H:%M:%S")

def render_line(role: str, content: str, ts: datetime):
    tag = f"[{HOST}]" if role=="assistant" else "[you]"
    st.markdown(f'<div class="line"><span class="tag">{tag}</span>{content}<span class="time">{fmt_time(ts)}</span></div>',
                unsafe_allow_html=True)
    

def typewriter(container, text: str, delay: float = 0.01):
    """Efecto de tipeo en tiempo real."""
    out = ""
    for ch in text:
        out += ch
        container.markdown(f'<div class="line"><span class="tag">[{HOST}]</span>{out}</div>', unsafe_allow_html=True)
        time.sleep(delay)
def _playlist_summary_text(results: list[dict]) -> Optional[str]:
        url = None
        added = None
        for r in results or []:
            if r.get("server") != "spotify" or not r.get("ok"):
                continue

            if r.get("tool") in ("create_playlist", "build_playlist_from_profile", "create_playlist_with_tracks"):
                data = (r.get("result") or {}).get("parsed")
                if not data:
                    raw = (r.get("result") or {}).get("_text")
                    try:
                        data = json.loads(raw) if raw else None
                    except Exception:
                        data = None
                if isinstance(data, dict):
                    url = data.get("url") or url
                    if data.get("added") is not None:
                        added = data["added"]

            if r.get("tool") == "add_to_playlist":
                data = (r.get("result") or {}).get("parsed")
                if not data:
                    raw = (r.get("result") or {}).get("_text")
                    try:
                        data = json.loads(raw) if raw else None
                    except Exception:
                        data = None
                if isinstance(data, dict) and data.get("added") is not None:
                    added = data["added"]

        if url:
            return f"✅ Creé tu playlist y agregué {added} canciones: {url}" if added is not None else f"✅ Creé tu playlist: {url}"
        return None
# === Bloque de conexión Spotify (compacto, sin pegar URL) ===

def _whoami() -> dict:
    res = execute_plan_blocking([{"server":"spotify","tool":"whoami","args":{}}])
    for r in res:
        if r.get("server")=="spotify" and r.get("tool")=="whoami" and r.get("ok"):
            data = (r.get("result") or {}).get("structuredContent", {}) or {}
            data = data.get("result") or {}
            return data if isinstance(data, dict) else {"authed": False}
    return {"authed": False}

# 1) Si volvimos de Spotify con ?code=..., completa OAuth sin pedir nada
params = st.query_params if hasattr(st, "query_params") else st.experimental_get_query_params()
code_in_url = params.get("code", [None])
code_in_url = code_in_url[0] if isinstance(code_in_url, list) else code_in_url

if code_in_url:
    # Llama a auth_complete con code (no necesitamos la URL entera)
    res = execute_plan_blocking([{"server":"spotify","tool":"auth_complete","args":{"code": code_in_url}}])
    # Limpia el query param para no reintentar en cada rerun
    if hasattr(st, "query_params"):
        st.query_params.clear()
    else:
        st.experimental_set_query_params()

# 2) UI de estado + botón “Conectar Spotify”
who = _whoami()
if not who.get("authed") and st.session_state.get("last_auth_url"):
    st.link_button("Conectar Spotify", st.session_state["last_auth_url"])

if not who.get("authed"):
    if st.button("🔗 Conectar Spotify"):
        ab = execute_plan_blocking([{"server":"spotify","tool":"auth_begin","args":{}}])
        auth_url = None
        for r in ab:
            if r.get("server")=="spotify" and r.get("tool")=="auth_begin" and r.get("ok"):
                res = r.get("result") or {}

                # 1) structuredContent (si el server lo envía así)
                sc = (res.get("structuredContent") or {}).get("result")
                if isinstance(sc, dict) and sc.get("authorize_url"):
                    auth_url = sc["authorize_url"]
                else:
                    # 2) parsea el JSON del _text
                    raw = res.get("_text") or ""
                    try:
                        auth_url = json.loads(raw).get("authorize_url")
                    except Exception:
                        # 3) último recurso: regex que corta antes de comillas
                        m = re.search(r'https?://[^"\s]+', raw)
                        auth_url = m.group(0) if m else None

        if auth_url:
            st.session_state["last_auth_url"] = auth_url
            st.rerun()
else:
    user_label = who.get("display_name") or who.get("id") or "tu cuenta"
    st.success(f"Conectado como {user_label}")

# ===== Render historial =====
for m in st.session_state.messages:
    render_line(m["role"], m["content"], m["ts"])

    # Si el mensaje es del bot y tiene razonamiento → mostrar expander
    if m["role"] == "assistant" and m.get("thought"):
        with st.expander("Ver razonamiento", expanded=False):
            st.markdown(
                f"<div style='font-size:16px; opacity:0.85; margin-left:20px'>"
                f"{m['thought']}</div>",
                unsafe_allow_html=True
            )


# ===== Input =====
user_msg = st.chat_input("escribe tu mensaje…")
if user_msg:
    # 1) Mostrar mensaje de usuario
    now = datetime.now()
    st.session_state.messages.append({"role":"user","content":user_msg,"ts":now})
    render_line("user", user_msg, now)

    m = re.search(r"[A-Za-z]:\\[^\n]+mcp_repo_demo", user_msg)
    if m:
        st.session_state["last_repo_path"] = m.group(0)
    # Contenedores para razonamiento (expander) y respuesta (typewriter)
    reasoning_box = st.empty()
    typing_box = st.empty()

    # 2) Planner (Gemini): plan de acciones + thought
    lower_msg = (user_msg or "").strip().lower()
    if any(w in lower_msg for w in ("pública","publica","hacerla pública","hazla pública","hazla publica")):
        st.session_state.music_ctx["last_public"] = True
    elif any(w in lower_msg for w in ("privada","hacerla privada","hazla privada")):
        st.session_state.music_ctx["last_public"] = False

    try:
        plan = plan_llm(user_msg, st.session_state.messages)
    except Exception:
        plan = fallback_plan(user_msg)
    thought = plan.get("thought","")
    actions = plan.get("actions", [])
    actions = fix_plan(actions)
    reply_preview = plan.get("reply_preview","Procesando...")
    needs_auto_playlist = (
        not any(a.get("server")=="spotify" and a.get("tool") in ("create_playlist","create_playlist_with_tracks","build_playlist_from_profile") for a in actions)
        and len(st.session_state.music_ctx["basket_track_ids"]) >= 1
        and st.session_state.music_ctx.get("last_public") is not None
    )

    if needs_auto_playlist:
        name_guess = st.session_state.music_ctx.get("last_playlist_name") or "Mi Mix • Rock & Roll"
        actions = actions + [{
            "server": "spotify",
            "tool": "create_playlist_with_tracks",
            "args": {
                "name": name_guess,
                "track_ids": st.session_state.music_ctx["basket_track_ids"][:50],  # por si acaso
                "public": bool(st.session_state.music_ctx["last_public"]),
                "description": "Auto-generada desde el chat",
            }
        }]
    if thought:
        with reasoning_box.expander("Ver razonamiento", expanded=False):
            st.markdown(f"<div style='font-size:16px; opacity:0.85; margin-left:20px'>{thought}</div>", unsafe_allow_html=True)

    # 3) Ejecutar acciones MCP (si las hay) + loggear cada paso
    execution_results = []
    if actions:
        log_mcp({"event":"mcp_plan", "actions": actions})
        execution_results = execute_plan_blocking(actions)
        for r in execution_results or []:
            if r.get("server") == "spotify" and r.get("ok"):
                # 1) Si hubo búsquedas, acumula IDs únicas en la "cesta"
                if r.get("tool") == "search_track":
                    data = (r.get("result") or {}).get("parsed") or None
                    # si no hay "parsed", intenta json en _text
                    if not data:
                        raw = (r.get("result") or {}).get("_text")
                        try: data = json.loads(raw) if raw else None
                        except: data = None
                    if isinstance(data, dict) and data.get("id"):
                        tid = data["id"]
                        if tid not in st.session_state.music_ctx["basket_track_ids"]:
                            st.session_state.music_ctx["basket_track_ids"].append(tid)

                # 2) Si se creó una playlist (cualquier tool que lo haga), guarda último link
                if r.get("tool") in ("create_playlist", "build_playlist_from_profile", "create_playlist_with_tracks"):
                    data = (r.get("result") or {}).get("parsed") or None
                    if not data:
                        raw = (r.get("result") or {}).get("_text")
                        try: data = json.loads(raw) if raw else None
                        except: data = None
                    if isinstance(data, dict) and data.get("url"):
                        st.session_state.music_ctx["last_playlist_url"] = data["url"]
                        st.session_state.music_ctx["last_playlist_id"]  = data.get("playlist_id")
    else:
        # sin acciones: al menos log del plan
        log_mcp({"event":"mcp_plan_empty", "user_msg": user_msg})
    def _spotify_failed(results: list[dict]) -> bool:
        return any(r.get("server") == "spotify" and not r.get("ok") for r in results)

    def _used_spotify(results: list[dict]) -> bool:
        return any(r.get("server") == "spotify" for r in results)

    # Re-intento silencioso si falló Spotify (opcional)
    # Re-intento silencioso si falló Spotify (opcional)
    if actions and _spotify_failed(execution_results):
        # pequeño backoff
        time.sleep(0.5)
        retry_results = execute_plan_blocking(actions)

        # si el reintento trajo algo bueno, sustitúyelo
        if any(r.get("server") == "spotify" and r.get("ok") for r in retry_results):
            execution_results = retry_results

        # Si no apareció auth_begin aún, ofrece botón para iniciar OAuth
        has_auth_begin = any(
            r.get("server") == "spotify" and r.get("tool") == "auth_begin"
            for r in execution_results
        )
        if not has_auth_begin and st.button("Conectar Spotify"):
            ab_res = execute_plan_blocking([{"server": "spotify", "tool": "auth_begin", "args": {}}])
            execution_results += ab_res  # 👈 ahora solo se usa si existe

        # Buscar si ya tenemos un auth_begin exitoso para mostrar el link
        def _find_auth_begin(results):
            for r in results:
                if r.get("server") == "spotify" and r.get("tool") == "auth_begin" and r.get("ok"):
                    return r
            return None

        auth_res = _find_auth_begin(execution_results)
        if auth_res:
            res = auth_res.get("result") or {}
            url = (res.get("structuredContent") or {}).get("result", {}).get("authorize_url")
            if not url:
                raw = res.get("_text") or ""
                try:
                    url = json.loads(raw).get("authorize_url")
                except Exception:
                    m = re.search(r'https?://[^"\s]+', raw)
                    url = m.group(0) if m else None
            if url:
                st.session_state["last_auth_url"] = url







    # --- Selección del texto final ---
    summary = _playlist_summary_text(execution_results)
    if summary:
        final_text = summary
    else:
        final_text = finalize_llm(user_msg, execution_results) if actions else (ask_llm(user_msg) if ask_llm else reply_preview or "Listo.")

    # typewriter de la respuesta final
    out = ""
    for ch in final_text:
        out += ch
        typing_box.markdown(f'<div class="line"><span class="tag">[{HOST}]</span>{out}</div>', unsafe_allow_html=True)
        time.sleep(0.01)

    # ---- Quick replies (solo si tuvo intención musical) ----
    def _used_spotify(results: list[dict]) -> bool:
        return any(r.get("server") == "spotify" for r in results)

    is_music_turn = _used_spotify(execution_results) or any(
        kw in (user_msg or "").lower()
        for kw in ("canciones", "temas", "playlist", "rock", "pop", "lofi", "jazz", "nublado", "rainy", "calm")
    )
    if is_music_turn:
        st.write("")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Dame 5 más"):
            st.session_state.messages.append({"role":"user","content":"Dame 5 más del mismo estilo","ts":datetime.now()}); st.rerun()
        if c2.button("Más clásico"):
            st.session_state.messages.append({"role":"user","content":"Quiero más clásico de este estilo","ts":datetime.now()}); st.rerun()
        if c3.button("Más moderno"):
            st.session_state.messages.append({"role":"user","content":"Quiero más moderno de este estilo","ts":datetime.now()}); st.rerun()
        if c4.button("Solo instrumentales"):
            st.session_state.messages.append({"role":"user","content":"Dame solo instrumentales del mismo estilo","ts":datetime.now()}); st.rerun()

    # 5) Guardar en historial + log de intercambio LLM
    now2 = datetime.now()
    st.session_state.messages.append({"role":"assistant","content":final_text,"thought":thought,"ts":now2})
    log_mcp({
        "event": "llm_exchange",
        "user": user_msg,
        "assistant": final_text,
        "thought": thought,
        "actions": actions,
        "execution_results": execution_results
    })
# ← aquí sí termina el if user_msg:

