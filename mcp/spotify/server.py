# server.py 
import os, logging, re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import random
import time

from tenacity import retry, wait_exponential, stop_after_attempt
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
from spotipy.exceptions import SpotifyException

from mcp.server.fastmcp import FastMCP
from models import Track, Mood, ExplainContext, PlaylistRef, EnsureDeviceResult, AddedResult, PlayResult

logging.basicConfig(level=logging.INFO)
load_dotenv()
load_dotenv(Path(__file__).with_name(".env"))

redir = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback").replace("localhost", "127.0.0.1")
os.environ["SPOTIPY_CLIENT_ID"] = os.getenv("SPOTIFY_CLIENT_ID", "")
os.environ["SPOTIPY_CLIENT_SECRET"] = os.getenv("SPOTIFY_CLIENT_SECRET", "")
os.environ["SPOTIPY_REDIRECT_URI"] = redir

SCOPES = [
    "playlist-modify-public", "playlist-modify-private", "user-read-playback-state",
    "user-modify-playback-state", "user-top-read", "user-read-recently-played",
    "user-read-currently-playing", "user-read-private"
]
SCOPE_STR = " ".join(SCOPES)
BOT_MODE = os.getenv("SPOTIFY_BOT_MODE", "0") == "1"

@dataclass
class SpotifyClients:
    app: spotipy.Spotify
    user: Optional[spotipy.Spotify]

def _build_app_client():
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    csec = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("Faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
    auth = SpotifyClientCredentials(client_id=cid, client_secret=csec)
    return spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=3)



def _build_bot_user_client():
    rt = os.getenv("SPOTIFY_REFRESH_TOKEN", "")
    if not rt:
        raise RuntimeError("Falta SPOTIFY_REFRESH_TOKEN en .env para BOT_MODE.")
    oauth = SpotifyOAuth(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
        scope=SCOPE_STR,
        open_browser=False,
        cache_path=f".cache-{os.getenv('SPOTIFY_USERNAME','bot')}",
        requests_timeout=10,
    )
    token_info = oauth.refresh_access_token(rt)
    try: oauth.cache_handler.save_token_to_cache(token_info)
    except: pass
    return spotipy.Spotify(auth_manager=oauth, requests_timeout=10, retries=3)

class SpotifyService:
    def __init__(self):
        self.market = os.environ.get("SPOTIFY_MARKET", "US")
        self.clients = SpotifyClients(app=_build_app_client(), user=None)
        if BOT_MODE:
            self.clients.user = _build_bot_user_client()

    def set_user_auth_manager(self, oauth: SpotifyOAuth):
        self.clients.user = spotipy.Spotify(auth_manager=oauth, requests_timeout=10, retries=3)

    def _require_user(self):
        if not self.clients.user:
            raise PermissionError("Acción requiere OAuth de usuario configurado y login previo.")
        return self.clients.user

    def _sp_tracks(self, ids: List[str]):
        sp = self.clients.app
        try: return sp.tracks(tracks=ids, market=self.market)
        except TypeError: return sp.tracks(tracks=ids)

    @staticmethod
    def _coerce_seed_list(seeds):
        if not seeds: return []
        if isinstance(seeds, str):
            toks = re.split(r"[,\s]+", seeds.strip())
        else:
            flat = []
            for s in seeds:
                if not s: continue
                if isinstance(s, str): flat += re.split(r"[,\s]+", s.strip())
                else: flat.append(str(s))
            toks = [t for t in flat if t]
        out, seen = [], set()
        for t in toks:
            tid = t.split(":")[-1]
            if len(tid) == 22 and tid not in seen:
                seen.add(tid); out.append(tid)
        return out

    def search_tracks(self, query: str, limit: int = 10, market: Optional[str] = None):
        sp = self.clients.app
        res = sp.search(q=query, type="track", limit=limit, market=market or self.market)
        items = res.get("tracks", {}).get("items", [])
        return [{"id": t["id"], "name": t["name"], "artists": [{"name": a["name"]} for a in t.get("artists", [])],
                 "uri": t["uri"], "preview_url": t.get("preview_url")} for t in items]

    def audio_features_map(self, track_ids):
        if not track_ids: return {}
        ids = [str(t).split(":")[-1] for t in track_ids if t]
        CHUNK = 50; out = {}
        for i in range(0, len(ids), CHUNK):
            batch = ids[i:i+CHUNK]
            try: feats = self.clients.app.audio_features(tracks=batch) or []
            except SpotifyException as e:
                logging.warning("audio_features failed (%s) len=%d", getattr(e, "http_status", e), len(batch))
                return {}
            except Exception as e:
                logging.warning("audio_features error: %s", e); return {}
            for f in feats:
                if not f or not f.get("id"): continue
                out[f["id"]] = {k: f.get(k) for k in ("danceability", "energy", "valence", "tempo", "acousticness", "instrumentalness")}
        return out

    def _rank_by_targets(self, candidates, targets):
        if not candidates: return []
        ids = [t.get("id") for t in candidates if t and t.get("id")]
        fm = self.audio_features_map(ids)
        if not fm: return candidates
        keys = ("energy", "valence", "danceability", "tempo")
        want = {k: targets.get(k) for k in keys if targets.get(k) is not None}
        def norm_tempo(x): return max(0.0, min(1.0, (float(x) - 60.0) / 180.0))
        def dist(t):
            f = fm.get(t["id"], {}); d = n = 0
            for k, v in want.items():
                x = f.get(k)
                if x is None: continue
                if k == "tempo": x = norm_tempo(x); v = norm_tempo(v)
                d += (float(x) - float(v))**2; n += 1
            return d / n if n else 999.0
        return sorted(candidates, key=dist)

    def recommendations(self, seed_tracks, targets: Dict[str, float], limit: int = 20):
        sp = self.clients.app
        toks = [p.strip() for p in re.split(r"[,\s]+", seed_tracks) if p.strip()] if isinstance(seed_tracks, str) else \
            [p.strip() for s in (seed_tracks or []) for p in re.split(r"[,\s]+", str(s)) if p.strip()]
        ids = [t.split(":")[-1] for t in toks if len(t.split(":")[-1]) == 22][:5]
        valid_ids, artist_ids = [], []
        if ids:
            meta = self._sp_tracks(ids)
            for tr in (meta or {}).get("tracks", []) or []:
                if not tr: continue
                mkts = tr.get("available_markets") or []
                if not mkts or self.market in mkts: valid_ids.append(tr["id"])
                for a in (tr.get("artists") or [])[:1]:
                    if a.get("id"): artist_ids.append(a["id"])
        artist_ids = list(dict.fromkeys(artist_ids))[:5]
        tgt = {f"target_{k}": float(v) for k, v in targets.items() if v is not None and k in ("energy","valence","danceability","tempo")}
        def _official(**params):
            try: return (sp.recommendations(**params) or {}).get("tracks", []) or []
            except SpotifyException as e:
                logging.warning("Recommendations failed (%s) params=%s", getattr(e,"http_status",e), params); return []
            except Exception as e:
                logging.warning("Recommendations error (%s) params=%s", e, params); return []
        for k in (5,3,2,1):
            if len(valid_ids) >= k:
                tr = _official(seed_tracks=valid_ids[:k], limit=int(limit), **tgt)
                if tr:
                    return [{"id": t["id"], "name": t["name"], "artists": [{"name": a["name"]} for a in t.get("artists", [])],
                             "uri": t["uri"], "preview_url": t.get("preview_url")} for t in tr]
        candidates = []
        if artist_ids:
            for aid in artist_ids:
                try: rel = sp.artist_related_artists(aid).get("artists", [])[:5]
                except: rel = []
                base = [aid] + [a.get("id") for a in rel if a and a.get("id")]
                for a in base[:5]:
                    try: candidates.extend(sp.artist_top_tracks(a, market=self.market).get("tracks", [])[:5])
                    except: pass
        if not candidates:
            try: candidates.extend(sp.search(q="lofi rain calm piano", type="track", limit=50, market=self.market).get("tracks", {}).get("items", []))
            except: pass
        seen = set(); clean = []
        for t in candidates:
            if not t or not t.get("id"): continue
            if t["id"] in seen: continue
            mkts = t.get("available_markets") or []
            if mkts and self.market not in mkts: continue
            seen.add(t["id"]); clean.append(t)
        ranked = self._rank_by_targets(clean, targets)[:int(limit)]
        return [{"id": t["id"], "name": t["name"], "artists": [{"name": a["name"]} for a in t.get("artists", [])],
                 "uri": t["uri"], "preview_url": t.get("preview_url")} for t in ranked]

    def create_playlist(self, name: str, description: str = "", public: bool = False):
        sp = self._require_user()
        me = sp.me()
        pl = sp.user_playlist_create(me["id"], name=name, public=public, description=description)
        return {"playlist_id": pl["id"], "url": pl["external_urls"]["spotify"]}

    def add_to_playlist(self, playlist_id: str, track_uris: List[str]):
        sp = self._require_user()
        if not track_uris: return 0
        chunks = [track_uris[i:i+100] for i in range(0, len(track_uris), 100)]
        added = 0
        for ch in chunks:
            sp.playlist_add_items(playlist_id, ch)
            added += len(ch)
        return added

    def list_devices(self):
        sp = self._require_user()
        return (sp.devices() or {}).get("devices", [])

    @retry(wait=wait_exponential(multiplier=0.5, max=4), stop=stop_after_attempt(3))
    def start_or_transfer(self, playlist_uri: str, device_id: Optional[str]):
        sp = self._require_user()
        try:
            sp.start_playback(device_id=device_id, context_uri=playlist_uri)
            return "playing"
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 403: return "not_premium"
            raise

    def user_seed_track_ids(self, want: int = 5):
        sp = self._require_user()
        seen, seeds = set(), []
        try:
            top = sp.current_user_top_tracks(limit=min(20, max(5, want*3)), time_range="medium_term")
            for it in top.get("items", []):
                tid = it.get("id")
                if tid and tid not in seen:
                    seeds.append(tid); seen.add(tid)
                if len(seeds) >= want: return seeds
        except: pass
        try:
            rp = sp.current_user_recently_played(limit=50)
            for it in rp.get("items", []):
                tr = it.get("track", {}); tid = tr.get("id")
                if tid and tid not in seen:
                    seeds.append(tid); seen.add(tid)
                if len(seeds) >= want: return seeds
        except: pass
        try:
            arts = sp.current_user_top_artists(limit=5, time_range="medium_term")
            for a in arts.get("items", []):
                at = sp.artist_top_tracks(a["id"], market=self.market)
                for tr in at.get("tracks", []):
                    tid = tr.get("id")
                    if tid and tid not in seen:
                        seeds.append(tid); seen.add(tid)
                    if len(seeds) >= want: return seeds
        except: pass
        return seeds

mcp = FastMCP("spotify")
_svc: Optional[SpotifyService] = None
def svc():
    global _svc
    if _svc is None: _svc = SpotifyService()
    return _svc


_pending_oauth: Optional[SpotifyOAuth] = None

def _new_oauth():
    return SpotifyOAuth(
        client_id=os.environ["SPOTIPY_CLIENT_ID"],
        client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        redirect_uri=os.environ["SPOTIPY_REDIRECT_URI"],
        scope=SCOPE_STR,
        open_browser=False,
        cache_path=f".cache-{os.getenv('SPOTIFY_USERNAME','me')}",
        requests_timeout=10,
    )

@mcp.tool()
def ping() -> dict:
    """Healthcheck simple."""
    return {"ok": True}

@mcp.tool()
def server_info() -> dict:
    """Info básica del servidor para debug / telemetría ligera."""
    return {
        "name": "spotify",
        "version": os.environ.get("APP_VERSION", "0.1.0"),
        "bot_mode": BOT_MODE,
        "market": svc().market,
        "scopes": SCOPES,
    }

@mcp.tool()
def whoami():
    u = svc().clients.user
    if not u: return {"authed": False}
    try:
        me = u.me()
        return {"authed": True, "id": me.get("id"), "display_name": me.get("display_name")}
    except: return {"authed": False}

@mcp.tool()
def auth_begin() -> dict:
    if BOT_MODE:
        return {
            "bot_mode": True,
            "authorize_url": None,
            "message": "OAuth de usuario deshabilitado: el servidor usa cuenta bot."
        }
    global _pending_oauth
    _pending_oauth = _new_oauth()
    url = _pending_oauth.get_authorize_url()
    return {"authorize_url": url}

@mcp.tool()
def auth_complete(redirect_url: Optional[str] = None, code: Optional[str] = None) -> dict:
    if BOT_MODE:
        return {"ok": False, "bot_mode": True, "error": "auth_complete deshabilitado en BOT_MODE."}
    global _pending_oauth
    oauth = _pending_oauth or _new_oauth()
    if not code:
        if not redirect_url:
            return {"ok": False, "error": "Falta 'code' o 'redirect_url'."}
        code = oauth.parse_response_code(redirect_url)
    if not code:
        return {"ok": False, "error": "No encontré 'code'."}
    token_info = oauth.get_access_token(code=code)
    svc().clients.user = spotipy.Spotify(auth_manager=oauth, requests_timeout=10, retries=3)
    _pending_oauth = None
    me = svc().clients.user.me()
    return {"ok": True, "id": me.get("id"), "display_name": me.get("display_name")}

@mcp.tool()
def search_track(query: str, market: Optional[str] = None, limit: int = 10):
    items = svc().search_tracks(query=query, limit=limit, market=market)
    return [Track(**t) for t in items]

KEY_TO_MOOD = {
    "calm":  {"valence": 0.6, "energy": 0.2},
    "focus": {"valence": 0.55, "energy": 0.25},
    "happy": {"valence": 0.85, "energy": 0.6},
    "sad":   {"valence": 0.2, "energy": 0.15},
    "party": {"valence": 0.8, "energy": 0.85, "danceability": 0.8},
    "night": {"valence": 0.55, "energy": 0.2},
    "piano": {"valence": 0.6, "energy": 0.25, "acousticness": 0.7, "instrumentalness": 0.5},
}
class MoodModel(Mood): pass

KEY_TO_MOOD = {
    "calm":        {"valence": 0.6, "energy": 0.2},
    "focus":       {"valence": 0.55, "energy": 0.25},
    "happy":       {"valence": 0.85, "energy": 0.6},
    "sad":         {"valence": 0.2, "energy": 0.15},
    "party":       {"valence": 0.8, "energy": 0.85, "danceability": 0.8},
    "night":       {"valence": 0.55, "energy": 0.2},
    "piano":       {"valence": 0.6, "energy": 0.25, "acousticness": 0.7, "instrumentalness": 0.5},

    "rock and roll": {"valence": 0.75, "energy": 0.80},
    "rock":          {"valence": 0.65, "energy": 0.75},
    "metal":         {"valence": 0.45, "energy": 0.90},
    "indie":         {"valence": 0.60, "energy": 0.55},
    "jazz":          {"valence": 0.55, "energy": 0.35},
    "lofi":          {"valence": 0.55, "energy": 0.20},
    "reggaeton":     {"valence": 0.75, "energy": 0.80, "danceability": 0.85},
    "trap":          {"valence": 0.50, "energy": 0.70, "danceability": 0.80},
    "pop":           {"valence": 0.80, "energy": 0.65, "danceability": 0.75},

    "electronic":    {"valence": 0.70, "energy": 0.85, "danceability": 0.80},
    "edm":           {"valence": 0.75, "energy": 0.90, "danceability": 0.85},
    "house":         {"valence": 0.70, "energy": 0.80, "danceability": 0.90},
    "techno":        {"valence": 0.60, "energy": 0.95, "danceability": 0.85},
    "dubstep":       {"valence": 0.55, "energy": 0.95, "danceability": 0.80},

    "classical":     {"valence": 0.65, "energy": 0.25, "acousticness": 0.9, "instrumentalness": 0.95},
    "orchestral":    {"valence": 0.60, "energy": 0.30, "acousticness": 0.85, "instrumentalness": 0.90},
    "ambient":       {"valence": 0.50, "energy": 0.10, "acousticness": 0.95, "instrumentalness": 0.90},

    "blues":         {"valence": 0.40, "energy": 0.35},
    "soul":          {"valence": 0.70, "energy": 0.50},
    "funk":          {"valence": 0.80, "energy": 0.70, "danceability": 0.85},
    "r&b":           {"valence": 0.75, "energy": 0.60, "danceability": 0.80},

    "hip hop":       {"valence": 0.70, "energy": 0.75, "danceability": 0.85},
    "rap":           {"valence": 0.65, "energy": 0.80, "danceability": 0.80},

    "folk":          {"valence": 0.60, "energy": 0.30, "acousticness": 0.85},
    "country":       {"valence": 0.75, "energy": 0.55, "acousticness": 0.70},

    "latin":         {"valence": 0.80, "energy": 0.75, "danceability": 0.85},
    "salsa":         {"valence": 0.85, "energy": 0.80, "danceability": 0.90},
    "cumbia":        {"valence": 0.80, "energy": 0.70, "danceability": 0.85},
    "tango":         {"valence": 0.60, "energy": 0.50, "danceability": 0.70},

    "reggae":        {"valence": 0.75, "energy": 0.50, "danceability": 0.80},
    "ska":           {"valence": 0.80, "energy": 0.70, "danceability": 0.85},

    "punk":          {"valence": 0.60, "energy": 0.95},
    "grunge":        {"valence": 0.50, "energy": 0.80},

    "disco":         {"valence": 0.85, "energy": 0.80, "danceability": 0.90},
    "synthwave":     {"valence": 0.75, "energy": 0.70, "danceability": 0.80},

    "chill":         {"valence": 0.65, "energy": 0.25},
    "romantic":      {"valence": 0.80, "energy": 0.40},
    "epic":          {"valence": 0.70, "energy": 0.90},
    "dark":          {"valence": 0.30, "energy": 0.60},
    "uplifting":     {"valence": 0.90, "energy": 0.80},
    "melancholic":   {"valence": 0.35, "energy": 0.25},
}

GENRE_QUERY_HINTS = {
    "rock and roll": [
        "classic rock and roll 50s 60s", "rock and roll legends", "rockabilly classics"
    ],
    "rock": [
        "classic rock anthems", "alternative rock classics", "90s rock hits", "modern rock bangers"
    ],
    "metal": [
        "heavy metal classics", "thrash metal", "power metal anthems"
    ],
    "indie": [
        "indie rock classics", "indie anthems", "bedroom indie"
    ],
    "jazz": [
        "cool jazz classics", "hard bop classics", "modern jazz"
    ],
    "lofi": [
        "lofi hip hop beats", "study lofi"
    ],
    "reggaeton": [
        "reggaeton hits", "old school reggaeton classics", "perreo intenso"
    ],
    "trap": [
        "latin trap hits", "trap bangers"
    ],
    "pop": [
        "pop anthems", "80s pop classics", "modern pop hits"
    ],
    "electronic": [
        "electronic dance hits", "electronic chill", "electronic classics"
    ],
    "edm": [
        "edm festival anthems", "edm hits", "edm classics"
    ],
    "house": [
        "house music classics", "deep house", "progressive house"
    ],
    "techno": [
        "techno bangers", "classic techno", "minimal techno"
    ],
    "dubstep": [
        "dubstep essentials", "classic dubstep", "modern dubstep"
    ],
    "classical": [
        "classical masterpieces", "romantic era classics", "baroque classics"
    ],
    "orchestral": [
        "orchestral film scores", "epic orchestral", "orchestral classics"
    ],
    "ambient": [
        "ambient chill", "ambient soundscapes", "ambient classics"
    ],
    "blues": [
        "blues legends", "classic blues", "modern blues"
    ],
    "soul": [
        "soul classics", "neo soul", "motown hits"
    ],
    "funk": [
        "funk classics", "modern funk", "funk legends"
    ],
    "r&b": [
        "r&b classics", "modern r&b", "90s r&b hits"
    ],
    "hip hop": [
        "hip hop classics", "modern hip hop", "old school hip hop"
    ],
    "rap": [
        "rap anthems", "classic rap", "modern rap hits"
    ],
    "folk": [
        "folk classics", "modern folk", "indie folk"
    ],
    "country": [
        "country classics", "modern country hits", "country legends"
    ],
    "latin": [
        "latin hits", "latin pop classics", "latin party"
    ],
    "salsa": [
        "salsa classics", "salsa party", "modern salsa"
    ],
    "cumbia": [
        "cumbia classics", "modern cumbia", "cumbia hits"
    ],
    "tango": [
        "tango classics", "modern tango", "argentinian tango"
    ],
    "reggae": [
        "reggae classics", "roots reggae", "modern reggae"
    ],
    "ska": [
        "ska classics", "modern ska", "ska punk"
    ],
    "punk": [
        "punk rock classics", "modern punk", "pop punk hits"
    ],
    "grunge": [
        "grunge classics", "90s grunge", "modern grunge"
    ],
    "disco": [
        "disco classics", "modern disco", "disco party"
    ],
    "synthwave": [
        "synthwave classics", "modern synthwave", "retro synthwave"
    ],
    "chill": [
        "chill hits", "chillout lounge", "chill vibes"
    ],
    "romantic": [
        "romantic ballads", "love songs", "romantic classics"
    ],
    "epic": [
        "epic soundtracks", "epic orchestral", "epic movie themes"
    ],
    "dark": [
        "dark ambient", "dark electronic", "dark wave"
    ],
    "uplifting": [
        "uplifting anthems", "feel good hits", "uplifting pop"
    ],
    "melancholic": [
        "melancholic indie", "sad songs", "melancholic classics"
    ],
}

class MoodModel(Mood):
    query_hints: list[str] | None = None

def infer_mood(prompt: str) -> MoodModel:
    p = (prompt or "").lower()
    for g in GENRE_QUERY_HINTS.keys():
        if g in p:
            base = KEY_TO_MOOD.get(g, {"valence": 0.6, "energy": 0.4})
            return MoodModel(
                mood=g,
                valence=base.get("valence", 0.6),
                energy=base.get("energy", 0.4),
                tags=[g],
                query_hints=GENRE_QUERY_HINTS[g]
            )
    tags = [k for k in KEY_TO_MOOD if k in p]
    agg: Dict[str, float] = {}
    for k in tags:
        for kk, vv in KEY_TO_MOOD[k].items():
            agg[kk] = (agg.get(kk, 0.0) + vv) / 2 if kk in agg else vv
    if not agg:
        if any(w in p for w in ("lluv", "rain", "rainy")):
            agg = {"valence": 0.45, "energy": 0.30}
            tags = ["rainy"]
        else:
            agg = {"valence": 0.6, "energy": 0.4}
            tags = ["neutral"]
    return MoodModel(mood=tags[0], valence=agg.get("valence", 0.6), energy=agg.get("energy", 0.4), tags=tags, query_hints=None)

@mcp.tool()
def analyze_mood(prompt: str):
    return infer_mood(prompt)

@mcp.tool()

@mcp.tool()
def get_recommendations(seed_tracks: Optional[List[str]] = None, mood: Optional[str] = None,
                        energy: Optional[float] = None, valence: Optional[float] = None,
                        danceability: Optional[float] = None, tempo: Optional[float] = None,
                        limit: int = 20):
    m = infer_mood(mood or "")
    targets = {k: v for k, v in dict(energy=energy, valence=valence, danceability=danceability, tempo=tempo).items() if v is not None}
    if m and not targets:
        targets = {"energy": m.energy, "valence": m.valence}

    tracks = svc().recommendations(seed_tracks or [], targets, limit=limit)
    seen_art = set()
    uniq = []
    for t in tracks:
        main = (t.get("artists") or [{}])[0].get("name", "").lower()
        if main in seen_art:
            continue
        seen_art.add(main)
        uniq.append(t)
    uniq = uniq[: int(limit)]

    rnd = random.Random(int(time.time() // 3600))
    rnd.shuffle(uniq)

    if len(uniq) < int(limit):
        hints = (m.query_hints or []) if m else []
        if hints:
            need = int(limit) - len(uniq)
            q = rnd.choice(hints)
            more = svc().search_tracks(query=q, limit=min(50, need * 5))
            for t in more:
                main = (t.get("artists") or [{}])[0].get("name", "").lower()
                if main in seen_art:
                    continue
                seen_art.add(main)
                uniq.append(t)
                if len(uniq) >= int(limit):
                    break

    return [Track(**t) for t in uniq[: int(limit)]]

@mcp.tool()
def create_playlist(self, name: str, description: str = "", public: bool = False):
    sp = self._require_user()
    if BOT_MODE:
        public = True  
    me = sp.me()
    pl = sp.user_playlist_create(me["id"], name=name, public=public, description=description)
    return {"playlist_id": pl["id"], "url": pl["external_urls"]["spotify"]}



@mcp.tool()
def add_to_playlist(playlist_id: str, track_ids: List[str]):
    uris = [tid if tid.startswith("spotify:track:") else f"spotify:track:{tid}" for tid in track_ids]
    n = svc().add_to_playlist(playlist_id, uris)
    return AddedResult(added=n)
@mcp.tool()
def create_playlist_with_tracks(
    name: str,
    track_ids: List[str],
    description: str = "",
    public: bool = False
):
    uris = [
        tid if str(tid).startswith("spotify:track:")
        else f"spotify:track:{str(tid).split(':')[-1]}"
        for tid in (track_ids or [])
        if tid
    ]
    out = svc().create_playlist(name=name, description=description, public=public)
    added = svc().add_to_playlist(out["playlist_id"], uris) if uris else 0
    return {"playlist_id": out["playlist_id"], "url": out["url"], "added": added}

@mcp.tool()
def ensure_device_ready():
    try: devs = svc().list_devices()
    except PermissionError: return EnsureDeviceResult(device_id=None, status="not_premium")
    if not devs: return EnsureDeviceResult(device_id=None, status="no_devices")
    target = next((d for d in devs if d.get("is_active")), None) or devs[0]
    return EnsureDeviceResult(device_id=target.get("id"), status="ready")

@mcp.tool()
def play_playlist(playlist_id: str, device_id: Optional[str] = None):
    pl_uri = f"spotify:playlist:{playlist_id}" if not playlist_id.startswith("spotify:playlist:") else playlist_id
    try:
        status = svc().start_or_transfer(pl_uri, device_id)
        if status == "not_premium": return PlayResult(status="not_premium", device_id=device_id)
        return PlayResult(status="playing", device_id=device_id)
    except PermissionError:
        return PlayResult(status="unsupported", device_id=device_id)
    except Exception as e:
        logging.exception("play_playlist error: %s", e)
        return PlayResult(status="no_device", device_id=device_id)

@mcp.tool()
def explain_selection(tracks: List[Track], context: ExplainContext):
    feats = svc().audio_features_map([t.id for t in tracks])
    hdr = f"**Contexto:** mood={context.mood or '-'} • activity={context.activity or '-'} • time={context.time_of_day or '-'}"
    lines = [hdr]
    for t in tracks:
        f = feats.get(t.id, {})
        ar = ", ".join(a.name for a in t.artists)
        parts = [
            f"danceability {f['danceability']:.2f}" if f.get("danceability") is not None else None,
            f"energy {f['energy']:.2f}" if f.get("energy") is not None else None,
            f"valence {f['valence']:.2f}" if f.get("valence") is not None else None,
            f"tempo {round(f['tempo'])} BPM" if f.get("tempo") is not None else None,
        ]
        reason = "; ".join(p for p in parts if p) or "sin features disponibles"
        lines.append(f"- **{t.name}** — {ar} → {reason}")
    return {"rationale_md": "\n".join(lines)}

@mcp.tool()
def build_playlist_from_profile(
    mood_prompt: str,
    name: Optional[str] = None,
    public: bool = False,
    limit: int = 25
):
    m = infer_mood(mood_prompt)
    targets = {"energy": m.energy, "valence": m.valence}
    seeds: List[str] = []
    try: seeds = svc().user_seed_track_ids(want=5)
    except (PermissionError, AttributeError): pass
    if len(seeds) < 1:
        themed = svc().search_tracks(query="lofi rain calm piano", limit=5)
        seeds = [t["id"] for t in themed]
    recs = svc().recommendations(seed_tracks=seeds, targets=targets, limit=limit)
    title = name or f"{m.mood.title()} • Rainy Day Mix"
    pl = svc().create_playlist(
        title,
        description=f"{m.mood} · energy {m.energy:.2f} · valence {m.valence:.2f}",
        public=public,
    )
    track_uris = [f"spotify:track:{t['id']}" for t in recs]
    svc().add_to_playlist(pl["playlist_id"], track_uris)
    return PlaylistRef(**pl)

@mcp.tool()
def create_public_mix(mood_prompt: str, name: str = "Bot Mix", limit: int = 20) -> PlaylistRef:
    m = infer_mood(mood_prompt)
    targets = {"energy": m.energy, "valence": m.valence}
    themed = svc().search_tracks(query="alternative rock classics", limit=5)
    seeds = [t["id"] for t in themed] or []
    recs = svc().recommendations(seed_tracks=seeds, targets=targets, limit=limit)
    pl = svc().create_playlist(name, description=f"{m.mood} • auto-mix", public=True)
    svc().add_to_playlist(pl["playlist_id"], [f"spotify:track:{t['id']}" for t in recs])
    return PlaylistRef(**pl)

@mcp.prompt()
def explain_selection_prompt(tracks: List[Track], mood: str = "neutral", activity: str = "", time_of_day: str = ""):
    return (
        "Eres un curador musical. Redacta 1–2 párrafos explicando por qué la siguiente selección encaja "
        f"con mood={mood}, activity={activity}, time_of_day={time_of_day}. "
        "Enfócate en energía, valencia, danceability, tempo y timbre. Lista final:\n"
        + "\n".join(f"- {t.name} — {', '.join(a.name for a in t.artists)}" for t in tracks)
    )

def main():
    mcp.run()
    logging.info("Servidor MCP iniciado correctamente")

if __name__ == "__main__":
    main()
