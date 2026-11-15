from __future__ import annotations

import json
from pathlib import Path

from .base import EventDraft


class EntityWatcher:
    """Flags entities that were not there before (new devices/integrations) and open Zigbee permit-join."""

    name = "entities"

    def __init__(self, ha, baseline_path: Path, permit_join_entities: list[str] | None = None):
        self.ha = ha
        self.path = Path(baseline_path)
        self.permit_join = permit_join_entities or []

    def _load(self) -> set[str] | None:
        if not self.path.exists():
            return None
        return set(json.loads(self.path.read_text()))

    def _save(self, ids: set[str]) -> None:
        self.path.write_text(json.dumps(sorted(ids)))

    def poll(self) -> list[EventDraft]:
        states = self.ha.states()
        current = {s["entity_id"] for s in states}
        by_id = {s["entity_id"]: s for s in states}
        out: list[EventDraft] = []

        baseline = self._load()
        if baseline is None:
            self._save(current)  # first run: learn silently
        else:
            new = sorted(current - baseline)
            trackers = [e for e in new if e.startswith("device_tracker.")]
            others = [e for e in new if not e.startswith("device_tracker.")]
            if trackers:
                out.append(EventDraft(
                    "ha", "new_device_tracker", "medium",
                    f"New network device(s) seen by HA: {', '.join(trackers[:5])}",
                    {"entities": trackers}, dedupe_key="new_tracker:" + ",".join(trackers), cooldown=3600,
                ))
            if others:
                out.append(EventDraft(
                    "ha", "new_entities", "low",
                    f"{len(others)} new entities appeared (new device or integration?)",
                    {"entities": others[:30]}, dedupe_key="new_entities:" + ",".join(others[:10]), cooldown=3600,
                ))
            if new:
                self._save(baseline | current)

        for eid in self.permit_join:
            if by_id.get(eid, {}).get("state") == "on":
                out.append(EventDraft(
                    "zigbee", "permit_join_open", "medium",
                    f"Zigbee permit-join is ON ({eid}). New devices can pair right now.",
                    {"entity": eid}, dedupe_key=f"permit:{eid}", cooldown=600,
                ))
        return out
