# Servidor MCP (stdio) para Google Calendar / Gmail
import sys, json, re, time, datetime as dt
from typing import Dict, Any, Optional
from urllib import request, parse, error

import config  # ← tu config.py

# ===== respaldo por si mcp_minimal no está en PYTHONPATH
try:
    import mcp_minimal
except ImportError:
    import sys as _sys, json as _json, re as _re
    ENC = "utf-8"
    def _respond(obj: Dict[str, Any]) -> None:
        data = _json.dumps(obj).encode(ENC)
        hdr = f"Content-Length: {len(data)}\r\nContent-Type: application/json\r\n\r\n".encode(ENC)
        _sys.stdout.buffer.write(hdr); _sys.stdout.buffer.write(data); _sys.stdout.flush()
    def _read_request():
        headers = b""
        while b"\r\n\r\n" not in headers:
            ch = _sys.stdin.buffer.read(1)
            if not ch: return None
            headers += ch
        m = _re.search(rb"Content-Length:\s*(\d+)", headers, _re.I)
        if not m: return None
        body = _sys.stdin.buffer.read(int(m.group(1)))
        try: return _json.loads(body.decode(ENC, errors="ignore"))
        except Exception: return None
    def _serve(tools: Dict[str, Any], server_name="mcp-minimal", version="0.1.0"):
        while True:
            req = _read_request()
            if not req: return
            mid = req.get("id"); method = req.get("method")
            if method == "initialize":
                _respond({"jsonrpc":"2.0","id":mid,"result":{
                    "protocolVersion":"2025-06-18",
                    "serverInfo":{"name":server_name,"version":version},
                    "capabilities":{"tools":{"listChanged": False}}
                }})
            elif method == "tools/list":
                _respond({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":k,"description":(v.__doc__ or "")} for k,v in tools.items()]}})
            elif method == "tools/call":
                p = req.get("params",{}); name = p.get("name"); args = p.get("arguments") or {}
                if name not in tools:
                    _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":f"Herramienta no encontrada: {name}"}})
                else:
                    try:
                        out = tools[name](**args) if isinstance(args, dict) else tools[name]()
                        _respond({"jsonrpc":"2.0","id":mid,"result":{"content": out}})
                    except Exception as e:
                        _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32000,"message":str(e)}})
            else:
                _respond({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":f"Método desconocido: {method}"}})
    class mcp_minimal: serve = staticmethod(_serve)

ENC = "utf-8"

# ===== HTTP helpers
def http_get(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode(ENC))
    except error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {e.read().decode(ENC, errors='ignore')}")

def http_post(url: str, data: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    body = json.dumps(data).encode(ENC)
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode(ENC))
    except error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {e.read().decode(ENC, errors='ignore')}")

# ===== OAuth refresh
def google_access_token() -> str:
    """Intercambia refresh_token por access_token."""
    if not (config.CLIENT_ID and config.CLIENT_SECRET and config.REFRESH_TOKEN):
        raise RuntimeError("Faltan CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN en config/archivos JSON.")
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": config.CLIENT_ID,
        "client_secret": config.CLIENT_SECRET,
        "refresh_token": config.REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    body = parse.urlencode(data).encode("utf-8")
    req = request.Request(token_url, data=body, headers={"Content-Type":"application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
            return out["access_token"]
    except error.HTTPError as e:
        raise RuntimeError(f"OAuth refresh error: {e.read().decode('utf-8','ignore')}")

# ===== parse fechas
def parse_when(when_text: str) -> dt.datetime:
    """'hoy HH:MM' | 'mañana HH:MM' | 'YYYY-MM-DD HH:MM' → datetime local naive"""
    t = (when_text or "").strip()
    now = dt.datetime.now()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", t)
    if m:
        y, mo, d, hh, mm = map(int, m.groups())
        return dt.datetime(y, mo, d, hh, mm, 0)
    m = re.search(r"(\d{1,2}):(\d{2})", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        base = now.date()
        if "mañana" in t.lower() or "manana" in t.lower():
            base = base + dt.timedelta(days=1)
        return dt.datetime.combine(base, dt.time(hh, mm, 0))
    return dt.datetime.combine(now.date(), dt.time(15,0,0))

def parse_day_ref(s: str) -> dt.date:
    s = (s or "").strip().lower()
    today = dt.datetime.now().date()
    if s in ("", "hoy"): return today
    if s in ("mañana","manana"): return today + dt.timedelta(days=1)
    if s in ("pasado mañana","pasado manana"): return today + dt.timedelta(days=2)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m: return dt.date(*map(int, m.groups()))
    return today

# ===== TZ helpers
def tz_offset_minutes_for(tz_name: str) -> int:
    known = {"America/Guatemala": -6*60}
    if tz_name in known: return known[tz_name]
    import time as _time
    if _time.daylight and _time.localtime().tm_isdst: return int(-_time.altzone//60)
    return int(-_time.timezone//60)

def to_rfc3339_utc(dt_local: dt.datetime, tz_name: str) -> str:
    off = tz_offset_minutes_for(tz_name)
    dt_utc = dt_local - dt.timedelta(minutes=off)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

# ===== Google Calendar / Gmail ops
def calendar_create_event(title: str, when: str, duration_minutes: int = 60, meet_link: bool=False) -> Dict[str, Any]:
    access = google_access_token()
    tz = getattr(config, "TIMEZONE", "America/Guatemala") or "America/Guatemala"
    start_dt = parse_when(when); end_dt = start_dt + dt.timedelta(minutes=int(duration_minutes or 60))
    payload = {
        "summary": title or "Evento",
        "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        "end":   {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
    }
    query = ""
    if meet_link:
        payload["conferenceData"] = {"createRequest": {"requestId": f"mcp-{int(time.time())}"}}
        query = "?conferenceDataVersion=1"
    cal_id = getattr(config, "CALENDAR_ID", "primary") or "primary"
    url = f"https://www.googleapis.com/calendar/v3/calendars/{parse.quote(cal_id)}/events{query}"
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
    out = http_post(url, payload, headers)
    return {"id": out.get("id"), "htmlLink": out.get("htmlLink"), "hangoutLink": out.get("hangoutLink")}

def calendar_list_day(when: str = "hoy") -> Dict[str, Any]:
    access = google_access_token()
    tz = getattr(config, "TIMEZONE", "America/Guatemala") or "America/Guatemala"
    target = parse_day_ref(when)
    start_dt_local = dt.datetime.combine(target, dt.time(0,0,0))
    end_dt_local   = dt.datetime.combine(target, dt.time(23,59,59))
    tmin = to_rfc3339_utc(start_dt_local, tz); tmax = to_rfc3339_utc(end_dt_local, tz)
    cal_id = getattr(config, "CALENDAR_ID", "primary") or "primary"
    url = ("https://www.googleapis.com/calendar/v3/calendars/"
           f"{parse.quote(cal_id)}/events?timeMin={parse.quote(tmin)}&timeMax={parse.quote(tmax)}"
           f"&singleEvents=true&orderBy=startTime")
    out = http_get(url, headers={"Authorization": f"Bearer {access}"})
    events = []
    for it in out.get("items", []):
        start = it.get("start",{}).get("dateTime") or it.get("start",{}).get("date")
        end   = it.get("end",{}).get("dateTime") or it.get("end",{}).get("date")
        events.append({"summary": it.get("summary","(sin título)"), "start": start, "end": end, "hangoutLink": it.get("hangoutLink")})
    return {"events": events, "count": len(events), "day": str(target)}

def gmail_send_summary(text: str) -> Dict[str, Any]:
    access = google_access_token()
    from_addr = getattr(config, "SENDER_EMAIL", "") or ""
    to_addr   = getattr(config, "USER_EMAIL", "") or from_addr
    subject   = "Resumen del día"
    msg = f"From: {from_addr}\r\nTo: {to_addr}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{text}".encode("utf-8")
    import base64
    raw = base64.urlsafe_b64encode(msg).decode("utf-8").rstrip("=")
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
    payload = {"raw": raw}
    out = http_post(url, payload, headers)
    return {"id": out.get("id", "")}

# ===== Tools MCP
def get_daily_agenda(when: Optional[str] = None) -> Dict[str, str]:
    """Devuelve la agenda de un día. Arg opcional: when='hoy'|'mañana'|'YYYY-MM-DD'."""
    w = (when or "hoy").strip()
    agenda = calendar_list_day(w)
    if not agenda["count"]:
        return {"text": f"Agenda de {w}: no tienes eventos."}
    lines = [f"Agenda de {w}:"]
    for ev in agenda["events"]:
        lines.append(f"- {ev['start']} → {ev['end']}: {ev['summary']}")
    return {"text": "\n".join(lines)}

def get_agenda(when: Optional[str] = None) -> Dict[str, str]:
    """Alias de get_daily_agenda."""
    return get_daily_agenda(when)

def create_calendar_event(title: Optional[str] = None, when: Optional[str] = None,
                          duration_minutes: Optional[int] = None, meet_link: Optional[bool] = None) -> Dict[str, str]:
    """Crea un evento. Args: title, when ('hoy 15:00'|'mañana 10:30'|'YYYY-MM-DD HH:MM'), duration_minutes, meet_link?"""
    t = title or "Evento"
    w = when or "hoy 15:00"
    d = int(duration_minutes or 60)
    m = bool(meet_link)
    out = calendar_create_event(t, w, d, m)
    txt = f"¡Listo! Creé '{t}'. Enlace: {out.get('htmlLink') or '(N/A)'}"
    if out.get("hangoutLink"): txt += f"\nMeet: {out['hangoutLink']}"
    return {"text": txt}

def send_daily_summary() -> Dict[str, str]:
    """Envía un email con la agenda de hoy."""
    agenda = calendar_list_day("hoy")
    text = "Resumen: Hoy no hay eventos programados." if not agenda["count"] else \
           "Resumen del día:\n" + "\n".join([f"- {ev['start']} → {ev['end']}: {ev['summary']}" for ev in agenda["events"]])
    status = gmail_send_summary(text)
    return {"text": f"Enviado (id: {status.get('id','')})"}

if __name__ == "__main__":
    tools = {
        "get_daily_agenda": get_daily_agenda,
        "get_agenda": get_agenda,
        "create_calendar_event": create_calendar_event,
        "send_daily_summary": send_daily_summary,
    }
    mcp_minimal.serve(tools, server_name=getattr(config,"SERVER_NAME","google-calendar-mcp"),
                      version=getattr(config,"SERVER_VERSION","0.2.0"))
