from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import EventDraft

NEIGH_RE = re.compile(r"^(\S+) dev (\S+) lladdr ([0-9a-fA-F:]{17}) (\S+)$")
GOOD_STATES = {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"}


def parse_neigh(text: str) -> dict[str, str]:
    """Parse `ip neigh show` output into {mac: ip}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = NEIGH_RE.match(line.strip())
        if m and m.group(4) in GOOD_STATES:
            out[m.group(3).lower()] = m.group(1)
    return out


class ArpWatcher:
    """Spots devices with a MAC address never seen before on the LAN.

    Must run on the same Layer-2 network as your devices (e.g. on the NUC, Docker with host networking).
    """

    name = "arp"

    def __init__(self, known_path: Path, scan_cidr: str = "", known_macs: list[str] | None = None, runner=None):
        self.path = Path(known_path)
        self.scan_cidr = scan_cidr
        self.trusted = {m.lower() for m in (known_macs or [])}
        self.run = runner or self._run

    @staticmethod
    def _run(cmd: list[str]) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout

    def poll(self) -> list[EventDraft]:
        if self.scan_cidr and shutil.which("nmap"):
            self.run(["nmap", "-sn", "-n", self.scan_cidr])  # populates the neighbour table
        seen = parse_neigh(self.run(["ip", "neigh", "show"]))
        known: dict[str, dict] | None = json.loads(self.path.read_text()) if self.path.exists() else None
        first_run = known is None
        known = known or {}
        out = []
        for mac, ip in seen.items():
            if mac in known or mac in self.trusted:
                known.setdefault(mac, {"ip": ip, "first_seen": time.time()})
                continue
            known[mac] = {"ip": ip, "first_seen": time.time()}
            if not first_run:
                out.append(EventDraft(
                    "network", "new_lan_device", "medium",
                    f"Unknown device joined the LAN: {mac} at {ip}",
                    {"mac": mac, "ip": ip}, dedupe_key=f"arp:{mac}", cooldown=86400,
                ))
        self.path.write_text(json.dumps(known))
        return out
