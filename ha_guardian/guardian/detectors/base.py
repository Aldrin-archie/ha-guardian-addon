from __future__ import annotations

import ipaddress
from collections import defaultdict, deque
from dataclasses import dataclass, field

SEVERITIES = ["info", "low", "medium", "high", "critical"]


def rank(severity: str) -> int:
    return SEVERITIES.index(severity)


def bump(severity: str) -> str:
    return SEVERITIES[min(rank(severity) + 1, len(SEVERITIES) - 1)]


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved)


@dataclass
class EventDraft:
    source: str
    kind: str
    severity: str
    summary: str
    details: dict = field(default_factory=dict)
    dedupe_key: str | None = None
    cooldown: int = 900  # seconds during which an identical dedupe_key is suppressed


class RateCounter:
    """Counts hits per key inside a sliding time window."""

    def __init__(self, window_s: int):
        self.window = window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def add(self, key: str, now: float) -> int:
        q = self.hits[key]
        q.append(now)
        while q and q[0] < now - self.window:
            q.popleft()
        return len(q)
