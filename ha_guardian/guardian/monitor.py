from __future__ import annotations

import logging
import time

from .detectors.base import EventDraft, rank
from .events import EventStore

log = logging.getLogger("guardian.monitor")

ALERT_NOW = rank("high")      # sent immediately by deterministic code, no LLM involved
FALLBACK_NOTIFY = rank("medium")  # if triage is unavailable, notify at this level and up


class Monitor:
    """Polls detectors, queues events, alerts on serious ones deterministically, then asks the LLM to triage.

    Detection and high-severity alerting never depend on the LLM. If the LLM or API fails,
    medium+ events are still sent to you.
    """

    def __init__(self, detectors, store: EventStore, channel, triage=None, fail_limit: int = 3):
        self.detectors, self.store, self.channel, self.triage = detectors, store, channel, triage
        self.fail_limit = fail_limit
        self.failures: dict[str, int] = {}

    def tick(self) -> None:
        for det in self.detectors:
            try:
                drafts = det.poll()
                self.failures[det.name] = 0
            except Exception as e:
                n = self.failures[det.name] = self.failures.get(det.name, 0) + 1
                log.warning("detector %s failed: %s", det.name, e)
                if n == self.fail_limit:  # blind spots are themselves worth knowing about
                    drafts = [EventDraft(
                        "guardian", "detector_failing", "medium",
                        f"Detector '{det.name}' has failed {n} times in a row: {e}",
                        {"detector": det.name}, dedupe_key=f"fail:{det.name}", cooldown=3600,
                    )]
                else:
                    continue
            for d in drafts:
                self.store.add(d)

        pending = self.store.pending()
        if not pending:
            return

        urgent = [e for e in pending if rank(e["severity"]) >= ALERT_NOW and not e["alerted"]]
        for e in urgent:
            self.channel.send(f"🚨 [{e['severity'].upper()}] {e['summary']}")
        self.store.mark_alerted([e["id"] for e in urgent])

        if self.triage is not None:
            try:
                history = self.store.recent(24, 30, exclude=[e["id"] for e in pending])
                self.triage.process(pending, history)
            except Exception as e:
                log.warning("triage failed: %s", e)
                self._fallback(pending, f"(AI triage unavailable: {e})")
        else:
            self._fallback(pending, "")
        self.store.mark_handled([e["id"] for e in pending])

    def _fallback(self, pending: list[dict], note: str) -> None:
        for e in pending:
            if rank(e["severity"]) >= FALLBACK_NOTIFY and not (e["alerted"] or rank(e["severity"]) >= ALERT_NOW):
                self.channel.send(f"⚠️ [{e['severity']}] {e['summary']} {note}".strip())

    def run_forever(self, interval: int = 60) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("monitor tick crashed")
            time.sleep(interval)
