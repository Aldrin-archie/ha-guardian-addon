"""Builds a ready-made Home Assistant dashboard ("Guardian") from the entities that actually exist.

Only built-in cards are used, so nothing extra has to be installed. The dashboard is a separate sidebar
entry; the default dashboard is never touched. It is rebuilt each time the add-on starts, so to customise it,
copy it (three-dot menu > Take control is not needed: just duplicate the cards into your own dashboard).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("guardian.dashboard")

URL_PATH = "ha-guardian"
CAP = 40  # max entities per card, keeps the page usable

SECURITY_CLASSES = {"motion", "occupancy", "presence", "door", "window", "garage_door", "opening",
                    "smoke", "gas", "carbon_monoxide", "moisture", "tamper", "safety", "vibration", "lock"}
SKIP_WORDS = ("linkquality", "last_seen", "firmware", "update_available")


def _name(s: dict) -> str:
    return (s.get("attributes") or {}).get("friendly_name") or s["entity_id"]


def _dc(s: dict) -> str | None:
    return (s.get("attributes") or {}).get("device_class")


def _dom(s: dict) -> str:
    return s["entity_id"].split(".", 1)[0]


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ids(items: list[dict]) -> list[str]:
    return [s["entity_id"] for s in items[:CAP]]


def _tiles(items: list[dict], columns: int = 2) -> list[dict]:
    return [{"type": "grid", "columns": columns, "square": False,
             "cards": [{"type": "tile", "entity": e} for e in _ids(items)]}] if items else []


def _heading(text: str) -> dict:
    return {"type": "markdown", "content": f"## {text}"}


def build(states: list[dict]) -> dict:
    states = [s for s in states if not any(w in s["entity_id"] for w in SKIP_WORDS) and not s["entity_id"].startswith("update.")]
    by_dom: dict[str, list[dict]] = {}
    for s in states:
        by_dom.setdefault(_dom(s), []).append(s)

    def sensors(dc: str) -> list[dict]:
        return sorted((s for s in by_dom.get("sensor", []) if _dc(s) == dc), key=_name)

    people = by_dom.get("person", [])
    lights, switches = by_dom.get("light", []), by_dom.get("switch", [])
    covers, climates, fans = by_dom.get("cover", []), by_dom.get("climate", []), by_dom.get("fan", [])
    locks, alarms, cams = by_dom.get("lock", []), by_dom.get("alarm_control_panel", []), by_dom.get("camera", [])
    media = by_dom.get("media_player", [])
    temps, hums = sensors("temperature"), sensors("humidity")
    power, energy = sensors("power"), sensors("energy")
    sec_bins = sorted((s for s in by_dom.get("binary_sensor", []) if _dc(s) in SECURITY_CLASSES), key=_name)
    motion = [s for s in sec_bins if _dc(s) in ("motion", "occupancy", "presence")]

    # ---- Home ----------------------------------------------------------------------------------
    home: list[dict] = []
    # One-tap controls: every script (e.g. Movie mode) and any automation that is a mode switch (name has "movie").
    scripts = sorted(by_dom.get("script", []), key=_name)
    modes = sorted((s for s in by_dom.get("automation", []) if "movie" in _name(s).lower()), key=_name)
    if scripts or modes:
        home.append(_heading("Controls"))
        home += _tiles(scripts + modes)
    if people:
        home.append({"type": "glance", "title": "People", "entities": _ids(people), "show_state": True})
    if temps or hums:
        home.append({"type": "glance", "title": "Climate", "entities": _ids(temps + hums), "show_name": True})
    if alarms or locks:
        home.append({"type": "entities", "title": "Locks & alarm", "entities": _ids(alarms + locks)})
    if lights:
        home.append(_heading("Lights"))
        home += _tiles(lights)
    if switches or fans or covers:
        home.append(_heading("Switches & covers"))
        home += _tiles(switches + fans + covers)
    if climates:
        home += [{"type": "thermostat", "entity": e} for e in _ids(climates)]
    if media:
        home += [{"type": "media-control", "entity": e} for e in _ids(media)[:4]]

    # ---- Security ------------------------------------------------------------------------------
    security: list[dict] = []
    for c in cams[:6]:
        security.append({"type": "picture-entity", "entity": c["entity_id"], "camera_view": "auto", "show_state": False})
    if sec_bins:
        security.append({"type": "entities", "title": "Sensors", "entities": _ids(sec_bins)})
    if motion:
        security.append({"type": "history-graph", "title": "Motion, last 24 h", "hours_to_show": 24, "entities": _ids(motion)[:10]})
    if locks or alarms:
        security.append({"type": "entities", "title": "Locks & alarm", "entities": _ids(locks + alarms)})

    # ---- Climate & energy ----------------------------------------------------------------------
    climate: list[dict] = []
    if temps:
        climate.append({"type": "history-graph", "title": "Temperature, last 24 h", "hours_to_show": 24, "entities": _ids(temps)[:10]})
    if hums:
        climate.append({"type": "history-graph", "title": "Humidity, last 24 h", "hours_to_show": 24, "entities": _ids(hums)[:10]})
    if power:
        climate.append({"type": "history-graph", "title": "Power, last 24 h", "hours_to_show": 24, "entities": _ids(power)[:10]})
    if energy:
        climate.append({"type": "entities", "title": "Energy", "entities": _ids(energy)})

    # ---- Health --------------------------------------------------------------------------------
    batteries = []
    for s in by_dom.get("sensor", []):
        if _dc(s) == "battery" and _num(s["state"]) is not None:
            batteries.append(s)
    batteries.sort(key=lambda s: _num(s["state"]))
    low_bin = [s for s in by_dom.get("binary_sensor", []) if _dc(s) == "battery"]
    ink = [s for s in by_dom.get("sensor", []) if ("ink" in s["entity_id"].lower().replace("link", "") or "toner" in s["entity_id"].lower())
           and _num(s["state"]) is not None]
    offline = [s for s in states if s["state"] == "unavailable"]
    health: list[dict] = [{
        "type": "markdown",
        "content": f"## Device health\n**{len(offline)}** offline · **{sum(1 for b in batteries if _num(b['state']) < 20)}** low batteries"
                   + (f" · **{len(ink)}** ink/toner sensors" if ink else ""),
    }]
    if offline:
        health.append({"type": "entities", "title": "Offline right now", "entities": _ids(offline)})
    if batteries or low_bin:
        health.append({"type": "entities", "title": "Batteries (lowest first)", "entities": _ids(low_bin + batteries)})
    if ink:
        health.append({"type": "entities", "title": "Printer ink / toner", "entities": _ids(sorted(ink, key=lambda s: _num(s["state"])))})

    views = [{"title": "Home", "path": "home", "icon": "mdi:home", "cards": home or [_heading("No devices found yet")]},
             {"title": "Security", "path": "security", "icon": "mdi:shield-home", "cards": security or [_heading("No cameras or door/motion sensors found")]},
             {"title": "Climate", "path": "climate", "icon": "mdi:thermometer", "cards": climate or [_heading("No climate or energy sensors found")]},
             {"title": "Health", "path": "health", "icon": "mdi:heart-pulse", "cards": health}]
    return {"title": "Guardian", "views": views}


def install(ha, ws_url: str, token: str) -> str:
    """Create the 'Guardian' dashboard if missing and (re)write its content. Returns a one-line result."""
    from websockets.sync.client import connect

    cfg = build(ha.states())
    with connect(ws_url, open_timeout=15) as ws:
        json.loads(ws.recv())
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise RuntimeError("websocket auth failed")
        n = 0

        def call(payload: dict) -> dict:
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, **payload}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == n and m.get("type") == "result":
                    return m

        existing = call({"type": "lovelace/dashboards/list"})
        if not existing.get("success"):
            raise RuntimeError(f"cannot list dashboards: {existing.get('error')}")
        if not any(d.get("url_path") == URL_PATH for d in existing.get("result", [])):
            r = call({"type": "lovelace/dashboards/create", "url_path": URL_PATH, "title": "Guardian",
                      "icon": "mdi:shield-home", "show_in_sidebar": True, "require_admin": False, "mode": "storage"})
            if not r.get("success"):
                raise RuntimeError(f"cannot create dashboard: {r.get('error')}")
        r = call({"type": "lovelace/config/save", "url_path": URL_PATH, "config": cfg})
        if not r.get("success"):
            raise RuntimeError(f"cannot save dashboard: {r.get('error')}")
    cards = sum(len(v["cards"]) for v in cfg["views"])
    return f"Dashboard 'Guardian' updated ({cards} cards). Find it in the sidebar."
