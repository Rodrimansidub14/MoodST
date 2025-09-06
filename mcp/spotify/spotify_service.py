import os, logging, time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from tenacity import retry, wait_exponential, stop_after_attempt
import spotipy
from pathlib import Path
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
from spotipy.exceptions import SpotifyException

import re
load_dotenv() 
load_dotenv(Path(__file__).with_name(".env"))

redir = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback")
redir = redir.replace("localhost", "127.0.0.1")
os.environ["SPOTIPY_CLIENT_ID"] = os.getenv("SPOTIFY_CLIENT_ID", "")
os.environ["SPOTIPY_CLIENT_SECRET"] = os.getenv("SPOTIFY_CLIENT_SECRET", "")
os.environ["SPOTIPY_REDIRECT_URI"] = redir

SCOPES = [
    "playlist-modify-public",
    "playlist-modify-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-top-read",
    "user-read-recently-played",
    "user-read-currently-playing",
    "user-read-private",  # a veces útil para verificar cuenta
]
SCOPE_STR = " ".join(SCOPES)

@dataclass
class SpotifyClients:
    app: spotipy.Spotify
    user: Optional[spotipy.Spotify]

def _build_app_client() -> spotipy.Spotify:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    csec = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("Faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET")
    auth = SpotifyClientCredentials(client_id=cid, client_secret=csec)
    return spotipy.Spotify(auth_manager=auth, requests_timeout=10, retries=3)

def _build_user_client() -> Optional[spotipy.Spotify]:
    cid = os.environ["SPOTIPY_CLIENT_ID"]
    csec = os.environ["SPOTIPY_CLIENT_SECRET"]
    redir = os.environ["SPOTIPY_REDIRECT_URI"]
    logging.info("OAuth redirect_uri=%s", redir)  # debería imprimir 127.0.0.1
    oauth = SpotifyOAuth(
        client_id=cid,
        client_secret=csec,
        redirect_uri=redir,
        scope=SCOPE_STR,
        open_browser=True,
        cache_path=f".cache-{os.getenv('SPOTIFY_USERNAME','me')}",
        requests_timeout=10,
    )
    return spotipy.Spotify(auth_manager=oauth, requests_timeout=10, retries=3)


class SpotifyService:
    def __init__(self):
        self.market = os.environ.get("SPOTIFY_MARKET", "US")
        self.clients = SpotifyClients(app=_build_app_client(), user=None)
    def _ensure_user(self) -> spotipy.Spotify:
        if self.clients.user is None:
            self.clients.user = _build_user_client()  # esto abre el browser (open_browser=True) si falta token
        return self.clients.user

    def _require_user(self) -> spotipy.Spotify:
        sp = self._ensure_user()  # 👈 clave: dispara OAuth si falta
        return sp

    def _sp_tracks(self, ids: list[str]):
        sp = self.clients.app
        try:
            return sp.tracks(tracks=ids, market=self.market)
        except TypeError:
            return sp.tracks(tracks=ids)

    @staticmethod
    def _coerce_seed_list(seeds):
        """Devuelve una lista de IDs de pista (22 chars) válidos, sin prefijos."""
        if not seeds:
            return []
        # aceptar string, lista de strings, URIs, separados por coma/espacio/nueva línea
        if isinstance(seeds, str):
            toks = re.split(r"[,\s]+", seeds.strip())
        else:
            flat = []
            for s in seeds:
                if not s:
                    continue
                if isinstance(s, str):
                    flat += re.split(r"[,\s]+", s.strip())
                else:
                    flat.append(str(s))
            toks = [t for t in flat if t]

        out, seen = [], set()
        for t in toks:
            tid = t.split(":")[-1]  # quita spotify:track:
            if len(tid) == 22 and tid not in seen:
                seen.add(tid)
                out.append(tid)
        return out
    
    # ---------- Catalog-only (app token) ----------
    def search_tracks(self, query: str, limit: int = 10, market: Optional[str] = None) -> List[Dict[str, Any]]:
        sp = self.clients.app
        res = sp.search(q=query, type="track", limit=limit, market=market or self.market)
        items = res.get("tracks", {}).get("items", [])
        out = []
        for t in items:
            out.append({
                "id": t["id"],
                "name": t["name"],
                "artists": [{"name": a["name"]} for a in t.get("artists", [])],
                "uri": t["uri"],
                "preview_url": t.get("preview_url"),
            })
        return out
    def audio_features_map(self, track_ids):
            """Devuelve map id -> features. Tolerante a errores/403 y con chunking."""
            if not track_ids:
                return {}
            # normaliza a IDs (sin prefijo)
            ids = [str(t).split(":")[-1] for t in track_ids if t]
            CHUNK = 50  # seguro << 100 (límite Spotify)
            out = {}
            for i in range(0, len(ids), CHUNK):
                batch = ids[i:i+CHUNK]
                try:
                    feats = self.clients.app.audio_features(tracks=batch) or []
                except SpotifyException as e:
                    logging.warning("audio_features failed (%s) on batch=%d; skip features",
                                    getattr(e, "http_status", e), len(batch))
                    # Fallback duro: sin features → devolvemos lo que haya
                    return {}
                except Exception as e:
                    logging.warning("audio_features error: %s", e)
                    return {}
                for f in feats:
                    if not f or not f.get("id"):
                        continue
                    out[f["id"]] = {
                        "danceability": f.get("danceability"),
                        "energy": f.get("energy"),
                        "valence": f.get("valence"),
                        "tempo": f.get("tempo"),
                        "acousticness": f.get("acousticness"),
                        "instrumentalness": f.get("instrumentalness"),
                    }
            return out

    def _rank_by_targets(self, candidates, targets):
            """Ordena candidatos por cercanía a targets; si no hay features, mantiene orden/popularidad."""
            if not candidates:
                return []

            ids = [t.get("id") for t in candidates if t and t.get("id")]
            fm = self.audio_features_map(ids)
            # sin features → quedate con el orden original (o por popularidad si querés)
            if not fm:
                # Muchos endpoints ya devuelven ordenado por popularidad; si querés forzar:
                # return sorted(candidates, key=lambda t: -(t.get("popularity") or 0))
                return candidates

            keys = ("energy", "valence", "danceability", "tempo")
            want = {k: targets.get(k) for k in keys if targets.get(k) is not None}

            def norm_tempo(x):
                # 60–240 BPM → 0..1
                return max(0.0, min(1.0, (float(x) - 60.0) / 180.0))

            def dist(t):
                f = fm.get(t["id"], {})
                d, n = 0.0, 0
                for k, v in want.items():
                    x = f.get(k)
                    if x is None:
                        continue
                    if k == "tempo":
                        x = norm_tempo(x); v = norm_tempo(v)
                    d += (float(x) - float(v))**2
                    n += 1
                return d / n if n else 999.0

            return sorted(candidates, key=dist)

    def recommendations(self, seed_tracks, targets: Dict[str, float], limit: int = 20):
        sp = self.clients.app

        # 1) Normaliza seeds a IDs de 22 chars
        ids = []
        toks = []
        if isinstance(seed_tracks, str):
            toks = [p.strip() for p in re.split(r"[,\s]+", seed_tracks) if p.strip()]
        else:
            for s in (seed_tracks or []):
                toks += [p.strip() for p in re.split(r"[,\s]+", str(s)) if p.strip()]
        for t in toks:
            tid = t.split(":")[-1]
            if len(tid) == 22:
                ids.append(tid)
        ids = ids[:5]

        # 2) Valida por market y toma artistas de cada track
        valid_ids, artist_ids = [], []
        if ids:
            meta = self._sp_tracks(ids)
            for tr in (meta or {}).get("tracks", []) or []:
                if not tr:
                    continue
                mkts = tr.get("available_markets") or []
                if not mkts or self.market in mkts:
                    valid_ids.append(tr["id"])
                for a in (tr.get("artists") or [])[:1]:
                    if a.get("id"):
                        artist_ids.append(a["id"])
        artist_ids = list(dict.fromkeys(artist_ids))[:5]

        # 3) Targets seguros → target_*
        tgt = {}
        for k in ("energy", "valence", "danceability", "tempo"):
            v = targets.get(k)
            if v is not None:
                tgt[f"target_{k}"] = float(v)

        # 4) Intento “oficial” con /recommendations (si funciona en tu entorno)
        def _official(**params):
            try:
                logging.info("Trying recommendations with params=%s", params)
                res = sp.recommendations(**params) or {}
                tracks = res.get("tracks", [])
                return tracks or []
            except SpotifyException as e:
                logging.warning("Recommendations failed (%s) params=%s",
                                getattr(e, "http_status", e), params)
                return []
            except Exception as e:
                logging.warning("Recommendations error (%s) params=%s", e, params)
                return []

        for k in (5, 3, 2, 1):
            if len(valid_ids) >= k:
                tr = _official(seed_tracks=valid_ids[:k], limit=int(limit), **tgt)
                if tr:
                    return [{
                        "id": t["id"],
                        "name": t["name"],
                        "artists": [{"name": a["name"]} for a in t.get("artists", [])],
                        "uri": t["uri"],
                        "preview_url": t.get("preview_url"),
                    } for t in tr]

        # 5) FALLBACK HEURÍSTICO (sin /recommendations):
        #    - artistas relacionados → top tracks
        #    - si no hay seeds, búsqueda temática
        candidates = []

        if artist_ids:
            # relacionados de cada artista
            for aid in artist_ids:
                try:
                    rel = sp.artist_related_artists(aid).get("artists", [])[:5]
                except Exception:
                    rel = []
                base = [aid] + [a.get("id") for a in rel if a and a.get("id")]
                for a in base[:5]:
                    try:
                        tt = sp.artist_top_tracks(a, market=self.market).get("tracks", [])[:5]
                        candidates.extend(tt)
                    except Exception:
                        pass

        if not candidates:
            # fallback temático a partir del mood
            q = "lofi rain calm piano"  # puedes ajustar por mood
            try:
                sr = sp.search(q=q, type="track", limit=50, market=self.market)
                candidates.extend(sr.get("tracks", {}).get("items", []))
            except Exception:
                pass

        # dedup + filtra por market
        seen = set(); clean = []
        for t in candidates:
            if not t or not t.get("id"): 
                continue
            if t["id"] in seen:
                continue
            mkts = t.get("available_markets") or []
            if mkts and self.market not in mkts:
                continue
            seen.add(t["id"]); clean.append(t)

        # ordena por cercanía a targets y limita
        ranked = self._rank_by_targets(clean, targets)[:int(limit)]
        return [{
            "id": t["id"],
            "name": t["name"],
            "artists": [{"name": a["name"]} for a in t.get("artists", [])],
            "uri": t["uri"],
            "preview_url": t.get("preview_url"),
        } for t in ranked]


    # ---------- User actions (OAuth usuario) ----------
    def _require_user(self) -> spotipy.Spotify:
        if not self.clients.user:
            raise PermissionError("Acción requiere OAuth de usuario configurado (.env) y login previo.")
        return self.clients.user

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> Dict[str, str]:
        sp = self._require_user()
        me = sp.me()
        pl = sp.user_playlist_create(me["id"], name=name, public=public, description=description)
        return {"playlist_id": pl["id"], "url": pl["external_urls"]["spotify"]}

    def add_to_playlist(self, playlist_id: str, track_uris: List[str]) -> int:
        sp = self._require_user()
        if not track_uris:
            return 0
        chunks = [track_uris[i:i+100] for i in range(0, len(track_uris), 100)]
        added = 0
        for ch in chunks:
            sp.playlist_add_items(playlist_id, ch)
            added += len(ch)
        return added

    def list_devices(self) -> list[Dict[str, Any]]:
        sp = self._require_user()
        devs = sp.devices()
        return devs.get("devices", [])

    @retry(wait=wait_exponential(multiplier=0.5, max=4), stop=stop_after_attempt(3))
    def start_or_transfer(self, playlist_uri: str, device_id: Optional[str]) -> str:
        sp = self._require_user()
        try:
            # start playback (Premium requirement)
            sp.start_playback(device_id=device_id, context_uri=playlist_uri)
            return "playing"
        except spotipy.exceptions.SpotifyException as e:
            # 403 => no Premium / restricción de playback
            if e.http_status == 403:
                return "not_premium"
            raise
# dentro de class SpotifyService:

def user_seed_track_ids(self, want: int = 5) -> list[str]:
    """Devuelve hasta 'want' track IDs personalizadas del perfil (top tracks → recently played → top artists)."""
    sp = self._require_user()
    seen = set()
    seeds: list[str] = []

    # 1) Top tracks
    try:
        top = sp.current_user_top_tracks(limit=min(20, max(5, want*3)), time_range="medium_term")
        for it in top.get("items", []):
            tid = it.get("id")
            if tid and tid not in seen:
                seeds.append(tid); seen.add(tid)
            if len(seeds) >= want:
                return seeds
    except Exception:
        pass

    # 2) Recently played
    try:
        rp = sp.current_user_recently_played(limit=50)
        for it in rp.get("items", []):
            tr = it.get("track", {})
            tid = tr.get("id")
            if tid and tid not in seen:
                seeds.append(tid); seen.add(tid)
            if len(seeds) >= want:
                return seeds
    except Exception:
        pass

    # 3) Top artists → artist top tracks
    try:
        arts = sp.current_user_top_artists(limit=5, time_range="medium_term")
        for a in arts.get("items", []):
            at = sp.artist_top_tracks(a["id"], market=self.market)
            for tr in at.get("tracks", []):
                tid = tr.get("id")
                if tid and tid not in seen:
                    seeds.append(tid); seen.add(tid)
                if len(seeds) >= want:
                    return seeds
    except Exception:
        pass

    return seeds  # puede ser < want; el caller hace fallback
