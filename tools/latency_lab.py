"""Replay scripted caller turns and measure first returned audio.

This is a client-side lab, not a deployment tool. Point it at a local app
started with different environment variables, or at the deployed WebSocket
for a baseline. Each input file is one caller turn: PCM16LE, mono, 8kHz WAV
or raw audio.

Example:
    python tools/latency_lab.py --url ws://127.0.0.1:8000/ws/ivr \
        --audio turn-1.wav turn-2.wav turn-3.wav turn-4-price.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

import websockets

FRAME_MS = 20
SAMPLE_RATE = 8000
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000


def read_pcm(path: Path) -> bytes:
    if path.suffix.lower() != ".wav":
        return path.read_bytes()
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != SAMPLE_RATE
        ):
            raise ValueError(f"{path}: expected mono PCM16LE WAV at 8kHz")
        return source.readframes(source.getnframes())


async def run(args: argparse.Namespace) -> list[dict[str, object]]:
    headers = {"Authorization": f"Bearer {args.bearer}"} if args.bearer else {}
    overrides = {
        key: value
        for key, value in (
            ("vad_silence_ms", args.vad_silence_ms),
            ("vad_prefix_ms", args.vad_prefix_ms),
            ("input_batch_frames", args.batch_frames),
        )
        if value is not None
    }
    url = f"{args.url}?{urlencode(overrides)}" if overrides else args.url
    async with websockets.connect(
        url, additional_headers=headers, max_size=None
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "callId": "latency-lab-" + "0" * 30,
                    "caller": args.caller,
                    "system": args.system,
                    "token": args.bearer or "latency-lab",
                    "format": "pcm16;rate=8000;ch=1",
                }
            )
        )

        received: list[tuple[float, int]] = []

        async def drain() -> None:
            try:
                async for message in ws:
                    if isinstance(message, bytes) and any(message):
                        received.append((time.monotonic(), len(message)))
            except Exception:
                pass

        reader = asyncio.create_task(drain())
        await asyncio.sleep(args.warmup_ms / 1000)
        results: list[dict[str, object]] = []
        for index, path_text in enumerate(args.audio, 1):
            path = Path(path_text)
            pcm = read_pcm(path)
            if len(pcm) % FRAME_BYTES:
                pcm += b"\x00" * (FRAME_BYTES - len(pcm) % FRAME_BYTES)
            for offset in range(0, len(pcm), FRAME_BYTES):
                await ws.send(pcm[offset : offset + FRAME_BYTES])
                await asyncio.sleep(FRAME_MS / 1000)
            sent_end = time.monotonic()
            silence_frames = max(1, round(args.pause_ms / FRAME_MS))
            for _ in range(silence_frames):
                await ws.send(b"\x00" * FRAME_BYTES)
                await asyncio.sleep(FRAME_MS / 1000)
            deadline = time.monotonic() + args.turn_timeout_ms / 1000
            first = next((item for item in received if item[0] >= sent_end), None)
            while first is None and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
                first = next((item for item in received if item[0] >= sent_end), None)
            if first is not None:
                last_audio = first[0]
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                    new_audio = [
                        item for item in received if last_audio < item[0]
                    ]
                    if new_audio:
                        last_audio = new_audio[-1][0]
                    elif time.monotonic() - last_audio >= args.output_settle_ms / 1000:
                        break
            results.append(
                {
                    "turn": index,
                    "file": str(path),
                    "audio_ms": round(len(pcm) / (SAMPLE_RATE * 2) * 1000, 1),
                    "first_audio_ms": (
                        round((first[0] - sent_end) * 1000, 1) if first else None
                    ),
                    "first_audio_bytes": first[1] if first else None,
                }
            )
        await asyncio.sleep(args.settle_ms / 1000)
        reader.cancel()
        return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/ivr")
    parser.add_argument("--audio", nargs="+", required=True)
    parser.add_argument("--caller", default="0501234567")
    parser.add_argument("--system", default="100")
    parser.add_argument("--bearer")
    parser.add_argument("--warmup-ms", type=int, default=10000)
    parser.add_argument("--pause-ms", type=int, default=900)
    parser.add_argument("--turn-timeout-ms", type=int, default=12000)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument("--output-settle-ms", type=int, default=500)
    parser.add_argument("--vad-silence-ms", type=int)
    parser.add_argument("--vad-prefix-ms", type=int)
    parser.add_argument("--batch-frames", type=int)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
