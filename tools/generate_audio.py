"""Generate the static audio files for the PBX library with Hebrew TTS.

Run from the project root:

    python tools/generate_audio.py

Output is written to ``audio/`` as MP3 files. Upload them to the PBX via
``ivrFilesApi.php?action=uploadFile`` or through the Technoline UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import tts
from app.ivr import DEFAULT_AUDIO


def main() -> None:
    out_dir = Path("audio")
    out_dir.mkdir(exist_ok=True)
    for logical, file_name in DEFAULT_AUDIO.items():
        text = tts.AUDIO_TEXTS.get(logical, "")
        if not text:
            print(f"skip {file_name}: no prompt")
            continue
        try:
            data = tts.synthesize(text)
            path = out_dir / f"{file_name}.mp3"
            path.write_bytes(data)
            print(f"wrote {path} ({len(data)} bytes)")
        except Exception as exc:
            print(f"failed {file_name}: {exc}")


if __name__ == "__main__":
    main()
