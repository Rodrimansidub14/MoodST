# config.py — ajustes locales (NO subir a git)
import os, json

# === 1) Google OAuth client (descargado de GCP) ===
CLIENT_ID = ""
CLIENT_SECRET = ""
if os.path.exists("credentials.json"):
    with open("credentials.json","r",encoding="utf-8") as f:
        creds = json.load(f)
        block = creds.get("installed") or creds.get("web") or {}
        CLIENT_SECRET = block.get("client_secret","")

# === 2) Refresh token (generado con tu script) ===
REFRESH_TOKEN = ""
if os.path.exists("token.json"):
    with open("token.json","r",encoding="utf-8") as f:
        REFRESH_TOKEN = (json.load(f) or {}).get("refresh_token","")

# === 3) Emails (ajusta a tu cuenta) ===
USER_EMAIL   = os.getenv("USER_EMAIL",   "tu_correo@gmail.com")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "tu_correo@gmail.com")

# === 4) Scopes requeridos ===
SCOPES = (
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/gmail.send"
)

# === 5) Otros ajustes ===
TIMEZONE       = "America/Guatemala"
LOG_FILE       = "mcp_io.log"
SERVER_NAME    = "google-calendar-mcp"
SERVER_VERSION = "0.2.0"

# === 6) (opcional) registro para tu host
MCP_SERVERS = {
    "calendar": [os.sys.executable, "google_calendar_mcp_server.py"],
}
MCP_DEFAULT = "calendar"
