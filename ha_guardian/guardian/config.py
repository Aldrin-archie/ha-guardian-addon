from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    ha_url: str
    control_token: str
    monitor_token: str
    control_model: str
    triage_model: str
    telegram_token: str | None
    telegram_chat_id: int | None
    data_dir: Path
    cfg: dict
    notify_service: str | None = None
    create_dashboard: bool = True

    @property
    def control(self) -> dict:
        return self.cfg.get("control", {})

    @property
    def monitor(self) -> dict:
        return self.cfg.get("monitor", {})

    @property
    def actions(self) -> dict:
        return self.cfg.get("actions", {})


def load_addon(
    options_path: str = "/data/options.json",
    user_cfg: str = "/config/config.yaml",
    default_cfg: str = "/app/default_config.yaml",
    data_dir: str = "/data",
) -> Settings:
    """Settings when running as a Home Assistant add-on.

    HA is reached through the Supervisor proxy with the add-on's own token, so no HA tokens are needed.
    Secrets come from the add-on Configuration tab; the policy/detector config lives in the add-on's
    config folder (/config/config.yaml) where you can edit it.
    """
    opts = json.loads(Path(options_path).read_text())
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SystemExit("SUPERVISOR_TOKEN is missing: the add-on needs homeassistant_api: true.")
    if not opts.get("anthropic_api_key"):
        raise SystemExit("Set anthropic_api_key in the add-on's Configuration tab, then restart.")
    os.environ["ANTHROPIC_API_KEY"] = opts["anthropic_api_key"]

    user = Path(user_cfg)
    if not user.exists():
        user.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(default_cfg, user)
    cfg = yaml.safe_load(user.read_text()) or {}
    models = cfg.get("models", {})
    chat_id = str(opts.get("telegram_chat_id") or "").strip()
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    return Settings(
        ha_url="http://supervisor/core",
        control_token=token,
        monitor_token=token,
        control_model=opts.get("control_model") or models.get("control", "claude-sonnet-5-5"),
        triage_model=opts.get("triage_model") or models.get("triage", "claude-haiku-5-5"),
        telegram_token=opts.get("telegram_bot_token") or None,
        telegram_chat_id=int(chat_id) if chat_id else None,
        data_dir=d,
        cfg=cfg,
        notify_service=(opts.get("mobile_notify_service") or "").strip() or None,
        create_dashboard=opts.get("create_dashboard", True) is not False,
    )


def load(config_path: str = "config.yaml") -> Settings:
    load_dotenv()
    cfg = yaml.safe_load(Path(config_path).read_text()) or {}
    env = os.environ
    missing = [k for k in ("HA_URL", "HA_CONTROL_TOKEN") if not env.get(k)]
    if missing:
        raise SystemExit(f"Missing required settings in .env: {', '.join(missing)}")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    models = cfg.get("models", {})
    data_dir = Path(cfg.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        ha_url=env["HA_URL"],
        control_token=env["HA_CONTROL_TOKEN"],
        monitor_token=env.get("HA_MONITOR_TOKEN") or env["HA_CONTROL_TOKEN"],
        control_model=models.get("control", "claude-sonnet-5-5"),
        triage_model=models.get("triage", "claude-haiku-5-5"),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=int(chat_id) if chat_id else None,
        data_dir=data_dir,
        cfg=cfg,
    )
