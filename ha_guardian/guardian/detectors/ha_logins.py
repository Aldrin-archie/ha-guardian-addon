from __future__ import annotations

import re
import time

from .base import EventDraft, RateCounter, bump, is_public_ip

# HA logs "... invalid authentication from <hostname-or-ip> (<ip>). ..." (hostname first, IP in brackets),
# so always prefer the bracketed IP. The bare-IP fallback only matches real IPv4/IPv6 addresses, never hostnames.
IP = r"((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F]*:[0-9a-fA-F:]+)"
FAILED = [
    re.compile(r"invalid authentication from \S+ \(" + IP + r"\)"),
    re.compile(r"invalid authentication from " + IP + r"(?![\w-])"),
    re.compile(r"Login attempt failed.*?\(" + IP + r"\)"),
    re.compile(r"Login attempt failed.*?from " + IP + r"(?![\w-])"),
]
BANNED = re.compile(r"Banned IP " + IP)


class HALoginDetector:
    """Watches HA's own log (via /api/error_log) for failed logins and IP bans."""

    name = "ha_logins"
    TAIL = 5

    def __init__(self, ha, window_s: int = 600, threshold: int = 5, clock=time.time):
        self.ha = ha
        self.threshold = threshold
        self.counter = RateCounter(window_s)
        self.clock = clock
        self.tail: list[str] | None = None  # None => first poll, baseline only

    def _new_lines(self, lines: list[str]) -> list[str]:
        """Lines added since the last poll. Matches on the previous poll's last few lines, so it also works
        when the source is a rolling window (the Supervisor returns only the last N lines)."""
        if self.tail is None:  # first poll: baseline only
            self.tail = lines[-self.TAIL:]
            return []
        k = len(self.tail)
        new = lines  # tail not found (log rotated or a burst bigger than the window): treat everything as new
        for i in range(len(lines) - k, -1, -1):
            if k and lines[i : i + k] == self.tail:
                new = lines[i + k :]
                break
        if lines:
            self.tail = lines[-self.TAIL:]
        return new

    def poll(self) -> list[EventDraft]:
        new = self._new_lines(self.ha.error_log().splitlines())
        now, out = self.clock(), []
        for line in new:
            m = BANNED.search(line)
            if m:
                ip = m.group(1)
                out.append(EventDraft(
                    "ha", "ip_banned", "high" if is_public_ip(ip) else "medium",
                    f"HA banned {ip} after repeated failed logins",
                    {"ip": ip, "public": is_public_ip(ip), "line": line[:300]},
                    dedupe_key=f"ban:{ip}",
                ))
                continue
            for pat in FAILED:
                m = pat.search(line)
                if not m:
                    continue
                ip = m.group(1)
                n = self.counter.add(ip, now)
                if n >= self.threshold:
                    public = is_public_ip(ip)
                    sev = "medium"
                    if n >= 20:
                        sev = "high"
                    if public:
                        sev = bump(sev)  # failed logins from the internet means HA is exposed
                    out.append(EventDraft(
                        "ha", "failed_logins", sev,
                        f"{n} failed HA logins from {ip} in the last {self.counter.window // 60} min"
                        + (" (PUBLIC IP: HA may be exposed to the internet)" if public else ""),
                        {"ip": ip, "count": n, "public": public},
                        dedupe_key=f"login:{ip}:{'20' if n >= 20 else '5'}",
                    ))
                break
        return out
