from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import sys
import threading
import time

import httpx

log = logging.getLogger("guardian.channel")


class ConsoleChannel:
    """stdout/stdin channel. Approvals are only possible when interactive."""

    def __init__(self, interactive: bool = True):
        self.interactive = interactive and sys.stdin.isatty()
        self.inbox: queue.Queue[str] = queue.Queue()

    def start(self) -> None:
        pass

    def send(self, text: str) -> None:
        print(text, flush=True)

    def ask_approval(self, text: str, timeout: int = 120) -> bool:
        if not self.interactive:
            print(f"[approval required, no interactive channel -> NOT approved]\n{text}", flush=True)
            return False
        ans = input(f"\n!! APPROVAL NEEDED:\n{text}\nApprove? [y/N] ").strip().lower()
        return ans in {"y", "yes"}


class HAChannel:
    """Fallback when Telegram isn't configured: alerts appear as Home Assistant notifications.
    There is no way to answer YES/NO here, so approval-tier actions are never approved."""

    def __init__(self, ha):
        self.ha = ha
        self.inbox: queue.Queue[str] = queue.Queue()

    def start(self) -> None:
        pass

    def send(self, text: str) -> None:
        print(text, flush=True)
        try:
            self.ha.call_service(
                "persistent_notification", "create", {"title": "HA Guardian", "message": text[:1000]}
            )
        except Exception as e:
            print(f"(could not create HA notification: {e})", flush=True)

    def ask_approval(self, text: str, timeout: int = 120) -> bool:
        self.send(f"Approval needed but no Telegram is configured, so this was NOT done:\n{text}")
        return False


class TelegramChannel:
    """Chat + alerts + YES/NO approvals over one Telegram bot, locked to a single chat id.

    A background thread polls getUpdates. A message that arrives while an approval is pending and
    reads yes/no resolves the approval; everything else goes to `inbox` for the chat agent.
    """

    def __init__(self, token: str, chat_id: int):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = int(chat_id)
        self.http = httpx.Client(timeout=45)
        self.inbox: queue.Queue[str] = queue.Queue()
        self._pending: dict | None = None
        self._approval_lock = threading.Lock()
        self._offset = 0

    def _call(self, method: str, **params) -> dict:
        r = self.http.post(f"{self.api}/{method}", json=params)
        r.raise_for_status()
        return r.json()

    def send(self, text: str) -> None:
        for i in range(0, len(text), 3900):
            self._call("sendMessage", chat_id=self.chat_id, text=text[i : i + 3900])

    def start(self) -> None:
        # Skip any backlog so an old "yes" can never be replayed as an approval.
        try:
            res = self._call("getUpdates", offset=-1, timeout=0).get("result", [])
            if res:
                self._offset = res[-1]["update_id"] + 1
        except Exception:
            pass
        threading.Thread(target=self._poll, daemon=True, name="telegram-poll").start()

    def _poll(self) -> None:
        import time

        while True:
            try:
                res = self._call("getUpdates", offset=self._offset, timeout=30).get("result", [])
                for u in res:
                    self._offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if msg.get("chat", {}).get("id") != self.chat_id:
                        continue  # ignore everyone except the owner
                    text = (msg.get("text") or "").strip()
                    if text:
                        self.route(text)
            except Exception:
                time.sleep(5)

    def route(self, text: str) -> None:
        p = self._pending
        if p is not None and text.lower() in {"yes", "y", "no", "n"}:
            p["result"] = text.lower() in {"yes", "y"}
            p["event"].set()
            return
        self.inbox.put(text)

    def ask_approval(self, text: str, timeout: int = 120) -> bool:
        with self._approval_lock:
            p = {"event": threading.Event(), "result": False}
            self._pending = p
            try:
                self.send(f"⚠️ Approval needed:\n{text}\n\nReply YES or NO (expires in {timeout}s).")
                answered = p["event"].wait(timeout)
            finally:
                self._pending = None
            if not answered:
                self.send("Approval timed out. Action NOT taken.")
                return False
            return bool(p["result"])


ACTION_RE = re.compile(r"GUARDIAN_(APPROVE|DENY)_([A-Za-z0-9_-]+)")


def ws_url_from(ha_url: str) -> str:
    """WebSocket endpoint for a given HA base URL (Supervisor proxy inside an add-on)."""
    if "//supervisor/" in ha_url + "/":
        return "ws://supervisor/core/websocket"
    return ha_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


def discover_mobile_services(ha) -> list[str]:
    """Names of notify.mobile_app_* services (one per phone with the Companion app)."""
    for dom in ha.services():
        if dom.get("domain") == "notify":
            return sorted(n for n in dom.get("services", {}) if n.startswith("mobile_app_"))
    return []


class CompanionChannel(HAChannel):
    """Alerts as push notifications to your phone (Home Assistant Companion app) plus a persistent HA notification.

    Approvals: the push carries Approve / Deny buttons. Taps come back as the HA event
    `mobile_app_notification_action`, received over the HA websocket. Each approval uses a random one-time id,
    so a stray or old tap cannot approve anything. Without a phone registered, approvals are never granted.
    """

    def __init__(self, ha, ws_url: str, token: str, service: str | None = None):
        super().__init__(ha)
        self.ws_url, self.token = ws_url, token
        names = [n.strip().removeprefix("notify.") for n in (service or "").split(",") if n.strip()]
        self.service = names[0] if names else None   # the owner's phone: alerts AND approvals
        self.extra = names[1:]                       # other phones: alerts only
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- setup ---------------------------------------------------------------
    def start(self) -> None:
        if getattr(self, "_started", False):
            return
        self._started = True
        if not self.service:
            try:
                found = discover_mobile_services(self.ha)
            except Exception as e:
                found = []
                log.warning("could not list notify services: %s", e)
            if len(found) == 1:
                self.service = found[0]
            elif len(found) > 1:
                log.warning("Several phones found %s. Set mobile_notify_service to the one that should get alerts and approvals.", found)
            else:
                log.warning("No Companion-app phone found (notify.mobile_app_*). Alerts will only appear inside Home Assistant.")
        if self.service:
            log.info("Alerts and approvals: notify.%s. Alerts only: %s", self.service, ", ".join(self.extra) or "none")
            threading.Thread(target=self._ws_loop, daemon=True, name="companion-ws").start()

    def _ws_loop(self) -> None:
        from websockets.sync.client import connect

        while True:
            try:
                with connect(self.ws_url, open_timeout=15) as ws:
                    json.loads(ws.recv())  # auth_required
                    ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    reply = json.loads(ws.recv())
                    if reply.get("type") != "auth_ok":
                        raise RuntimeError(f"websocket auth failed: {reply.get('type')}")
                    ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}))
                    for raw in ws:
                        self._on_message(json.loads(raw))
            except Exception as e:
                log.warning("companion websocket dropped (%s); retrying in 5s", e)
                time.sleep(5)

    def _on_message(self, msg: dict) -> None:
        if msg.get("type") != "event":
            return
        ev = msg.get("event", {})
        if ev.get("event_type") == "mobile_app_notification_action":
            self.handle_action(str(ev.get("data", {}).get("action", "")))

    def handle_action(self, action: str) -> bool:
        m = ACTION_RE.fullmatch(action)
        if not m:
            return False
        with self._lock:
            p = self._pending.get(m.group(2))
        if not p:
            return False  # unknown, expired or already answered
        p["result"] = m.group(1) == "APPROVE"
        p["event"].set()
        return True

    # -- channel API ---------------------------------------------------------
    def _push(self, text: str, data: dict | None = None, everyone: bool = False) -> None:
        if not self.service:
            return
        payload = {"title": "HA Guardian", "message": text[:500]}
        if data:
            payload["data"] = data
        for svc in [self.service] + (self.extra if everyone else []):
            try:
                self.ha.call_service("notify", svc, payload)
            except Exception as e:
                print(f"(could not push to {svc}: {e})", flush=True)

    def send(self, text: str) -> None:
        super().send(text)  # print + persistent HA notification
        self._push(text, everyone=True)

    def send_image(self, text: str, camera_entity: str) -> None:
        """Alert with the camera's live picture attached (Android: image, iOS: attachment)."""
        HAChannel.send(self, text)
        url = f"/api/camera_proxy/{camera_entity}"
        self._push(text, {
            "image": url,
            "attachment": {"url": url, "content-type": "jpeg"},
            "tag": f"guardian-cam-{camera_entity}",
            "push": {"interruption-level": "time-sensitive"},
        }, everyone=True)

    def ask_approval(self, text: str, timeout: int = 120) -> bool:
        if not self.service:
            return super().ask_approval(text, timeout)
        aid = secrets.token_urlsafe(6)
        p = {"event": threading.Event(), "result": False}
        with self._lock:
            self._pending[aid] = p
        try:
            self._push(
                f"Approval needed:\n{text}",
                {
                    "tag": f"guardian-{aid}",
                    "actions": [
                        {"action": f"GUARDIAN_APPROVE_{aid}", "title": "Approve"},
                        {"action": f"GUARDIAN_DENY_{aid}", "title": "Deny", "destructive": True},
                    ],
                },
            )
            answered = p["event"].wait(timeout)
        finally:
            with self._lock:
                self._pending.pop(aid, None)
        self._push("clear_notification", {"tag": f"guardian-{aid}"})
        if not answered:
            self.send("Approval timed out. Action NOT taken.")
            return False
        return bool(p["result"])
