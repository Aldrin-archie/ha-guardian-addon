from __future__ import annotations

import re
import time
from fnmatch import fnmatch

from .base import EventDraft

# Domains worth watching for "offline". Others (weather, calendar, update...) flap or are cloud-only noise.
DEFAULT_DOMAINS = [
    "sensor", "binary_sensor", "light", "switch", "cover", "climate", "fan", "camera",
    "media_player", "lock", "vacuum", "humidifier", "water_heater", "alarm_control_panel", "remote",
]
# 'ink' / 'toner' as a whole word-ish token, so "linkquality" or "Wink" don't match.
INK_RE = re.compile(r"(?<![a-z])(ink|toner)(?![a-z])", re.I)


def _num(state) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


class HealthWatcher:
    """Devices offline or removed, low batteries, low printer ink/toner, and recoveries."""

    name = "health"

    def __init__(self, ha, cfg: dict | None = None, clock=time.time):
        c = cfg or {}
        self.ha, self.clock = ha, clock
        self.after = int(c.get("unavailable_after_s", 600))
        self.battery_below = float(c.get("battery_below", 20))
        self.ink_below = float(c.get("ink_below", 15))
        self.ignore = list(c.get("ignore_entities", []))
        self.domains = set(c.get("domains", DEFAULT_DOMAINS))
        self.down_since: dict[str, float] = {}      # entity -> first time seen unavailable
        self.missing_since: dict[str, float] = {}   # entity -> first time seen missing from HA
        self.reported: set[str] = set()             # entities already reported offline/removed
        self.known: set[str] = set()                # every monitored entity seen so far
        self.names: dict[str, str] = {}

    def _watched(self, eid: str) -> bool:
        return eid.split(".", 1)[0] in self.domains and not any(fnmatch(eid, g) for g in self.ignore)

    def poll(self) -> list[EventDraft]:
        now, out = self.clock(), []
        offline: list[tuple[str, str, str]] = []   # (entity, name, why)
        back: list[tuple[str, str]] = []
        present: set[str] = set()

        for s in self.ha.states():
            eid = s["entity_id"]
            if not self._watched(eid):
                continue
            present.add(eid)
            attrs = s.get("attributes", {}) or {}
            name = attrs.get("friendly_name") or eid
            self.names[eid] = name
            self.known.add(eid)
            self.missing_since.pop(eid, None)
            if s["state"] == "unavailable":
                t0 = self.down_since.setdefault(eid, now)
                if now - t0 >= self.after and eid not in self.reported:
                    self.reported.add(eid)
                    offline.append((eid, name, "unavailable"))
            else:
                self.down_since.pop(eid, None)
                if eid in self.reported:
                    self.reported.discard(eid)
                    back.append((eid, name))
                self._levels(eid, name, s["state"], attrs, out)

        # Entities that vanished (integration unloaded, device unlinked/deleted)
        for eid in sorted(self.known - present):
            t0 = self.missing_since.setdefault(eid, now)
            if now - t0 >= self.after and eid not in self.reported:
                self.reported.add(eid)
                offline.append((eid, self.names.get(eid, eid), "removed"))

        if offline:
            n = len(offline)
            listed = ", ".join(f"{name}{' (removed)' if why == 'removed' else ''}" for _, name, why in offline[:8])
            more = f" and {n - 8} more" if n > 8 else ""
            out.append(EventDraft(
                "health", "devices_offline", "high" if n >= 8 else "medium",
                f"{n} device(s) offline for {self.after // 60}+ min: {listed}{more}",
                {"entities": [e for e, _, _ in offline][:50], "removed": [e for e, _, w in offline if w == "removed"]},
            ))
        if back:
            names = ", ".join(n for _, n in back[:8])
            out.append(EventDraft(
                "health", "devices_recovered", "low",
                f"{len(back)} device(s) back online: {names}", {"entities": [e for e, _ in back][:50]},
            ))
        return out

    def _levels(self, eid: str, name: str, state: str, attrs: dict, out: list[EventDraft]) -> None:
        dom = eid.split(".", 1)[0]
        dc = attrs.get("device_class")
        unit = attrs.get("unit_of_measurement")
        if dc == "battery":
            if dom == "binary_sensor" and state == "on":
                out.append(EventDraft("health", "low_battery", "low", f"{name}: battery low",
                                      {"entity": eid}, dedupe_key=f"battery:{eid}", cooldown=86400))
            elif dom == "sensor":
                v = _num(state)
                if v is not None and v < self.battery_below:
                    out.append(EventDraft("health", "low_battery", "medium" if v < 10 else "low",
                                          f"{name}: battery at {v:.0f}%", {"entity": eid, "percent": v},
                                          dedupe_key=f"battery:{eid}", cooldown=86400))
        elif dom == "sensor" and INK_RE.search(f"{eid} {name}") and unit in (None, "%"):
            v = _num(state)
            if v is not None and 0 <= v <= 100 and v < self.ink_below:
                out.append(EventDraft("health", "low_ink", "medium" if v < 5 else "low",
                                      f"{name}: {v:.0f}% left", {"entity": eid, "percent": v},
                                      dedupe_key=f"ink:{eid}", cooldown=172800))
