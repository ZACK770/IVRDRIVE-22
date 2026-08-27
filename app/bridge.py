"""Feasibility spike: bridge one PBX call to Gemini Live and time everything.

The point of this module is not to be the product. It answers three questions a
real call cannot answer on paper: how fast the bot replies, whether it handles
silence sensibly, and whether the caller can interrupt it mid-sentence.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import random
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

from app import audio, capture, cost, db, notify, prompt, tools
from app.gemini_live import GeminiLiveSession, PREFIX_PADDING_MS, SILENCE_MS

log = logging.getLogger("bridge")

#: PBX framing, discovered empirically: 320 bytes = 160 samples = 20ms @ 8kHz.
FRAME_BYTES = 320
FRAME_S = 0.02
SILENCE_FRAME = b"\x00" * FRAME_BYTES

#: Gemini's server-side VAD needs a steady stream; batching 5 frames keeps the
#: request rate at 10/s instead of 50/s without adding meaningful latency.
INPUT_BATCH_FRAMES = 5
HESITATION_SILENCE_S = float(os.getenv("GEMINI_HESITATION_SILENCE_MS", "300")) / 1000
HESITATION_AUDIO_DIR = Path(__file__).parent.parent / "audio"
HESITATION_FILES = (
    "hesitation_01.wav",
    "hesitation_02.wav",
    "hesitation_03.wav",
)

GREETING = prompt.GREETING

#: The parallel channel transfer frame is not fully documented. The template is
#: injected so the operator can change the wire format without a redeploy; the
#: phone number is the one configured in `representative_extension`.
TRANSFER_FRAME = os.getenv(
    "PBX_TRANSFER_FRAME",
    '{"action":"transfer","phone":"{phone}"}',
)


def _load_hesitation_clips() -> tuple[tuple[bytes, ...], ...]:
    clips: list[tuple[bytes, ...]] = []
    for file_name in HESITATION_FILES:
        path = HESITATION_AUDIO_DIR / file_name
        try:
            with wave.open(str(path), "rb") as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != 8000
                ):
                    raise ValueError("expected mono PCM16LE at 8000Hz")
                payload = source.readframes(source.getnframes())
        except (OSError, ValueError) as exc:
            log.warning("could not load hesitation clip %s: %s", path, exc)
            continue

        frames = tuple(
            payload[offset : offset + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00")
            for offset in range(0, len(payload), FRAME_BYTES)
        )
        if frames:
            clips.append(frames)
    return tuple(clips)


HESITATION_CLIPS = _load_hesitation_clips()


class CallBridge:
    """Owns one call: PBX audio in, Gemini audio out, at a paced 20ms cadence."""

    def __init__(
        self,
        ws,
        cap: capture.CallCapture,
        api_key: str,
        caller: str = "",
        input_batch_frames: int = INPUT_BATCH_FRAMES,
        vad_silence_ms: int | None = None,
        vad_prefix_ms: int | None = None,
    ) -> None:
        self._ws = ws
        self._cap = cap
        self._api_key = api_key
        self._tools = tools.ToolContext(cap.call_id, caller)
        self._input_batch_frames = input_batch_frames
        self._vad_silence_ms = vad_silence_ms
        self._vad_prefix_ms = vad_prefix_ms
        self._hesitation_silence_s = (
            vad_silence_ms / 1000
            if vad_silence_ms is not None
            else HESITATION_SILENCE_S
        )
        self._out: collections.deque[bytes] = collections.deque()
        self._hesitation: collections.deque[bytes] = collections.deque()
        self._carry = b""
        self._partial = b""
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._session: GeminiLiveSession | None = None
        self._last_user_audio: float | None = None
        self._user_audio_seen = False
        self._hesitation_started = False
        self._speaking = False
        self._turn_id = 0
        self._pending_turn_id: int | None = None
        self._active_turn_id: int | None = None
        self._hangup_after_response = False
        self._meter = cost.UsageMeter()
        self.stats: dict[str, object] = {
            "caller": self._tools.caller,
            "turns": 0,
            "interruptions": 0,
            "reply_latency_ms": [],
            "tool_calls": [],
            "transcript": [],
            "usage": self._meter.snapshot(),
        }

    # ------------------------------------------------------------------ input

    def feed(self, pcm8k: bytes) -> None:
        """Called for every inbound PBX frame. Never blocks the receive loop."""
        self._inbox.put_nowait(pcm8k)

    async def _pump_input(self) -> None:
        batch: list[bytes] = []
        while True:
            batch.append(await self._inbox.get())
            if len(batch) < self._input_batch_frames:
                continue
            chunk = b"".join(batch)
            batch.clear()
            rms = audio.rms(chunk)
            if rms > 200:  # ignore the PBX's constant-8 silence
                self._last_user_audio = time.monotonic()
                self._user_audio_seen = True
                self._hesitation.clear()
                self._hesitation_started = False
                if not self._speaking and self._pending_turn_id is None:
                    self._pending_turn_id = self._turn_id + 1
                self._cap.trace(
                    "user_audio_batch_ready",
                    self._pending_turn_id,
                    rms=rms,
                    bytes=len(chunk),
                    batch_frames=self._input_batch_frames,
                )
            elif (
                self._user_audio_seen
                and not self._speaking
                and not self._hesitation_started
                and self._last_user_audio is not None
                and time.monotonic() - self._last_user_audio >= self._hesitation_silence_s
            ):
                self._start_hesitation()
            assert self._session is not None
            send_started = time.monotonic()
            pcm16k = audio.upsample_8k_to_16k(chunk)
            resample_ms = round((time.monotonic() - send_started) * 1000, 3)
            await self._session.send_audio(pcm16k)
            self._cap.trace(
                "gemini_audio_sent",
                bytes=len(pcm16k),
                resample_ms=resample_ms,
            )

    # ----------------------------------------------------------------- output

    async def _pump_output(self) -> None:
        """Emit exactly one 20ms frame per tick, silence when we have nothing.

        A continuous stream keeps the PBX jitter buffer fed and makes barge-in a
        matter of dropping queued frames.
        """
        next_tick = time.monotonic()
        while True:
            next_tick += FRAME_S
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
            if self._out:
                hesitation_frame = False
                frame = self._out.popleft()
            elif self._hesitation:
                hesitation_frame = True
                frame = self._hesitation.popleft()
            else:
                hesitation_frame = False
                frame = SILENCE_FRAME
            try:
                await self._ws.send_bytes(frame)
            except Exception:
                return
            self._cap.record("out", "binary", frame)
            if self._active_turn_id is not None and frame != SILENCE_FRAME and not hesitation_frame:
                self._cap.trace(
                    "pbx_audio_sent",
                    self._active_turn_id,
                    bytes=len(frame),
                )
                self._active_turn_id = None

    async def _drain_and_close(self, *, max_wait_s: float = 30.0, linger_s: float = 1.0) -> None:
        """Play out every queued frame, then give the PBX jitter buffer a moment
        before closing, so the caller hears the whole goodbye sentence."""
        if self._partial:
            self._out.append(self._partial.ljust(FRAME_BYTES, b"\x00"))
            self._partial = b""
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline and self._out:
            await asyncio.sleep(FRAME_S)
        await asyncio.sleep(linger_s)
        try:
            await self._ws.close()
        except Exception:
            pass

    def _enqueue(self, pcm8k: bytes) -> None:
        data = self._partial + pcm8k
        whole = len(data) - (len(data) % FRAME_BYTES)
        for i in range(0, whole, FRAME_BYTES):
            self._out.append(data[i : i + FRAME_BYTES])
        self._partial = data[whole:]

    def _start_hesitation(self) -> None:
        if self._hesitation_started or not HESITATION_CLIPS:
            return
        self._hesitation.extend(random.choice(HESITATION_CLIPS))
        self._hesitation_started = True

    # ------------------------------------------------------------------ model

    async def _pump_model(self) -> None:
        assert self._session is not None
        async for event in self._session.events():
            kind = event["type"]
            if kind == "audio":
                received_at = time.monotonic()
                pcm8k, self._carry = audio.downsample_24k_to_8k(event["pcm24k"], self._carry)
                self._hesitation.clear()
                if not self._speaking:
                    self._speaking = True
                    self._turn_id = self._pending_turn_id or (self._turn_id + 1)
                    self._pending_turn_id = None
                    self._active_turn_id = self._turn_id
                    first_audio_ms = round(
                        (received_at - self._last_user_audio) * 1000, 3
                    ) if self._last_user_audio is not None else None
                    self._cap.trace(
                        "gemini_first_audio",
                        self._turn_id,
                        bytes=len(event["pcm24k"]),
                        first_audio_ms=first_audio_ms,
                    )
                    if self._last_user_audio is not None:
                        latency = round((received_at - self._last_user_audio) * 1000)
                        self.stats["reply_latency_ms"].append(latency)
                        log.info("[%s] reply latency %d ms", self._cap.call_id, latency)
                resample_started = time.monotonic()
                self._enqueue(pcm8k)
                self._cap.trace(
                    "audio_enqueued",
                    self._turn_id if self._turn_id else None,
                    bytes=len(pcm8k),
                    resample_ms=round((time.monotonic() - resample_started) * 1000, 3),
                    queue_frames=len(self._out),
                )
            elif kind == "interrupted":
                self.stats["interruptions"] += 1
                self._out.clear()
                self._carry = b""
                self._partial = b""
                self._speaking = False
                log.info("[%s] barge-in: caller interrupted the bot", self._cap.call_id)
            elif kind == "turn_complete":
                self.stats["turns"] += 1
                self._speaking = False
                self._user_audio_seen = False
                self._hesitation_started = False
                if self._hangup_after_response:
                    await self._drain_and_close()
                    return
            elif kind == "transcript":
                self.stats["transcript"].append(f"{event['who']}: {event['text']}")
                log.info("[%s] %s: %s", self._cap.call_id, event["who"], event["text"])
            elif kind == "tool_call":
                responses = []
                for call in event["calls"]:
                    tool_started = time.monotonic()
                    result = self._tools.run(call["name"], call.get("args") or {})
                    tool_ms = round((time.monotonic() - tool_started) * 1000, 3)
                    self.stats["tool_calls"].append({"name": call["name"], "result": result})
                    tool_turn_id = self._pending_turn_id or self._turn_id + 1
                    self._cap.trace(
                        "tool_completed",
                        tool_turn_id,
                        name=call["name"],
                        duration_ms=tool_ms,
                    )
                    responses.append(
                        {"id": call.get("id"), "name": call["name"], "response": result}
                    )
                    if call["name"] == "hangup_call" and result.get("hung_up"):
                        self._hangup_after_response = True
                    if call["name"] == "transfer_to_representative":
                        await self._handle_transfer(result)
                        return
                await self._session.send_tool_responses(responses)
            elif kind == "usage":
                self._meter.add(event["usage"])
                self.stats["usage"] = self._meter.snapshot()
            elif kind == "go_away":
                log.warning("[%s] gemini going away: %s", self._cap.call_id, event["detail"])

    # ------------------------------------------------------------------- life

    async def _handle_transfer(self, result: dict[str, Any]) -> None:
        """Transfer to the configured extension, or gracefully tell the caller
        the office will call back and hang up if the PBX does not understand."""
        if not result.get("ok"):
            log.warning("[%s] transfer failed: %s", self._cap.call_id, result)
            await self._session.send_text(
                "אמור בקצרה: שירות הנציגים לא זמין כרגע. אין אפשרות להעביר. סיים את השיחה."
            )
            self._hangup_after_response = True
            return
        phone = str(result.get("transfer_to") or "")
        if not phone:
            return
        try:
            frame = TRANSFER_FRAME.format(phone=phone)
        except Exception:
            frame = json.dumps({"action": "transfer", "phone": phone})
        # Best-effort: send the frame the PBX may expect, then close so the call
        # ends cleanly even if the parallel channel ignores it.
        try:
            await self._ws.send_text(frame)
            log.info("[%s] sent transfer frame: %s", self._cap.call_id, frame)
        except Exception:
            log.exception("[%s] failed to send transfer frame", self._cap.call_id)
        notify.send_text(
            f"הלקוח {self._tools.caller} ביקש נציג; בוצעה העברה/סיום שיחה ל-{phone}.",
            kind="transfer",
        )
        self._hangup_after_response = True

    async def run(self) -> None:
        system_prompt = db.get_prompt("system")
        if self._tools.caller:
            system_prompt += f"\nמספר הטלפון של המתקשר הנוכחי הוא {self._tools.caller}."
        async with GeminiLiveSession(
            self._api_key,
            system_prompt,
            tools=tools.DECLARATIONS,
            silence_ms=self._vad_silence_ms if self._vad_silence_ms is not None else SILENCE_MS,
            prefix_padding_ms=(
                self._vad_prefix_ms
                if self._vad_prefix_ms is not None
                else PREFIX_PADDING_MS
            ),
        ) as session:
            self._session = session
            greeting = db.get_botconfig().get("opening_sentence") or prompt.GREETING
            if greeting:
                await session.send_text(f"אמור עכשיו בדיוק את המשפט הזה: {greeting}")
            tasks = [
                asyncio.create_task(self._pump_input()),
                asyncio.create_task(self._pump_output()),
                asyncio.create_task(self._pump_model()),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()

    def finish(self) -> None:
        """Persist the call so a redial within the memory window can resume it."""
        transcript = "\n".join(self.stats["transcript"])
        log.info("[%s] estimated cost $%.4f", self._cap.call_id, self._meter.cost_usd())
        with db.session_scope() as session:
            session.add(
                db.CallLog(
                    call_id=self._cap.call_id,
                    phone=self._tools.caller,
                    ended_at=datetime.utcnow(),
                    transcript=transcript,
                    stats_json=json.dumps(self.stats, ensure_ascii=False),
                    summary=(
                        f"order #{self._tools.saved_order_id}"
                        if self._tools.saved_order_id
                        else "no order saved"
                    ),
                )
            )
