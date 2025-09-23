# mcp/lol/server.py
import sys, json, traceback, requests
from typing import Dict, Any, Callable, List

ENC = "utf-8"

def _respond(obj: Dict[str, Any]) -> None:
    body = json.dumps(obj).encode(ENC)
    hdr  = f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode(ENC)
    sys.stdout.buffer.write(hdr); sys.stdout.buffer.write(body); sys.stdout.flush()

def _read_request():
    buf = b""
    while (b"\r\n\r\n" not in buf) and (b"\n\n" not in buf):
        ch = sys.stdin.buffer.read(1)
        if not ch: return None
        buf += ch
    if b"\r\n\r\n" in buf:
        header_bytes, rest = buf.split(b"\r\n\r\n", 1)
    else:
        header_bytes, rest = buf.split(b"\n\n", 1)

    header_text = header_bytes.decode(ENC, errors="ignore")
    length = None
    for line in header_text.splitlines():
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
            break
    if length is None: return None

    body = rest
    to_read = length - len(body)
    while to_read > 0:
        chunk = sys.stdin.buffer.read(to_read)
        if not chunk: return None
        body += chunk
        to_read -= len(chunk)

    try:
        return json.loads(body.decode(ENC, errors="ignore"))
    except Exception:
        return None

def _ok(id_, result):  return {"jsonrpc":"2.0","id":id_,"result":result}
def _err(id_, msg, code=-32000): return {"jsonrpc":"2.0","id":id_,"error":{"code":code,"message":msg}}

class DDragonClient:
    def __init__(self, version="latest", lang="en_US"):
        self.version = version; self.lang = lang
        self.champions: Dict[str, Dict[str, Any]] = {}
        self.items = {
            "boots_armor": "Plated Steelcaps",
            "boots_mr": "Mercury's Treads",
            "boots_ms": "Boots of Swiftness",
            "anti_heal_armor": "Thornmail",
            "anti_heal_ap": "Morellonomicon",
            "armor": "Dead Man's Plate",
            "mr": "Force of Nature",
            "hp": "Warmog's Armor",
            "ad_core_tank": "Stridebreaker",
            "ad_core_bruiser": "Black Cleaver",
            "ap_core_mage": "Liandry's Torment",
            "tenacity_item": "Silvermere Dawn"
        }

    def bootstrap(self):
        ver = self._resolve_version(self.version)
        data = self._fetch_json(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/{self.lang}/champion.json")
        champs = data.get("data", {})
        index: Dict[str, Dict[str, Any]] = {}
        for key, c in champs.items():
            name = c.get("id", key)
            tags = c.get("tags", [])
            info = c.get("info", {})
            spells = c.get("spells", [])
            passive = c.get("passive", {})
            damage = self._infer_damage(tags)
            cc_score = self._estimate_cc(spells, passive)
            tanky = bool(info.get("toughness", 0) >= 5 or "Tank" in tags)
            heals = self._has_heal(spells, passive)
            index[name.lower()] = {"damage": damage, "tanky": tanky, "cc_score": cc_score, "heals": heals}
        self.champions = index

    def get_champion(self, name: str): return self.champions.get(name.lower())

    def _resolve_version(self, version: str) -> str:
        if version != "latest": return version
        versions = self._fetch_json("https://ddragon.leagueoflegends.com/api/versions.json")
        return versions[0] if isinstance(versions, list) and versions else "14.10.1"

    def _fetch_json(self, url: str):
        resp = requests.get(url, timeout=30); resp.raise_for_status(); return resp.json()

    def _infer_damage(self, tags: List[str]) -> str:
        t = set(tags)
        if "Mage" in t: return "AP"
        if "Marksman" in t or "Assassin" in t or "Fighter" in t: return "AD"
        if "Tank" in t: return "MIX"
        return "MIX"

    def _estimate_cc(self, spells, passive) -> int:
        text = " ".join([(s.get("tooltip","")+" "+s.get("description","")) for s in spells]) + " " + passive.get("description","")
        text = text.lower()
        hard = ["stun","root","snare","knock up","knockup","airborne","taunt","fear","charm","sleep","suppression","silence"]
        soft = ["slow","cripple"]
        score = 0
        for k in hard:
            if k in text: score += 2
        for k in soft:
            if k in text: score += 1
        return max(1, min(score, 12))

    def _has_heal(self, spells, passive) -> bool:
        text = " ".join([(s.get("tooltip","")+" "+s.get("description","")) for s in spells]) + " " + passive.get("description","")
        text = text.lower()
        keys = ["heal","restores health","restores hp","lifesteal","omnivamp","regenerate"]
        return any(k in text for k in keys)

def analyze_enemy_comp(dd: DDragonClient, enemy_team: List[str]) -> Dict[str, Any]:
    picks = [str(x).strip().lower() for x in enemy_team]
    ad = ap = 0.0; cc_total = 0; tanks, healing, missing = [], [], []
    for c in picks:
        info = dd.get_champion(c)
        if not info: missing.append(c); continue
        dmg = info["damage"]
        if dmg == "AD": ad += 1
        elif dmg == "AP": ap += 1
        else: ad += 0.5; ap += 0.5
        cc_total += info["cc_score"]
        if info["tanky"]: tanks.append(c)
        if info["heals"]: healing.append(c)
    denom = max(ad + ap, 1)
    ad_ratio = round(ad / denom, 2); ap_ratio = round(ap / denom, 2)
    cc_level = "HIGH" if cc_total >= 10 else ("MEDIUM" if cc_total >= 6 else "LOW")
    out = {"ad_ratio": ad_ratio, "ap_ratio": ap_ratio, "cc_level": cc_level,
           "tanks": tanks, "healing_sources": healing, "picks": picks}
    if missing: out["unknown_champions"] = missing
    return out

def suggest_runes(ally_characteristic: str, comp: Dict[str, Any]) -> Dict[str, Any]:
    char = ally_characteristic.upper(); high_cc = comp.get("cc_level") == "HIGH"
    if char in ("AD","TANK"):
        primary = ["PRECISION", ["Conqueror","Triumph","Legend: Tenacity" if high_cc else "Legend: Alacrity","Last Stand"]]
        secondary = ["RESOLVE", ["Second Wind" if high_cc else "Demolish","Overgrowth"]]
        shards = ["AS","Armor" if comp.get("ad_ratio",0.5)>=0.5 else "MR","HP"]
    else:
        primary = ["SORCERY", ["Arcane Comet","Manaflow Band","Transcendence","Scorch"]]
        secondary = ["RESOLVE" if high_cc else "INSPIRATION",
                     ["Second Wind","Overgrowth"] if high_cc else ["Biscuit Delivery","Cosmic Insight"]]
        shards = ["AS","MR" if comp.get("ap_ratio",0.5)>=0.5 else "Armor","HP"]
    return {"primary":{"tree":primary[0],"picks":primary[1]},
            "secondary":{"tree":secondary[0],"picks":secondary[1]},
            "shards": shards}

def suggest_summoners(ally_characteristic: str, comp: Dict[str, Any]):
    high_cc = comp.get("cc_level") == "HIGH"
    if ally_characteristic.upper() in ("AD","TANK"):
        return {"summoners": ["Flash","Cleanse"] if high_cc else ["Flash","Ghost"]}
    return {"summoners": ["Flash","Cleanse"] if high_cc else ["Flash","Barrier"]}

def suggest_items(ally_characteristic: str, comp: Dict[str, Any], items_dict: Dict[str,str]):
    ad_ratio = comp.get("ad_ratio",0.5); ap_ratio = comp.get("ap_ratio",0.5)
    high_cc = comp.get("cc_level") == "HIGH"; has_heal = bool(comp.get("healing_sources"))
    boots = items_dict["boots_mr"] if high_cc else (items_dict["boots_armor"] if ad_ratio >= ap_ratio else items_dict["boots_mr"])
    core = []
    if ally_characteristic.upper() in ("AD","TANK"):
        core.append(items_dict["ad_core_tank"]); core.append(items_dict["armor"] if ad_ratio >= ap_ratio else items_dict["mr"])
    else:
        core.append(items_dict["ap_core_mage"]); core.append(items_dict["mr"] if ap_ratio >= ad_ratio else items_dict["armor"])
    situational = []
    if has_heal: situational.append(items_dict["anti_heal_armor"] if ad_ratio >= ap_ratio else items_dict["anti_heal_ap"])
    if high_cc: situational.append(items_dict["tenacity_item"])
    if abs(ad_ratio - ap_ratio) < 0.2: situational.append(items_dict["hp"])
    return {"starter":["Doran's Shield","Health Potion"],"boots":[boots],"core":core,"situational":situational}

STATE: Dict[str, Any] = {"dd": None, "last_comp": None}
TOOLS: Dict[str, Dict[str, Any]] = {}

def require_dd():
    if STATE["dd"] is None:
        raise RuntimeError("Data Dragon no inicializado. Llama a fetch_static_data primero.")
    return STATE["dd"]

def tool(name: str, description: str, schema: Dict[str, Any]):
    def deco(fn):
        TOOLS[name] = {"name": name, "description": description, "input_schema": schema, "run": fn}
        return fn
    return deco

@tool("fetch_static_data","Descarga/bootstrapea Data Dragon",{
    "type":"object","properties":{"ddragon_version":{"type":"string"},"lang":{"type":"string"}},
    "additionalProperties": False
})
def _tool_fetch_static_data(params: Dict[str, Any]):
    version = params.get("ddragon_version") or "latest"
    lang = params.get("lang") or "en_US"
    dd = DDragonClient(version=version, lang=lang); dd.bootstrap(); STATE["dd"] = dd
    return {"ok": True, "version": version, "lang": lang}

@tool("analyze_enemies","Analiza comp enemiga",{
    "type":"object","properties":{"enemy_team":{"type":"array","items":{"type":"string"},"minItems":5,"maxItems":5}},
    "required":["enemy_team"],"additionalProperties":False
})
def _tool_analyze(params: Dict[str, Any]):
    dd = require_dd()
    comp = analyze_enemy_comp(dd, [str(x).strip().lower() for x in params["enemy_team"]])
    STATE["last_comp"] = comp; return comp

@tool("suggest_runes","Runas sugeridas (usa last_comp si no mandas comp)",{
    "type":"object","properties":{"ally_champion":{"type":"string"},"ally_characteristic":{"type":"string","enum":["AD","AP","TANK"]},"comp":{"type":"object"}},
    "required":["ally_champion","ally_characteristic"],"additionalProperties":False
})
def _tool_runes(params: Dict[str, Any]):
    comp = params.get("comp") or STATE.get("last_comp")
    if not comp: raise RuntimeError("No 'comp' y no hay 'last_comp'. Ejecuta analyze_enemies primero.")
    return suggest_runes(params["ally_characteristic"], comp)

@tool("suggest_summoners","Hechizos sugeridos (usa last_comp si no mandas comp)",{
    "type":"object","properties":{"ally_champion":{"type":"string"},"ally_characteristic":{"type":"string","enum":["AD","AP","TANK"]},"comp":{"type":"object"}},
    "required":["ally_champion","ally_characteristic"],"additionalProperties":False
})
def _tool_summs(params: Dict[str, Any]):
    comp = params.get("comp") or STATE.get("last_comp")
    if not comp: raise RuntimeError("No 'comp' y no hay 'last_comp'. Ejecuta analyze_enemies primero.")
    return suggest_summoners(params["ally_characteristic"], comp)

@tool("suggest_items","Ítems sugeridos (usa last_comp si no mandas comp)",{
    "type":"object","properties":{"ally_champion":{"type":"string"},"ally_characteristic":{"type":"string","enum":["AD","AP","TANK"]},"comp":{"type":"object"}},
    "required":["ally_champion","ally_characteristic"],"additionalProperties":False
})
def _tool_items(params: Dict[str, Any]):
    dd = require_dd(); comp = params.get("comp") or STATE.get("last_comp")
    if not comp: raise RuntimeError("No 'comp' y no hay 'last_comp'. Ejecuta analyze_enemies primero.")
    return suggest_items(params["ally_characteristic"], comp, dd.items)

@tool("plan_build","One-shot: 5 enemigos → items/runes/summoners",{
    "type":"object","properties":{"ally_champion":{"type":"string"},"ally_characteristic":{"type":"string","enum":["AD","AP","TANK"]},"enemy_team":{"type":"array","items":{"type":"string"},"minItems":5,"maxItems":5}},
    "required":["ally_champion","ally_characteristic","enemy_team"],"additionalProperties":False
})
def _tool_plan(params: Dict[str, Any]):
    dd = require_dd()
    comp = analyze_enemy_comp(dd, [str(x).strip().lower() for x in params["enemy_team"]])
    STATE["last_comp"] = comp
    return {
        "comp": comp,
        "items": suggest_items(params["ally_characteristic"], comp, dd.items),
        "runes": suggest_runes(params["ally_characteristic"], comp),
        "summoners": suggest_summoners(params["ally_characteristic"], comp)
    }

def _rpc_initialize(_):
    return {"protocolVersion":"2024-11-05","serverInfo":{"name":"lol-champion-builder","version":"0.3.0"},
            "capabilities":{"tools":{"listChanged": True}}}

def _rpc_tools_list(_):
    return {"tools":[{"name":t["name"],"description":t["description"],"inputSchema":t["input_schema"]} for t in TOOLS.values()]}

def _rpc_tools_call(params):
    name = params.get("name"); args = params.get("arguments") or {}
    if name not in TOOLS: raise RuntimeError(f"Tool not found: {name}")
    out = TOOLS[name]["run"](args)
    return {"content":[{"type":"json","json":out}]}

HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "initialize": _rpc_initialize,
    "tools/list": _rpc_tools_list,
    "tools/call": _rpc_tools_call,
}

def main():
    while True:
        req = _read_request()
        if req is None: continue
        id_ = req.get("id"); method = req.get("method"); params = req.get("params") or {}
        try:
            if method not in HANDLERS: raise RuntimeError(f"Unknown method: {method}")
            result = HANDLERS[method](params)
            _respond(_ok(id_, result))
        except Exception as e:
            _respond(_err(id_, f"{e}\n{traceback.format_exc()}"))

if __name__ == "__main__":
    main()
