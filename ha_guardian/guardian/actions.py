from __future__ import annotations

import ipaddress
import subprocess

from .audit import Audit

AUTO, APPROVE = "auto", "approve"
ALLOWED_TOGGLE_DOMAINS = {"switch", "input_boolean"}


class ActionRunner:
    """The only things the security agent can DO. Two tiers:

    AUTO     harmless/reversible: disable Zigbee permit-join, HA persistent notification
    APPROVE  disruptive: block an IP, activate a lockdown scene. Needs the owner's YES.
    """

    TIERS = {
        "disable_zigbee_permit_join": AUTO,
        "create_ha_notification": AUTO,
        "block_ip": APPROVE,
        "activate_lockdown_scene": APPROVE,
    }

    def __init__(self, ha, channel, audit: Audit, cfg: dict):
        self.ha, self.channel, self.audit, self.cfg = ha, channel, audit, cfg
        self.timeout = int(cfg.get("approval_timeout_s", 300))

    def run(self, action: str, params: dict, reason: str) -> str:
        tier = self.TIERS.get(action)
        if tier is None:
            return f"ERROR: unknown action '{action}'. Available: {', '.join(self.TIERS)}"
        if tier == APPROVE:
            ok = self.channel.ask_approval(
                f"Security agent proposes: {action} {params}\nReason: {reason}", self.timeout
            )
            self.audit.log("action_approval", action=action, params=params, reason=reason, approved=ok)
            if not ok:
                return "NOT APPROVED by owner; action not taken"
        try:
            result = getattr(self, f"_{action}")(params)
        except Exception as e:
            self.audit.log("action_failed", action=action, params=params, error=str(e))
            return f"ERROR: {e}"
        self.audit.log("action_executed", action=action, params=params, reason=reason, tier=tier, result=result)
        return result

    # -- AUTO ---------------------------------------------------------------
    def _disable_zigbee_permit_join(self, params: dict) -> str:
        ents = self.cfg.get("permit_join_entities") or []
        if not ents:
            return "no permit_join_entities configured; nothing to do"
        done = []
        for eid in ents:
            domain = eid.split(".", 1)[0]
            if domain in ALLOWED_TOGGLE_DOMAINS:
                self.ha.call_service(domain, "turn_off", {"entity_id": eid})
                done.append(eid)
        return f"permit-join turned off on: {', '.join(done) or 'none (unsupported entity domains)'}"

    def _create_ha_notification(self, params: dict) -> str:
        self.ha.call_service(
            "persistent_notification", "create",
            {"title": str(params.get("title", "Security alert"))[:80], "message": str(params.get("message", ""))[:500]},
        )
        return "persistent notification created in HA"

    # -- APPROVE ------------------------------------------------------------
    def _block_ip(self, params: dict) -> str:
        ip = ipaddress.ip_address(str(params.get("ip", "")).strip())
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return f"REFUSED: {ip} is not a public address; isolate LAN devices manually"
        template = self.cfg.get("block_ip_command") or []
        if not template:
            return f"no block_ip_command configured; owner should block {ip} on the router/firewall manually"
        argv = [part.replace("{ip}", str(ip)) for part in template]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return f"block command exited {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}"

    def _activate_lockdown_scene(self, params: dict) -> str:
        scene = self.cfg.get("lockdown_scene")
        if not scene:
            return "no lockdown_scene configured"
        self.ha.call_service("scene", "turn_on", {"entity_id": scene})
        return f"activated {scene}"
