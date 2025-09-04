# app.py — Retro CRT + Show Reasoning + Typewriter
import streamlit as st
from datetime import datetime
from mcp_client import execute_plan_blocking, fix_plan
from publish import publish_repo
import time
from logger import log_mcp
from llm import plan_llm, finalize_llm 
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
    plan = plan_llm(user_msg, [m["content"] for m in st.session_state.messages if m["role"]=="user"])
    thought = plan.get("thought","")
    actions = plan.get("actions", [])
    actions = fix_plan(actions)
    reply_preview = plan.get("reply_preview","Procesando...")

    if thought:
        with reasoning_box.expander("Ver razonamiento", expanded=False):
            st.markdown(f"<div style='font-size:16px; opacity:0.85; margin-left:20px'>{thought}</div>", unsafe_allow_html=True)

    # 3) Ejecutar acciones MCP (si las hay) + loggear cada paso
    execution_results = []
    if actions:
        log_mcp({"event":"mcp_plan", "actions": actions})
        execution_results = execute_plan_blocking(actions)
        for r in execution_results:
            # log por acción
            log_mcp({
                "event": "mcp_tool_call",
                "server": r["server"],
                "tool": r["tool"],
                "args": r.get("args", {}),
                "ok": r.get("ok"),
                "result": r.get("result"),
                "error": r.get("error")
            })
    else:
        # sin acciones: al menos log del plan
        log_mcp({"event":"mcp_plan_empty", "user_msg": user_msg})

    # 4) Finalizer (Gemini): redacta respuesta final para el usuario
    final_text = finalize_llm(user_msg, execution_results) if actions else reply_preview or "Listo."
    # typewriter de la respuesta final
    out = ""
    for ch in final_text:
        out += ch
        typing_box.markdown(f'<div class="line"><span class="tag">[MoodST]</span>{out}</div>', unsafe_allow_html=True)
        time.sleep(0.01)

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
    