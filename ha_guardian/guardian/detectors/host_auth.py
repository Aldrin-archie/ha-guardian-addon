from __future__ import annotations

import re
import time
from pathlib import Path

from .base import EventDraft, RateCounter, bump, is_public_ip

SSH_FAIL = re.compile(r"(?:Failed password|Invalid user|authentication failure).*?(?:from|rhost=)\s*([0-9a-fA-F.:]+)")


class HostAuthDetector:
    """Brute-force detection from the host's sshd auth log (skip on HA OS, where you can't read it)."""

    name = "host_auth"

    def __init__(self, path: str = "/var/log/auth.log", window_s: int = 600, threshold: int = 5, clock=time.time):
        self.path = Path(path)
        self.threshold = threshold
        self.counter = RateCounter(window_s)
        self.clock = clock
        self.offset: int | None = None

    def poll(self) -> list[EventDraft]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if self.offset is None or size < self.offset:  # first run or rotated
            first = self.offset is None
            self.offset = size
            if first:
                return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read().decode("utf-8", "replace")
            self.offset = f.tell()
        out, now = [], self.clock()
        for line in chunk.splitlines():
            m = SSH_FAIL.search(line)
            if not m:
                continue
            ip = m.group(1)
            n = self.counter.add(ip, now)
            if n >= self.threshold:
                sev = "high" if n >= 20 else "medium"
                if is_public_ip(ip):
                    sev = bump(sev)
                out.append(EventDraft(
                    "host", "ssh_bruteforce", sev, f"{n} failed SSH logins from {ip}",
                    {"ip": ip, "count": n}, dedupe_key=f"ssh:{ip}:{'20' if n >= 20 else '5'}",
                ))
        return out
