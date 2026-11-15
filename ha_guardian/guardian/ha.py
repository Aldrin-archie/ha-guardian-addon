from __future__ import annotations

import httpx


class HAClient:
    """Thin Home Assistant REST client."""

    def __init__(self, url: str, token: str, timeout: float = 15.0):
        self.supervisor = "//supervisor/" in url + "/"  # running as an add-on via the Supervisor proxy
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def states(self) -> list[dict]:
        r = self.http.get("/api/states")
        r.raise_for_status()
        return r.json()

    def state(self, entity_id: str) -> dict:
        r = self.http.get(f"/api/states/{entity_id}")
        r.raise_for_status()
        return r.json()

    def call_service(self, domain: str, service: str, data: dict | None = None) -> None:
        r = self.http.post(f"/api/services/{domain}/{service}", json=data or {})
        r.raise_for_status()

    def services(self) -> list[dict]:
        r = self.http.get("/api/services")
        r.raise_for_status()
        return r.json()

    def error_log(self) -> str:
        r = self.http.get("/api/error_log")
        if r.status_code == 200:
            return r.text
        if not self.supervisor:
            r.raise_for_status()
        # Add-on mode: fall back to the Supervisor's core log endpoint.
        r2 = self.http.get("/logs", headers={"Accept": "text/plain"})
        if r2.status_code == 200:
            return r2.text
        raise RuntimeError(
            f"cannot read the HA log: /api/error_log returned {r.status_code}, "
            f"supervisor /core/logs returned {r2.status_code} (the add-on may need a higher hassio_role)"
        )
