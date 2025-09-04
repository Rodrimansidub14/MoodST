# app.py — Retro CRT + Show Reasoning + Typewriter
import streamlit as st
from datetime import datetime
from mcp_client import run_demo_blocking
import time
from logger import log_mcp

try:
    from llm import ask_llm
except Exception:
    ask_llm = None

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

st.write("")  # separador
with st.container():
    col1, col2, col3 = st.columns([3,2,2], vertical_alignment="center")
    with col1:
        st.markdown("**Demo MCP**: crear repo + README + commit")
    with col2:
        base_dir = st.text_input("Carpeta base", value=r"C:\Users\rodri\Documents\Redes\MoodST_Demos")
    with col3:
        repo_name = st.text_input("Nombre repo", value="mcp_repo_demo")

run_demo = st.button("▶ Ejecutar demo MCP", use_container_width=True)
if run_demo:
    readme = (
        "# MoodST Repo Demo\n\n"
        "- Creado vía MCP Filesystem + MCP Git.\n"
        "- Commit inicial automatizado.\n"
    )
    try:
        repo_path, readme_path = run_demo_blocking(base_dir, repo_name, readme)
        st.success(f"OK: Repo en {repo_path}\nREADME: {readme_path}")

        # Log estructurado de la demo
        log_mcp({
            "event": "mcp_demo_repo",
            "base_dir": base_dir,
            "repo_name": repo_name,
            "readme_path": readme_path
        })
    except Exception as e:
        st.error(f"Fallo demo MCP: {e}")
        log_mcp({"event":"mcp_demo_repo_error","error":str(e)})

        
# ===== Estado =====
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role":"assistant","content":"Bienvenido. ¿Qué quieres hacer hoy?","thought":"","ts":datetime.now()},
    ]

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
    now = datetime.now()
    st.session_state.messages.append({"role":"user","content":user_msg,"ts":now})
    render_line("user", user_msg, now) 

    # preparar contenedores para razonamiento y respuesta
    reasoning_box = st.empty()
    typing_box = st.empty()

    if ask_llm:
        try:
            hist = [x["content"] for x in st.session_state.messages if x["role"]=="user"]
            result = ask_llm(user_msg, hist)  # {"reply":..., "thought":...}
            reply = result["reply"]
            thought = result["thought"]
        except Exception as e:
            reply, thought = f"(error backend) {e}", ""
    else:
        reply, thought = f"Recibido: {user_msg}", ""

    # mostrar razonamiento inmediatamente
    if thought:
        with reasoning_box.expander("Ver razonamiento", expanded=False):
            st.markdown(
                f"<div style='font-size:16px; opacity:0.85; margin-left:20px'>{thought}</div>",
                unsafe_allow_html=True
            )

    # efecto typewriter para la respuesta
    typewriter(typing_box, reply)

    # guardar en historial
    now2 = datetime.now()
    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "thought": thought, "ts": now2}
    )

    # 🔹 Loggear la interacción
    log_mcp({
        "event": "llm_exchange",
        "user": user_msg,
        "assistant": reply,
        "thought": thought
    })