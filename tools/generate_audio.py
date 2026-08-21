"""Generate the main menu audio files with a slow, warm male Hebrew voice.

Run locally before deploy, or add it as a build step. The output is written to
``audio/`` using the PBX audio-library names from ``app/ivr.DEFAULT_AUDIO``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import edge_tts

from app import ivr, tts

AUDIO_DIR = Path(__file__).parent.parent / "audio"
VOICE = os.getenv("TTS_VOICE", "he-IL-AvriNeural")
RATE = os.getenv("TTS_RATE", "-15%")

# Everything in AUDIO_TEXTS is regenerated so the fallback TTS and
# any pre-recorded files stay in sync with the current prompt copy.
ALL_KEYS = list(tts.AUDIO_TEXTS.keys())


def _filename(key: str) -> str:
    """Use the PBX audio-library name as the file stem."""
    name = ivr.DEFAULT_AUDIO.get(key, key)
    return f"{name}.mp3"


async def _generate(key: str, text: str, dest: Path) -> None:
    communicate = edge_tts.Communicate(text, voice=VOICE, rate=RATE)
    data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            data.extend(chunk["data"])
    dest.write_bytes(bytes(data))
    print(f"wrote {dest} ({len(data)} bytes)")


def main() -> None:
    AUDIO_DIR.mkdir(exist_ok=True)
    for key in ALL_KEYS:
        text = tts.AUDIO_TEXTS.get(key)
        if not text:
            print(f"skipping {key}: no text")
            continue
        dest = AUDIO_DIR / _filename(key)
        asyncio.run(_generate(key, text, dest))


if __name__ == "__main__":
    main()
