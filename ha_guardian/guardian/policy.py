from __future__ import annotations

import re
from dataclasses import dataclass

# Entities the chat may READ (never control): sensors etc. Controlling still needs allowed_domains/confirm_domains.
DEFAULT_READABLE = ["sensor", "binary_sensor", "weather", "person", "sun", "device_tracker", "camera", "zone"]
ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
FORBIDDEN_DATA_KEYS = {"entity_id", "device_id", "area_id", "floor_id", "label_id"}


@dataclass(frozen=True)
class Decision:
    verdict: str  # allow | confirm | deny
    reason: str = ""


class Policy:
    """Deterministic allowlist for what the control agent may touch. The LLM cannot override it."""

    def __init__(self, cfg: dict):
        self.allowed_domains = set(cfg.get("allowed_domains", []))
        self.confirm_domains = set(cfg.get("confirm_domains", []))
        self.confirm_entities = set(cfg.get("confirm_entities", []))
        self.blocked = set(cfg.get("blocked_entities", []))
        self.readable_domains = set(cfg.get("readable_domains", DEFAULT_READABLE))
        self.allowed_services = set(cfg.get("allowed_services", []))
        self.max_targets = int(cfg.get("max_targets", 10))

    def visible(self, entity_id: str) -> bool:
        if entity_id in self.blocked:
            return False
        domain = entity_id.split(".", 1)[0]
        return (
            domain in self.allowed_domains
            or domain in self.confirm_domains
            or entity_id in self.confirm_entities
        )

    def readable(self, entity_id: str) -> bool:
        """May the agent see this entity's state? (Seeing is wider than controlling.)"""
        if entity_id in self.blocked:
            return False
        return self.visible(entity_id) or entity_id.split(".", 1)[0] in self.readable_domains

    def check(self, domain: str, service: str, entity_ids: list[str], data: dict | None = None) -> Decision:
        if not entity_ids:
            return Decision("deny", "at least one explicit entity_id is required")
        if len(entity_ids) > self.max_targets:
            return Decision("deny", f"too many targets (max {self.max_targets})")
        if data and FORBIDDEN_DATA_KEYS & set(data):
            return Decision("deny", "targets must be passed via entity_ids only")
        if service not in self.allowed_services:
            return Decision("deny", f"service '{service}' is not allowed")
        verdict = "allow"
        for eid in entity_ids:
            if not isinstance(eid, str) or not ENTITY_RE.match(eid):
                return Decision("deny", f"invalid entity id {eid!r}")
            if eid.split(".", 1)[0] != domain:
                return Decision("deny", f"{eid} does not belong to domain '{domain}'")
            if not self.visible(eid):
                return Decision("deny", f"{eid} is not in the allowlist")
            if domain in self.confirm_domains or eid in self.confirm_entities:
                verdict = "confirm"
        return Decision(verdict, "needs owner approval" if verdict == "confirm" else "ok")
