from __future__ import annotations

import socket
import time

from .base import EventDraft


def default_gateway() -> str | None:
    """Default IPv4 gateway from /proc/net/route (works in host-network mode)."""
    try:
        with open("/proc/net/route") as f:
            for line in list(f)[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    h = parts[2]
                    return ".".join(str(int(h[i : i + 2], 16)) for i in (6, 4, 2, 0))
    except OSError:
        pass
    return None


def tcp_reachable(host: str, port: int) -> bool:
    """True if something answers. A refused connection still proves the host is up."""
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


class ConnectivityWatcher:
    """Internet / router outages. Reports the outage when it starts and again, with duration, when it ends.

    Note: phone pushes need the internet, so an outage alert may only reach Home Assistant itself
    (persistent notification) until the connection returns, when the 'restored' message is pushed.
    """

    name = "connectivity"

    def __init__(self, targets=None, gateway: str | None = None, reachable=tcp_reachable,
                 clock=time.time, fail_limit: int = 2):
        self.targets = [tuple(t) for t in (targets or [("1.1.1.1", 443), ("8.8.8.8", 53)])]
        self.gateway = gateway
        self.reachable, self.clock, self.fail_limit = reachable, clock, fail_limit
        self.fails = 0
        self.down_since: float | None = None

    def poll(self) -> list[EventDraft]:
        now = self.clock()
        internet = any(self.reachable(h, p) for h, p in self.targets)
        if internet:
            self.fails = 0
            if self.down_since is not None:
                mins = max(1, round((now - self.down_since) / 60))
                self.down_since = None
                return [EventDraft("network", "internet_restored", "medium",
                                   f"Internet is back after about {mins} min", {"minutes": mins})]
            return []
        self.fails += 1
        if self.fails < self.fail_limit or self.down_since is not None:
            return []
        self.down_since = now
        gw = self.gateway or default_gateway()
        gw_ok = bool(gw) and any(self.reachable(gw, p) for p in (80, 443, 53))
        if gw_ok:
            msg = f"Internet is down, but the router ({gw}) still answers: likely an ISP or modem problem"
        else:
            msg = "Network looks down: neither the internet nor the router can be reached from the Home Assistant box"
        return [EventDraft("network", "internet_down", "high", msg, {"gateway": gw, "router_reachable": gw_ok})]
