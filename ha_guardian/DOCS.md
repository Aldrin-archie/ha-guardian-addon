# HA Guardian

Control your home from Telegram with Claude, and get alerts when something looks like an attack.

## Configuration tab
| option | what to put |
|---|---|
| `anthropic_api_key` | API key from the Claude Console (required). |
| `telegram_bot_token` | Token from @BotFather (optional but needed for chat and for approving actions). |
| `telegram_chat_id` | Your own Telegram chat id. Only this chat is obeyed. |
| `control_model` / `triage_model` | Leave blank for defaults (Sonnet for chat, Haiku for triage). |
| `device_health_alerts` | Default off. Turn on to be told about offline devices, low batteries and low printer ink. |
| `internet_alerts` | Default off. Turn on to be told when the internet or router goes down and comes back. |
| `mobile_notify_service` | Optional. Phones to alert, comma separated, e.g. `mobile_app_my_phone, mobile_app_partner_phone`. The FIRST gets alerts and Approve/Deny buttons; the rest get alerts only. Needed if several phones are registered. |

## Guardian Chat panel
A **Guardian Chat** entry appears in the Home Assistant sidebar (and the phone app, admins only). Type things like
"what's the temperature in the lounge?" or "turn off the porch light". It can read sensors, and control only what the
allowlist in `/addon_configs/.../config.yaml` permits. Locks and the alarm show Approve / Deny buttons in the chat.
The chat page only accepts requests that come through Home Assistant's own login (Ingress).

## Camera person detection
When a camera's motion sensor fires, the add-on takes a few snapshots and asks Claude whether a real person is there.
People get a push with a one-line description and the camera picture. Leaves, shadows, rain and (by default) animals are
logged quietly in `camera_checks.jsonl` and never pushed. Cameras and motion sensors are found automatically; a camera with no motion sensor in Home Assistant is watched by comparing snapshots every 2 s (only a big change wakes Claude). Snapshots
are sent to Anthropic's API for this check. Limits: 45 s between checks per camera and 200 checks per day (changeable
under `monitor.cameras` in the config). Add `people_notes` there to get "probably Grandpa" style hints; these are
guesses from your wording, never face recognition.

## Guardian dashboard
On every start the add-on builds a **Guardian** dashboard (sidebar) from your real devices: Home (people, climate, lights,
switches), Security (cameras, door/motion sensors, locks), Climate (24 h graphs), and Health (offline devices, batteries
lowest first, printer ink). It only uses built-in cards and never touches your default dashboard. It is rebuilt at each
start, so copy cards into your own dashboard if you want to customise them. Turn it off with `create_dashboard: false`.

## Phone alerts and approvals (Companion app, no Telegram)
Without Telegram the add-on runs the security monitor and sends alerts to the **Home Assistant Companion app**
on your phone (and as a notification inside Home Assistant). Install the Companion app, sign in, and allow notifications.

- If exactly one phone is registered, it is found automatically. If several are, set `mobile_notify_service`
  (find names under Developer tools > Actions, search `notify.mobile_app`; give it without the `notify.` part).
- Actions that need your approval (block IP, lockdown scene) arrive as a notification with **Approve** and **Deny**
  buttons (on iPhone, long-press the notification to see them). No tap in time means NOT approved.
- Chatting to control the house is not part of this mode; use Home Assistant's own Assist chat for that.

## Policy and detector settings
On first start the add-on creates `config.yaml` in its config folder (in the Studio Code Server or
File Editor add-ons, look for `addon_configs/<id>_ha_guardian/config.yaml`). Edit it to choose what the agent may
control, which entities need approval, and which detectors are on. Restart the add-on after changes.

## First run
Watch the Log tab. The first start records a baseline of existing entities, so only new ones raise alerts.
You should see "Guardian online" in Telegram.

## Security notes
- The add-on uses the Supervisor's token, which is powerful. The allowlist in `config.yaml` is what limits the
  chat agent, so review it before enabling Telegram.
- Leave locks, the alarm and the garage on the approval list.
