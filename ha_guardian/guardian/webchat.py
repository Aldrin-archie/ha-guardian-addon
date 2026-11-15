"""Chat panel inside Home Assistant (add-on Ingress): talk to the control agent from the sidebar or the phone app.

Security: the add-on uses host networking, so this port is reachable from the LAN. Requests are therefore accepted ONLY
from the Supervisor's ingress gateway (which has already checked the person's Home Assistant login and admin rights).
Approvals are buttons in this page; they are answered by a human click, never by the model.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

INGRESS_IP = "172.30.32.2"
MAX_TEXT = 2000


class WebChannel:
    """Channel for the control agent: keeps the transcript and pending approvals."""

    def __init__(self):
        self.msgs: list[dict] = []
        self.pending: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self) -> None:
        pass

    def add(self, role: str, text: str, **extra) -> None:
        with self.lock:
            self.msgs.append({"id": len(self.msgs) + 1, "role": role, "text": text, **extra})

    def send(self, text: str) -> None:
        self.add("assistant", text)

    def ask_approval(self, text: str, timeout: int = 120) -> bool:
        aid = uuid.uuid4().hex[:10]
        p = {"event": threading.Event(), "result": False}
        with self.lock:
            self.pending[aid] = p
        self.add("approval", text, approval_id=aid)
        answered = p["event"].wait(timeout)
        with self.lock:
            self.pending.pop(aid, None)
        if not answered:
            self.add("assistant", "Approval timed out. Action NOT taken.")
            return False
        return bool(p["result"])

    def answer(self, aid: str, ok: bool) -> bool:
        with self.lock:
            p = self.pending.get(aid)
        if not p:
            return False
        p["result"] = ok
        p["event"].set()
        self.add("assistant", "Approved." if ok else "Denied.")
        return True

    def snapshot(self, since: int) -> dict:
        with self.lock:
            return {"messages": [m for m in self.msgs if m["id"] > since], "pending": list(self.pending)}


class ChatApp:
    def __init__(self, agent, channel: WebChannel):
        self.agent, self.channel = agent, channel
        self.busy = False
        self._lock = threading.Lock()

    def send(self, text: str) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.busy = True
        self.channel.add("user", text)
        threading.Thread(target=self._run, args=(text,), daemon=True).start()
        return True

    def _run(self, text: str) -> None:
        try:
            reply = self.agent.chat(text) or "(no reply)"
        except Exception as e:
            reply = f"Error: {e}"
        self.channel.add("assistant", reply)
        with self._lock:
            self.busy = False


def make_server(app: ChatApp, host: str = "0.0.0.0", port: int = 8099, allowed: tuple[str, ...] = (INGRESS_IP,)):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the add-on log clean
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _ok(self) -> bool:
            if self.client_address[0] not in allowed:
                self._send(403, b'{"error":"forbidden"}')
                return False
            return True

        def do_GET(self):
            if not self._ok():
                return
            u = urlparse(self.path)
            path = u.path.rstrip("/")
            if path in ("", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/poll":
                try:
                    since = int(parse_qs(u.query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                snap = app.channel.snapshot(since)
                snap["busy"] = app.busy
                self._send(200, json.dumps(snap).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self._ok():
                return
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, b'{"error":"json only"}')
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 8192:
                    return self._send(413, b'{"error":"too large"}')
                data = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError):
                return self._send(400, b'{"error":"bad request"}')
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/send":
                text = str(data.get("text", "")).strip()[:MAX_TEXT]
                if not text:
                    return self._send(400, b'{"error":"empty"}')
                if not app.send(text):
                    return self._send(409, b'{"error":"busy"}')
                self._send(200, b'{"ok":true}')
            elif path == "/api/approve":
                ok = app.channel.answer(str(data.get("id", "")), data.get("approve") is True)
                self._send(200 if ok else 404, json.dumps({"ok": ok}).encode())
            else:
                self._send(404, b'{"error":"not found"}')

    return ThreadingHTTPServer((host, port), H)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Guardian Chat</title><style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1b1b1f;--card:#f1f2f5;--me:#0b6bcb;--line:#d5d7dd}
@media(prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e6e7eb;--card:#1e2128;--me:#2b7fd6;--line:#343944}}
*{box-sizing:border-box}body{margin:0;font:16px system-ui,sans-serif;background:var(--bg);color:var(--fg);display:flex;flex-direction:column;height:100vh}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;max-width:760px;width:100%;margin:0 auto}
.m{padding:10px 14px;border-radius:14px;max-width:85%;white-space:pre-wrap;word-wrap:break-word;line-height:1.4}
.assistant{background:var(--card);align-self:flex-start}.user{background:var(--me);color:#fff;align-self:flex-end}
.approval{background:var(--card);border:2px solid #d98e04;align-self:flex-start}
.approval button{margin:10px 8px 0 0;padding:8px 18px;border-radius:8px;border:0;font-size:15px;cursor:pointer}
.yes{background:#1a8f4c;color:#fff}.no{background:#c63b3b;color:#fff}
form{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line);max-width:760px;width:100%;margin:0 auto}
input{flex:1;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--fg);font-size:16px}
button.send{padding:0 20px;border-radius:10px;border:0;background:var(--me);color:#fff;font-size:16px}
#hint{color:#888;text-align:center;padding:30px 10px}
</style></head><body><div id="log"><div id="hint">Ask about your home, e.g. "what's the temperature in the lounge?" or "turn off the porch light".</div></div>
<form id="f"><input id="t" autocomplete="off" placeholder="Message Guardian..." maxlength="2000"><button class="send">Send</button></form>
<script>
let last=0,pending=[],busy=false;const log=document.getElementById('log');
function render(m){const h=document.getElementById('hint');if(h)h.remove();const d=document.createElement('div');d.className='m '+m.role;d.id='m'+m.id;d.textContent=m.text;
 if(m.role==='approval'){d.dataset.aid=m.approval_id;const y=document.createElement('button');y.className='yes';y.textContent='Approve';y.onclick=()=>ans(m.approval_id,true);
 const n=document.createElement('button');n.className='no';n.textContent='Deny';n.onclick=()=>ans(m.approval_id,false);d.append(document.createElement('br'),y,n);}
 log.append(d);log.scrollTop=log.scrollHeight}
async function ans(id,ok){await fetch('api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,approve:ok})});poll()}
async function poll(){try{const r=await fetch('api/poll?since='+last);const j=await r.json();j.messages.forEach(m=>{render(m);last=m.id});pending=j.pending;busy=j.busy;
 document.querySelectorAll('.approval').forEach(e=>{if(!pending.includes(e.dataset.aid))e.querySelectorAll('button').forEach(b=>b.remove())})}catch(e){}}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const t=document.getElementById('t');const v=t.value.trim();if(!v)return;t.value='';
 const r=await fetch('api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:v})});
 if(r.status===409)render({id:'x'+Date.now(),role:'assistant',text:'Still working on the last message, one moment.'});poll()};
setInterval(poll,1000);poll();
</script></body></html>"""
