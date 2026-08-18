"""Feasibility spike: bridge one PBX call to Gemini Live and time everything.

The point of this module is not to be the product. It answers three questions a
real call cannot answer on paper: how fast the bot replies, whether it handles
silence sensibly, and whether the caller can interrupt it mid-sentence.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import time

from app import audio, capture
from app.gemini_live import GeminiLiveSession

log = logging.getLogger("bridge")

#: PBX framing, discovered empirically: 320 bytes = 160 samples = 20ms @ 8kHz.
FRAME_BYTES = 320
FRAME_S = 0.02
SILENCE_FRAME = b"\x00" * FRAME_BYTES

#: Gemini's server-side VAD needs a steady stream; batching 5 frames keeps the
#: request rate at 10/s instead of 50/s without adding meaningful latency.
INPUT_BATCH_FRAMES = 5

DEFAULT_PROMPT = os.getenv(
    "BOT_SYSTEM_PROMPT",
    "אתה נציג טלפוני של מוקד הסעות בשם 'דרייברים'. "
    "דבר עברית בלבד, בקצרה ובטבעיות, משפט אחד או שניים בכל תור. "
    "המטרה שלך היא לאסוף מהלקוח: כתובת מוצא, כתובת יעד, מספר נוסעים ומועד הנסיעה. "
    "שאל שאלה אחת בכל פעם, ואל תמציא מחירים.",
)
GREETING = os.getenv("BOT_GREETING", "ברוך הבא למוקד דרייברים, איך אפשר לעזור?")


class CallBridge:
    """Owns one call: PBX audio in, Gemini audio out, at a paced 20ms cadence."""

    def __init__(self, ws, cap: capture.CallCapture, api_key: str) -> None:
        self._ws = ws
        self._cap = cap
        self._api_key = api_key
        self._out: collections.deque[bytes] = collections.deque()
        self._carry = b""
        self._partial = b""
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._session: GeminiLiveSession | None = None
        self._last_user_audio: float | None = None
        self._speaking = False
        self.stats: dict[str, object] = {
            "turns": 0,
            "interruptions": 0,
            "reply_latency_ms": [],
            "transcript": [],
        }

    # ------------------------------------------------------------------ input

    def feed(self, pcm8k: bytes) -> None:
        """Called for every inbound PBX frame. Never blocks the receive loop."""
        self._inbox.put_nowait(pcm8k)

    async def _pump_input(self) -> None:
        batch: list[bytes] = []
        while True:
            batch.append(await self._inbox.get())
            if len(batch) < INPUT_BATCH_FRAMES:
                continue
            chunk = b"".join(batch)
            batch.clear()
            if audio.rms(chunk) > 200:  # ignore the PBX's constant-8 silence
                self._last_user_audio = time.monotonic()
            assert self._session is not None
            await self._session.send_audio(audio.upsample_8k_to_16k(chunk))

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
            frame = self._out.popleft() if self._out else SILENCE_FRAME
            try:
                await self._ws.send_bytes(frame)
            except Exception:
                return
            self._cap.record("out", "binary", frame)

    def _enqueue(self, pcm8k: bytes) -> None:
        data = self._partial + pcm8k
        whole = len(data) - (len(data) % FRAME_BYTES)
        for i in range(0, whole, FRAME_BYTES):
            self._out.append(data[i : i + FRAME_BYTES])
        self._partial = data[whole:]

    # ------------------------------------------------------------------ model

    async def _pump_model(self) -> None:
        assert self._session is not None
        async for event in self._session.events():
            kind = event["type"]
            if kind == "audio":
                pcm8k, self._carry = audio.downsample_24k_to_8k(event["pcm24k"], self._carry)
                if not self._speaking:
                    self._speaking = True
                    if self._last_user_audio is not None:
                        latency = round((time.monotonic() - self._last_user_audio) * 1000)
                        self.stats["reply_latency_ms"].append(latency)
                        log.info("[%s] reply latency %d ms", self._cap.call_id, latency)
                self._enqueue(pcm8k)
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
            elif kind == "transcript":
                self.stats["transcript"].append(f"{event['who']}: {event['text']}")
                log.info("[%s] %s: %s", self._cap.call_id, event["who"], event["text"])
            elif kind == "go_away":
                log.warning("[%s] gemini going away: %s", self._cap.call_id, event["detail"])

    # ------------------------------------------------------------------- life

    async def run(self) -> None:
        async with GeminiLiveSession(self._api_key, DEFAULT_PROMPT) as session:
            self._session = session
            if GREETING:
                await session.send_text(f"אמור עכשיו בדיוק את המשפט הזה: {GREETING}")
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
