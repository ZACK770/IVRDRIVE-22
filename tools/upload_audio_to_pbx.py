"""Bulk-upload the generated audio files to the PBX audio library.

Run from the project root after generating the files:

    PBX_DRY_RUN=0 PBX_API_KEY=your_key PBX_BASE_URL=https://app.ipsales.co.il \
        python tools/upload_audio_to_pbx.py

If PBX_API_KEY is missing, the script aborts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import pbx


def main() -> None:
    if not pbx.API_KEY:
        print("PBX_API_KEY is not set. Cannot upload.")
        sys.exit(1)
    audio_dir = Path("audio")
    if not audio_dir.is_dir():
        print("Run python tools/generate_audio.py first.")
        sys.exit(1)
    for path in sorted(audio_dir.glob("*.mp3")):
        name = path.stem
        try:
            result = pbx.upload_file(name, path.read_bytes())
            if result.get("dry_run"):
                print(f"{name}: dry run (set PBX_DRY_RUN=0 to really upload)")
            else:
                print(f"{name}: uploaded -> {result.get('status')}")
        except Exception as exc:
            print(f"{name}: failed: {exc}")


if __name__ == "__main__":
    main()
