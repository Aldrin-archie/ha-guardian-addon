from __future__ import annotations

import argparse
import logging
import threading

from . import config
from .actions import ActionRunner
from .audit import Audit
from .channel import CompanionChannel, ConsoleChannel, HAChannel, TelegramChannel, ws_url_from
from .control_agent import ControlAgent
from .detectors import ArpWatcher, ConnectivityWatcher, EntityWatcher, HALoginDetector, HealthWatcher, HostAuthDetector
from .events import EventStore
from .ha import HAClient
from .monitor import Monitor
from .policy import Policy
from .triage_agent import TriageAgent


def make_channel(s: config.Settings, interactive: bool = True):
    if s.telegram_token and s.telegram_chat_id:
        return TelegramChannel(s.telegram_token, s.telegram_chat_id)
    return ConsoleChannel(interactive=interactive)


def make_client():
    import anthropic

    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


def build_detectors(s: config.Settings, ha: HAClient) -> list:
    m = s.monitor
    lg = m.get("ha_logins", {})
    dets = [
        HALoginDetector(ha, window_s=lg.get("window_s", 600), threshold=lg.get("threshold", 5)),
        EntityWatcher(ha, s.data_dir / "entities.json", m.get("entities", {}).get("permit_join_entities", [])),
    ]
    health = m.get("health", {})
    if health.get("enabled", True):
        dets.append(HealthWatcher(ha, health))
    conn = m.get("connectivity", {})
    if conn.get("enabled", True):
        dets.append(ConnectivityWatcher(conn.get("targets"), conn.get("gateway") or None))
    arp = m.get("arp", {})
    if arp.get("enabled"):
        dets.append(ArpWatcher(s.data_dir / "known_macs.json", arp.get("scan_cidr", ""), arp.get("known_macs", [])))
    ha_cfg = m.get("host_auth", {})
    if ha_cfg.get("enabled"):
        dets.append(HostAuthDetector(ha_cfg.get("path", "/var/log/auth.log"), ha_cfg.get("window_s", 600), ha_cfg.get("threshold", 5)))
    return dets


def build_monitor(s: config.Settings, channel, use_llm: bool = True) -> Monitor:
    ha = HAClient(s.ha_url, s.monitor_token)
    audit = Audit(s.data_dir / "audit.jsonl")
    store = EventStore(s.data_dir / "events.db")
    triage = None
    if use_llm:
        actions = ActionRunner(ha, channel, audit, s.actions)
        triage = TriageAgent(make_client(), s.triage_model, ha, channel, actions, audit)
    return Monitor(build_detectors(s, ha), store, channel, triage)


def build_control(s: config.Settings, channel) -> ControlAgent:
    return ControlAgent(
        make_client(), s.control_model, HAClient(s.ha_url, s.control_token), Policy(s.control), channel,
        Audit(s.data_dir / "audit.jsonl"), int(s.control.get("approval_timeout_s", 120)),
    )


# ---------------------------------------------------------------- commands
def cmd_check(s: config.Settings) -> None:
    ctl, mon = HAClient(s.ha_url, s.control_token), HAClient(s.ha_url, s.monitor_token)
    pol = Policy(s.control)
    states = ctl.states()
    visible = [x["entity_id"] for x in states if pol.visible(x["entity_id"])]
    print(f"✓ control token works: {len(states)} entities, {len(visible)} visible to the agent")
    try:
        log = mon.error_log()
        hits = [l for l in log.splitlines() if "authentication" in l.lower() or "Banned IP" in l]
        print(f"✓ monitor token can read the HA log ({len(log.splitlines())} lines, {len(hits)} auth-related)")
        for l in hits[-3:]:
            print("   sample:", l[:160])
        print("   Check the samples match the patterns in guardian/detectors/ha_logins.py")
    except Exception as e:
        print(f"✗ monitor token cannot read /api/error_log: {e}")
        print("   Use an admin user's token for HA_MONITOR_TOKEN, and make sure HA logs to file.")
    print("Telegram:", "configured" if s.telegram_token and s.telegram_chat_id else "not configured (console only)")


def cmd_baseline(s: config.Settings) -> None:
    ch = ConsoleChannel(interactive=False)
    mon = build_monitor(s, ch, use_llm=False)
    mon.tick()
    print("Baselines recorded (entities, LAN devices). Future new devices will raise events.")


def cmd_chat(s: config.Settings) -> None:
    ch = ConsoleChannel(interactive=True)
    agent = build_control(s, ch)
    print("Home control chat. Ctrl-C to quit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if text:
            print("agent>", agent.chat(text))


def cmd_monitor(s: config.Settings, use_llm: bool = True, channel=None) -> None:
    ch = channel or make_channel(s, interactive=False)
    ch.start()
    mon = build_monitor(s, ch, use_llm)
    ch.send("🛡️ Guardian monitor started.")
    mon.run_forever(int(s.monitor.get("interval_s", 60)))


def cmd_run(s: config.Settings) -> None:
    """Monitor in a background thread + Telegram chat control in the foreground."""
    if not (s.telegram_token and s.telegram_chat_id):
        raise SystemExit("`run` needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Use `chat` or `monitor` instead.")
    ch = TelegramChannel(s.telegram_token, s.telegram_chat_id)
    ch.start()
    mon = build_monitor(s, ch)
    threading.Thread(target=mon.run_forever, args=(int(s.monitor.get("interval_s", 60)),), daemon=True).start()
    agent = build_control(s, ch)
    store = mon.store
    ch.send("🛡️ Guardian online. Chat to control the house. /status for recent security events.")
    while True:
        text = ch.inbox.get()
        if text.strip() == "/status":
            ev = store.recent(24, 8)
            ch.send("\n".join(f"[{e['severity']}] {e['summary']}" for e in ev) or "No security events in the last 24h.")
            continue
        try:
            ch.send(agent.chat(text) or "(no reply)")
        except Exception as e:
            ch.send(f"Error: {e}")


def _install_dashboard(s: config.Settings) -> None:
    from . import dashboard

    try:
        print(dashboard.install(HAClient(s.ha_url, s.monitor_token), ws_url_from(s.ha_url), s.monitor_token), flush=True)
    except Exception as e:  # never let a cosmetic feature stop the security monitor
        print(f"(could not build the Guardian dashboard: {e})", flush=True)


def _run_cameras(s: config.Settings, ch) -> None:
    from .camera import CameraWatcher, fetch_registry

    cfg = s.monitor.get("cameras", {}) or {}
    try:
        CameraWatcher(
            HAClient(s.ha_url, s.monitor_token, timeout=20), make_client(), cfg.get("model") or s.triage_model, ch, cfg,
            registry_fn=lambda: fetch_registry(ws_url_from(s.ha_url), s.monitor_token),
            logfile=s.data_dir / "camera_checks.jsonl",
        ).run()
    except Exception as e:
        print(f"(camera watcher stopped: {e})", flush=True)


def _check_key(s: config.Settings, ch) -> None:
    """Tell the owner clearly, once, if the Anthropic key does not work."""
    import anthropic

    try:
        make_client().messages.create(model=s.triage_model, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        print("Anthropic API key works.", flush=True)
    except anthropic.AuthenticationError:
        ch.send("🔑 Anthropic rejected the API key in the HA Guardian add-on, so camera checks, AI triage and chat won't work. "
                "Create a key at console.anthropic.com > API keys (it starts with sk-ant-api03-), paste it into the add-on's "
                "Configuration tab, save, and restart.")
    except anthropic.PermissionDeniedError:
        ch.send("🔑 Anthropic says this API key isn't allowed to use the model. Check the key's workspace and permissions.")
    except anthropic.APIStatusError as e:
        if e.status_code == 400 and "credit" in str(e).lower():
            ch.send("💳 Your Anthropic account is out of credit. Add credit at console.anthropic.com > Billing; "
                    "camera checks, AI triage and chat are paused until then.")
        else:
            print(f"(API key check: {e})", flush=True)
    except Exception as e:  # network trouble etc: not the key's fault
        print(f"(API key check skipped: {e})", flush=True)


def _run_webchat(s: config.Settings) -> None:
    from .webchat import ChatApp, WebChannel, make_server

    try:
        wc = WebChannel()
        make_server(ChatApp(build_control(s, wc), wc)).serve_forever()
    except Exception as e:
        print(f"(chat panel stopped: {e})", flush=True)


def cmd_addon(s: config.Settings) -> None:
    """Entry point inside the Home Assistant add-on container."""
    if s.create_dashboard:
        threading.Thread(target=_install_dashboard, args=(s,), daemon=True).start()
    if s.telegram_token and s.telegram_chat_id:
        cmd_run(s)  # Telegram chat control + monitor
    else:
        print(
            "Telegram not configured: running the security monitor only. "
            "Alerts go to Home Assistant and to your phone via the Companion app.",
            flush=True,
        )
        ch = CompanionChannel(HAClient(s.ha_url, s.monitor_token), ws_url_from(s.ha_url), s.monitor_token, s.notify_service)
        ch.start()
        threading.Thread(target=_check_key, args=(s, ch), daemon=True, name="keycheck").start()
        threading.Thread(target=_run_webchat, args=(s,), daemon=True, name="webchat").start()
        if (s.monitor.get("cameras", {}) or {}).get("enabled", True):
            threading.Thread(target=_run_cameras, args=(s, ch), daemon=True, name="cameras").start()
        cmd_monitor(s, channel=ch)


def main() -> None:
    p = argparse.ArgumentParser(prog="guardian")
    p.add_argument("command", choices=["check", "baseline", "chat", "monitor", "monitor-nollm", "run", "addon"])
    p.add_argument("--config", default="config.yaml")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.command == "addon":
        cmd_addon(config.load_addon())
        return
    s = config.load(a.config)
    {
        "check": lambda: cmd_check(s),
        "baseline": lambda: cmd_baseline(s),
        "chat": lambda: cmd_chat(s),
        "monitor": lambda: cmd_monitor(s),
        "monitor-nollm": lambda: cmd_monitor(s, use_llm=False),
        "run": lambda: cmd_run(s),
    }[a.command]()
