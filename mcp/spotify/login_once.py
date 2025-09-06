# login_once.py
import os, json
from dotenv import load_dotenv

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()  # 👈 carga SPOTIPY_* desde .env

SCOPES = [
    "playlist-modify-public","playlist-modify-private",
    "user-read-playback-state","user-modify-playback-state",
    "user-top-read","user-read-recently-played",
    "user-read-currently-playing","user-read-private",
]

oauth = SpotifyOAuth(
    scope=" ".join(SCOPES),
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    cache_path=".cache-bot",
    open_browser=True,
    show_dialog=True,  # fuerza pantalla de consentimiento => asegura refresh_token
)

sp = Spotify(auth_manager=oauth)
me = sp.me()
print("✅ Autenticado como:", me.get("display_name") or me.get("id"))

token_info = oauth.get_cached_token()
if not token_info:
    raise SystemExit("❌ No se encontró token en cache. Reintenta.")

rt = token_info.get("refresh_token")
if not rt:
    raise SystemExit("❌ No vino refresh_token. Borra .cache-bot y vuelve a ejecutar.")

print("\n=== COPIA ESTE VALOR A TU .env ===")
print("SPOTIFY_REFRESH_TOKEN=", rt)
print("==================================")
