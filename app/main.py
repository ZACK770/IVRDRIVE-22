"""Raw-channel probe for the Technoline PBX streaming channel.

The streaming ("raw") channel is undocumented: the wire format, framing and
handshake are unknown. This service accepts the connection unconditionally,
never rejects, never assumes an encoding, and records everything so a single
real test call is enough to pin the protocol down.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.websockets import WebSocketDisconnect

from app import (
    admin,
    api,
    bridge,
    capture,
    codecs,
    console_proxy,
    db,
    ivr,
    ops_api,
    pbx,
    public_api,
    scheduler,
)
from mortgage_refinance.app import app as mortgage_app
from mortgage_refinance.app import initialize_database as initialize_mortgage_database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("probe")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    initialize_mortgage_database()
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="Technoline raw-channel probe", lifespan=lifespan)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(public_api.router)
app.include_router(ops_api.router)
app.include_router(ivr.router)
app.include_router(console_proxy.router)
app.mount("/mortgage", mortgage_app)


_DRIVER_REGISTER_PAGE = """<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>רישום נהג — דרייברים</title>
<style>
:root{--bg:#f8fafc;--panel:#fff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;--accent:#2563eb;--accent-dark:#1d4ed8;--danger:#dc2626;--success:#16a34a}
*{box-sizing:border-box;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
body{background:var(--bg);color:var(--text);margin:0;padding:1rem;line-height:1.6}
.container{max-width:640px;margin:0 auto}
.card{background:var(--panel);border:1px solid var(--border);border-radius:1rem;padding:1.5rem;box-shadow:0 4px 6px -1px rgb(0 0 0 / 0.05)}
h1{margin-top:0;font-size:1.5rem}
.muted{color:var(--muted);font-size:.9rem}
label{display:block;margin:.75rem 0 .25rem;font-weight:600}
input[type="text"],input[type="tel"],input[type="number"]{width:100%;padding:.65rem .9rem;border:1px solid var(--border);border-radius:.5rem;font-size:1rem}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgb(37 99 235 / 0.1)}
.areas{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:.5rem;margin-top:.5rem}
.area-chip{cursor:pointer;border:1px solid var(--border);border-radius:999px;padding:.45rem .8rem;text-align:center;background:#fff;transition:.15s}
.area-chip.selected{border-color:var(--accent);background:#eff6ff;color:var(--accent-dark)}
.terms{border:1px solid var(--border);border-radius:.5rem;padding:1rem;background:#f9fafb;margin:1rem 0}
.terms h2{font-size:1.1rem;margin-top:0}
.terms ul{padding-right:1.2rem;margin:.5rem 0}
.checkbox{display:flex;align-items:flex-start;gap:.5rem;margin:.6rem 0;cursor:pointer;font-weight:normal}
.checkbox input{margin-top:.25rem;flex-shrink:0}
button{width:100%;padding:.85rem;border:none;border-radius:.5rem;background:var(--accent);color:#fff;font-size:1.05rem;font-weight:600;cursor:pointer;margin-top:1rem}
button:disabled{background:#94a3b8;cursor:not-allowed}
.feedback{margin-top:1rem;padding:.8rem;border-radius:.5rem;text-align:center;display:none}
.feedback.error{background:#fef2f2;color:var(--danger);border:1px solid #fecaca}
.feedback.success{background:#f0fdf4;color:var(--success);border:1px solid #bbf7d0}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>רישום נהג</h1>
    <p class="muted">מלאו את הפרטים הבאים. הרישום נכנס לאישור משרד ורק לאחריו תוכלו לקבל הצעות נסיעה.</p>
    <form id="regForm">
      <label for="phone">טלפון נייד *</label>
      <input id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="0501234567" required />

      <label for="name">שם מלא *</label>
      <input id="name" type="text" autocomplete="name" placeholder="ישראל ישראלי" required />

      <label for="car_model">דגם הרכב *</label>
      <input id="car_model" type="text" placeholder="לדוגמה: Mercedes Vito" required list="carModels" />
      <datalist id="carModels"><option value="Mercedes Vito" /><option value="Volkswagen Caravelle" /><option value="Toyota Proace" /></datalist>

      <label for="seats">מספר מושבים *</label>
      <input id="seats" type="number" min="1" step="1" value="4" required />

      <label>אזורי פעילות *</label>
      <div id="areas" class="areas"><span class="muted">טוען אזורים...</span></div>

      <div class="terms">
        <h2>תנאי הצטרפות</h2>
        <ul>
          <li>אני מצהיר שבעבודתי כנהג/מפעיל רכב לצורך הסעות באמצעות המערכת, יש לי את כל המסמכים, הרשיונות, ההסמכות, הביטוחים והאישורים הנדרשים על פי חוק.</li>
          <li>אני מבין שהמערכת היא פלטפורמת תיווך בלבד ואינה נושאת באחריות לפעילות הנסיעה עצמה; האחריות לביצוע נסיעות חוקי ובטוח מוטלת עליי בלבד.</li>
          <li>אני מודע ומתחייב שלא לקחת יותר משתי נסיעות שיתופיות ביום, ולעקוב אחר כל הגבלה חוקית רלוונטית.</li>
        </ul>
        <label class="checkbox"><input type="checkbox" id="has_documents" required /><span>אני מאשר/ת שיש לי את כל המסמכים, הרשיונות, ההסמכות והביטוחים הדרושים.</span></label>
        <label class="checkbox"><input type="checkbox" id="accepts_limit" required /><span>אני מודע/ת ומתחייב/ת שלא לקחת יותר משתי נסיעות שיתופיות ביום ולעקוב אחרי ההגבלות החוקיות.</span></label>
        <label class="checkbox"><input type="checkbox" id="terms_accepted" required /><span>קראתי את התקנון ואני מסכים/מה לתנאיו, כולל פטור המערכת מאחריות.</span></label>
      </div>

      <button type="submit" id="submitBtn">שלח הרשמה</button>
      <div id="feedback" class="feedback"></div>
    </form>
  </div>
</div>
<script>
let selectedAreas = [];
async function loadAreas(){
  const res = await fetch('/api/public/areas');
  const data = await res.json();
  const container = document.getElementById('areas');
  container.innerHTML = '';
  if(!data.areas || data.areas.length === 0){ container.innerHTML = '<span class="muted">אין אזורים זמינים כרגע.</span>'; return; }
  data.areas.forEach(name => {
    const chip = document.createElement('div');
    chip.className = 'area-chip';
    chip.textContent = name;
    chip.onclick = () => {
      chip.classList.toggle('selected');
      if(selectedAreas.includes(name)) selectedAreas = selectedAreas.filter(a => a !== name);
      else selectedAreas.push(name);
    };
    container.appendChild(chip);
  });
}
function showFeedback(text, ok){
  const fb = document.getElementById('feedback');
  fb.textContent = text;
  fb.className = 'feedback ' + (ok ? 'success' : 'error');
  fb.style.display = 'block';
}
function normalizePhone(v){ return v.replace(/\\D/g,''); }
document.getElementById('regForm').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const phone = normalizePhone(document.getElementById('phone').value);
  if(phone.length < 9 || phone.length > 11){ showFeedback('יש להזין מספר טלפון תקין.', false); return; }
  if(selectedAreas.length === 0){ showFeedback('יש לבחור לפחות אזור אחד.', false); return; }
  const btn = document.getElementById('submitBtn');
  btn.disabled = true; btn.textContent = 'שולח...';
  try{
    const res = await fetch('/api/public/drivers', {
      method: 'POST',
      headers: {'content-type':'application/json'},
      body: JSON.stringify({
        phone,
        name: document.getElementById('name').value.trim(),
        car_model: document.getElementById('car_model').value.trim(),
        seats: Number(document.getElementById('seats').value),
        areas: selectedAreas,
        terms_accepted: document.getElementById('terms_accepted').checked,
        has_documents: document.getElementById('has_documents').checked,
        accepts_rides_limit: document.getElementById('accepts_limit').checked,
      })
    });
    const data = await res.json().catch(()=>({}));
    if(res.ok){
      showFeedback(data.message || 'ההרשמה התקבלה. תודה!', true);
      document.getElementById('regForm').reset();
      selectedAreas = [];
      document.querySelectorAll('.area-chip').forEach(c => c.classList.remove('selected'));
    } else {
      showFeedback(data.detail || 'שגיאה בשליחת הטופס. נסו שוב.', false);
    }
  }catch(err){
    showFeedback('שגיאת רשת. נסו שוב.', false);
  } finally { btn.disabled = false; btn.textContent = 'שלח הרשמה'; }
});
loadAreas();
</script>
</body>
</html>"""


@app.get("/register/driver", response_class=HTMLResponse)
def driver_register_page() -> HTMLResponse:
    return HTMLResponse(_DRIVER_REGISTER_PAGE)


#: The console runs as a separate Render service on its own domain, so the API
#: has to name it explicitly. Comma-separated; `*` for local development.
CORS_ORIGINS = [
    origin.strip() for origin in os.getenv("CONSOLE_ORIGINS", "*").split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


#: off | loopback | tone. Loopback proves the outbound direction works without
#: needing to know the codec: if the caller hears themselves, our framing is right.
ECHO_MODE = os.getenv("PROBE_ECHO", "loopback").lower()
ECHO_DELAY_MS = int(os.getenv("PROBE_ECHO_DELAY_MS", "700"))
TONE_CODEC = os.getenv("PROBE_TONE_CODEC", "mulaw").lower()
TONE_EVERY_S = float(os.getenv("PROBE_TONE_EVERY_S", "3"))
#: probe = record only (protocol discovery). ai = answer the call with Gemini Live.
MODE = os.getenv("PROBE_MODE", "probe").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EXPECTED_BEARER = os.getenv("PROBE_BEARER_SECRET")
ENFORCE_BEARER = os.getenv("PROBE_ENFORCE_BEARER", "0") == "1"

SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie"}


def _redact(name: str, value: str) -> str:
    if name.lower() not in SENSITIVE_HEADERS:
        return value
    if len(value) <= 12:
        return "***"
    return f"{value[:8]}…{value[-4:]} (len={len(value)})"


def _handshake_info(ws: WebSocket) -> dict:
    headers = {k.decode(): v.decode() for k, v in ws.scope.get("headers", [])}
    auth = headers.get("authorization", "")
    return {
        "path": ws.scope.get("path"),
        "query_params": dict(ws.query_params),
        "client": f"{ws.client.host}:{ws.client.port}" if ws.client else None,
        "subprotocols": ws.scope.get("subprotocols", []),
        "headers": {k: _redact(k, v) for k, v in headers.items()},
        "bearer_present": auth.lower().startswith("bearer "),
        "bearer_matches_expected": bool(EXPECTED_BEARER and auth[7:].strip() == EXPECTED_BEARER),
    }


def _caller_from_start(text: str) -> str:
    """The PBX announces the caller in its start frame; anything else is ignored."""
    try:
        data = json.loads(text)
    except Exception:
        return ""
    return str(data.get("caller") or "") if isinstance(data, dict) else ""


async def _echo_loopback(ws: WebSocket, cap: capture.CallCapture, kind: str, payload) -> None:
    await asyncio.sleep(ECHO_DELAY_MS / 1000)
    try:
        if kind == "binary":
            await ws.send_bytes(payload)
        else:
            await ws.send_text(payload)
    except Exception as exc:  # connection may already be gone
        log.warning("[%s] echo failed: %s", cap.call_id, exc)
        return
    cap.record("out", kind, payload)


async def _tone_loop(ws: WebSocket, cap: capture.CallCapture) -> None:
    encoders = {
        "mulaw": lambda s: codecs.pcm16_to_ulaw(s),
        "pcm16le": lambda s: b"".join(int(v).to_bytes(2, "little", signed=True) for v in s),
        "pcm16be": lambda s: b"".join(int(v).to_bytes(2, "big", signed=True) for v in s),
    }
    encode = encoders.get(TONE_CODEC, encoders["mulaw"])
    samples = codecs.tone(440, 400)
    payload = encode(samples)
    while True:
        await asyncio.sleep(TONE_EVERY_S)
        try:
            await ws.send_bytes(payload)
        except Exception:
            return
        cap.record("out", "binary", payload)


@app.websocket("/ws/ivr")
async def ws_ivr(ws: WebSocket) -> None:
    await _handle(ws)


@app.websocket("/ws/{rest:path}")
async def ws_any(ws: WebSocket, rest: str) -> None:
    """Catch-all: if the PBX dials a different path, still capture it."""
    await _handle(ws)


async def _handle(ws: WebSocket) -> None:
    info = _handshake_info(ws)
    call_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    if ENFORCE_BEARER and EXPECTED_BEARER and not info["bearer_matches_expected"]:
        log.warning("[%s] rejecting: bearer mismatch", call_id)
        await ws.close(code=1008)
        return

    # Accept any subprotocol the client asks for rather than negotiating none:
    # some telephony stacks abort when their offer is ignored.
    requested = info["subprotocols"]
    await ws.accept(subprotocol=requested[0] if requested else None)

    cap = capture.CallCapture(call_id, info)
    log.info("[%s] connected %s", call_id, json.dumps(info, ensure_ascii=False))

    ai_mode = MODE == "ai" and bool(GEMINI_API_KEY)
    if MODE == "ai" and not GEMINI_API_KEY:
        log.error("[%s] PROBE_MODE=ai but GEMINI_API_KEY is unset; recording only", call_id)

    call: bridge.CallBridge | None = None
    ai_task: asyncio.Task | None = None

    def start_ai(caller: str) -> None:
        """Deferred until the start frame arrives: the caller id shapes the prompt."""
        nonlocal call, ai_task
        call = bridge.CallBridge(ws, cap, GEMINI_API_KEY, caller)
        ai_task = asyncio.create_task(call.run())

    tone_task = (
        asyncio.create_task(_tone_loop(ws, cap)) if ECHO_MODE == "tone" and not ai_mode else None
    )
    echo_tasks: set[asyncio.Task] = set()
    reason = "unknown"

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                reason = f"client disconnect code={message.get('code')}"
                break
            if (payload := message.get("bytes")) is not None:
                kind = "binary"
            elif (payload := message.get("text")) is not None:
                kind = "text"
            else:
                continue

            entry = cap.record("in", kind, payload)
            if cap.inbound_frames <= 5 or kind == "text":
                log.info("[%s] %s", call_id, json.dumps(entry, ensure_ascii=False)[:1200])

            if ai_mode:
                if call is None:
                    start_ai(_caller_from_start(payload) if kind == "text" else "")
                elif kind == "binary":
                    call.feed(payload)
                continue

            if ECHO_MODE == "loopback":
                task = asyncio.create_task(_echo_loopback(ws, cap, kind, payload))
                echo_tasks.add(task)
                task.add_done_callback(echo_tasks.discard)
    except WebSocketDisconnect as exc:
        reason = f"WebSocketDisconnect code={exc.code}"
    except Exception as exc:
        reason = f"error: {type(exc).__name__}: {exc}"
        log.exception("[%s] receive loop failed", call_id)
    finally:
        if ai_task:
            ai_task.cancel()
            if call is not None:
                call.finish()
                log.info(
                    "[%s] ai stats: %s",
                    call_id,
                    json.dumps(call.stats, ensure_ascii=False),
                )
                cap.extra["ai"] = call.stats
        if tone_task:
            tone_task.cancel()
        for task in list(echo_tasks):
            task.cancel()
        summary = cap.close(reason)
        log.info("[%s] closed: %s", call_id, json.dumps(summary, ensure_ascii=False))


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "mode": MODE,
        "echo_mode": ECHO_MODE,
        "gemini_key_present": bool(GEMINI_API_KEY),
        "pbx_dry_run": pbx.DRY_RUN,
        "scheduler": scheduler.ENABLED,
    }


@app.get("/outbound-ip")
async def outbound_ip() -> dict:
    """Return the public IP the server uses for outbound calls."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"could not resolve outbound ip: {exc}"
        ) from exc
    return {"ip": payload.get("ip"), "source": "api.ipify.org"}


@app.api_route("/ws/ivr", methods=["GET", "POST"])
async def ws_probe_over_http(request: Request) -> JSONResponse:
    """The PBX may probe the endpoint over plain HTTP first — log that too."""
    log.info(
        "HTTP hit on websocket path: %s %s headers=%s",
        request.method,
        dict(request.query_params),
        {k: _redact(k, v) for k, v in request.headers.items()},
    )
    return JSONResponse({"status": "ok", "note": "websocket endpoint"}, status_code=200)


AUDIO_DIR = Path(__file__).parent.parent / "audio"


@app.get("/audio/{file_name}")
async def audio_file(file_name: str) -> FileResponse:
    if ".." in file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="bad path")
    path = AUDIO_DIR / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/captures")
async def api_captures() -> list[dict]:
    return capture.list_captures()


@app.get("/api/captures/{call_id}")
async def api_capture(call_id: str, limit: int = 500) -> dict:
    meta = capture.load_capture(call_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="unknown call_id")
    return {"meta": meta, "frames": capture.load_frames(call_id, limit)}


@app.get("/captures/{call_id}/{filename}")
async def download(call_id: str, filename: str) -> FileResponse:
    if "/" in filename or ".." in filename or "/" in call_id or ".." in call_id:
        raise HTTPException(status_code=400, detail="bad path")
    path = Path(capture.CAPTURE_ROOT) / call_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    rows = []
    for meta in capture.list_captures():
        verdict = (meta.get("codec_verdict") or {}).get("best_guess", "—")
        rows.append(
            f"<tr><td><a href='/call/{meta['call_id']}'>{meta['call_id']}</a></td>"
            f"<td>{meta.get('duration_s')}s</td>"
            f"<td>{meta.get('inbound_frames')} / {meta.get('outbound_frames')}</td>"
            f"<td>{meta.get('inbound_bytes')}</td>"
            f"<td>{json.dumps(meta.get('frame_kinds', {}))}</td>"
            f"<td><b>{verdict}</b></td></tr>"
        )
    body = "".join(rows) or "<tr><td colspan=6>אין עדיין שיחות. בצע שיחת בדיקה.</td></tr>"
    return f"""<!doctype html><html dir=rtl lang=he><meta charset=utf-8>
<title>Raw channel probe</title>
<h2>Technoline raw-channel probe</h2>
<p>echo mode: <code>{ECHO_MODE}</code></p>
<table border=1 cellpadding=4>
<tr><th>call</th><th>משך</th><th>frames in/out</th><th>bytes in</th>
<th>kinds</th><th>codec guess</th></tr>
{body}</table></html>"""


@app.get("/call/{call_id}", response_class=HTMLResponse)
async def call_page(call_id: str) -> str:
    meta = capture.load_capture(call_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="unknown call_id")
    frames = capture.load_frames(call_id, 400)
    frame_rows = "".join(
        f"<tr><td>{f['seq']}</td><td>{f['dir']}</td><td>{f['kind']}</td>"
        f"<td>{f['t_ms']}</td><td>{f.get('dt_ms', '')}</td><td>{f['bytes']}</td>"
        f"<td><code>{f.get('hex_head', '')}</code></td>"
        f"<td><code>{(f.get('text') or f.get('ascii_head') or '')[:120]}</code></td></tr>"
        for f in frames
    )
    wavs = "".join(
        f"<li><a href='/captures/{call_id}/inbound_as_{name}.wav'>inbound_as_{name}.wav</a></li>"
        for name in ("pcm16le", "pcm16be", "mulaw", "alaw")
    )
    return f"""<!doctype html><html dir=ltr lang=he><meta charset=utf-8>
<title>{call_id}</title>
<p><a href="/">← back</a></p>
<h2>{call_id}</h2>
<h3>summary</h3><pre>{json.dumps(meta, ensure_ascii=False, indent=2)}</pre>
<h3>audio renderings (listen: only one will sound like speech)</h3><ul>{wavs}</ul>
<p><a href="/captures/{call_id}/inbound.bin">inbound.bin</a> ·
<a href="/captures/{call_id}/frames.jsonl">frames.jsonl</a></p>
<h3>frames</h3>
<table border=1 cellpadding=3>
<tr><th>#</th><th>dir</th><th>kind</th><th>t_ms</th><th>dt_ms</th><th>bytes</th>
<th>hex head</th><th>text/ascii</th></tr>
{frame_rows}</table></html>"""
