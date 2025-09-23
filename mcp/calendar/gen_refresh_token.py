# gen_refresh_token.py — robusto: toma redirect_uri desde la URL pegada y muestra detalles de error
import json, os, webbrowser, urllib.parse, urllib.request, sys
FORCE_REDIR = "http://localhost"   # o prueba "http://127.0.0.1"

HERE = os.path.dirname(os.path.abspath(__file__))
CRED_PATH = os.path.join(HERE, "credentials.json")
TOKEN_PATH = os.path.join(HERE, "token.json")

def die(msg): print(f"[ERROR] {msg}"); sys.exit(1)

if not os.path.exists(CRED_PATH) or os.path.getsize(CRED_PATH)==0:
    die("credentials.json no existe o está vacío. Descárgalo de tu OAuth client en Google Cloud.")

try:
    with open(CRED_PATH, "r", encoding="utf-8") as f:
        creds = json.load(f)
except Exception as e:
    die(f"credentials.json inválido: {e}")

c = creds.get("installed") or creds.get("web")
if not c: die("credentials.json debe tener bloque 'installed' o 'web'.")

CLIENT_ID = c.get("client_id"); CLIENT_SECRET = c.get("client_secret")
REDIRS = c.get("redirect_uris") or []
DEFAULT_REDIR = REDIRS[0] if REDIRS else "http://127.0.0.1/"

if not CLIENT_ID or not CLIENT_SECRET:
    die("Faltan client_id/client_secret en credentials.json.")

SCOPES = "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/gmail.send"
REDIR = FORCE_REDIR

auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "response_type": "code",
    "redirect_uri": REDIR,  # se usará solo para abrir el consentimiento
    "scope": SCOPES,
    "access_type": "offline",
    "prompt": "consent",
})
print("[INFO] Abriendo navegador para consentimiento…")
print(f"[INFO] redirect_uri inicial: {DEFAULT_REDIR}")
webbrowser.open(auth_url)

raw = input("[INPUT] Pega aquí la URL COMPLETA que tiene ?code=... : ").strip()
if not raw: die("No ingresaste la URL.")

# Extrae code y el redirect_uri EXACTO usado (esquema+host[:puerto]+path)
if not raw.startswith("http"):
    die("Debes pegar la URL completa (empieza con http...).")

parsed = urllib.parse.urlparse(raw)
qs = urllib.parse.parse_qs(parsed.query)
if "authError" in qs: die("URL de error de Google (authError). Corrige el cliente OAuth y repite.")
code_vals = qs.get("code")
if not code_vals: die("No encontré parámetro 'code' en la URL pegada.")
code = code_vals[0]

# Reconstruye el redirect_uri EXACTO
REDIR_USED = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
print(f"[INFO] redirect_uri detectado en la URL: {REDIR_USED}")

data = urllib.parse.urlencode({
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code": code,
    "grant_type": "authorization_code",
    "redirect_uri": REDIR,  # usa el mismo de la URL pegada → evita mismatch
}).encode("utf-8")

req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=data,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    detail = e.read().decode("utf-8", "ignore")
    die(f"Intercambio falló ({e.code}). Detalle: {detail}")
except Exception as e:
    die(f"Intercambio falló: {e}")

refresh = out.get("refresh_token")
if not refresh:
    die("Google no devolvió refresh_token. Repite el consentimiento (usa incógnito) y asegúrate de 'access_type=offline' + 'prompt=consent'.")

with open(TOKEN_PATH, "w", encoding="utf-8") as f:
    json.dump({"refresh_token": refresh}, f, ensure_ascii=False, indent=2)

print(f"[OK] Escribí {TOKEN_PATH}")
