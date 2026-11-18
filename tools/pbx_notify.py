"""Ring, flash-call or leave a voice message through the Technoline PBX.

Dry run by default: campaign commands send ``demo=yes`` (the PBX validates and
prices the request without dialing) and ``code`` only prints what it would send.
Pass ``--live`` to really dial.

    python tools/pbx_notify.py flash 0521234567
    python tools/pbx_notify.py --live voice 0521234567 "העבודה הסתיימה"
    python tools/pbx_notify.py code 0521234567 --cid 1234 --mode voice
    python tools/pbx_notify.py report 140312

The system apiKey comes from ``PBX_API_KEY``; with ``--from-render`` it is read
from the IVR-DRIVER service environment using ``RENDER_API_KEY_NEW`` (or
``RENDER_API_KEY``). The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = os.getenv("PBX_BASE_URL", "https://api.tlivr.com").rstrip("/")
RENDER_SERVICE = os.getenv("PBX_RENDER_SERVICE", "srv-da25qqvqj5pc73dl1be0")
TIMEOUT = 30
# The PBX firewall returns 403 to the default Python-urllib user agent.
HEADERS = {"User-Agent": "pbx-notify/1.0", "Accept": "application/json"}


def render_api_key() -> str:
    token = os.getenv("RENDER_API_KEY_NEW") or os.getenv("RENDER_API_KEY")
    if not token:
        sys.exit("--from-render needs RENDER_API_KEY_NEW or RENDER_API_KEY")
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{RENDER_SERVICE}/env-vars?limit=100",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        env = {e["envVar"]["key"]: e["envVar"]["value"] for e in json.load(resp)}
    key = env.get("PBX_API_KEY") or env.get("TLIVR_SYSTEM_API_KEY")
    if not key:
        sys.exit("PBX_API_KEY is not set on the Render service")
    return key


def post_json(endpoint: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}/{endpoint}",
        data=json.dumps(body).encode(),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def make_call(params: dict, api_key: str) -> dict:
    query = urllib.parse.urlencode({"action": "makeCall", **params, "apiKey": api_key})
    req = urllib.request.Request(f"{BASE_URL}/ivrFilesApi.php?{query}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def campaign(args: argparse.Namespace, api_key: str, fields: dict) -> dict:
    body = {
        "action": "campaignRun",
        "apiKey": api_key,
        "phones": [args.phone],
        "title": args.title,
        **fields,
    }
    if args.caller_id:
        body["callId"] = args.caller_id
    if not args.live:
        body["demo"] = "yes"
    return post_json("campaignApi.php", body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="really dial (default: dry run)")
    parser.add_argument("--from-render", action="store_true", help="read the apiKey from Render")
    sub = parser.add_subparsers(dest="command", required=True)

    flash = sub.add_parser("flash", help="ring once and hang up (צינתוק, messagesType=RH)")
    voice = sub.add_parser("voice", help="call and read a text message aloud (TTS campaign)")
    voice.add_argument("text")
    voice.add_argument("--ring-only", action="store_true", help="ring only; message kept for call-back")
    voice.add_argument("--retries", type=int, default=1, help="dialRetries, max 5")
    for p in (flash, voice):
        p.add_argument("phone")
        p.add_argument("--caller-id", help="approved callId (default: the system number)")
        p.add_argument("--title", default="Devin notification")

    code = sub.add_parser("code", help="makeCall: show a 4-6 digit code as caller ID, or read it aloud")
    code.add_argument("phone")
    code.add_argument("--cid", required=True)
    code.add_argument("--mode", choices=["cid", "voice"], default="cid")
    code.add_argument("--voice-text", help="mode=voice text with {code}, max 200 chars")
    code.add_argument("--caller-id", help="approved callerId, or main")

    report = sub.add_parser("report", help="per-recipient result of a campaign")
    report.add_argument("campaign_id")

    args = parser.parse_args()
    api_key = render_api_key() if args.from_render else os.getenv("PBX_API_KEY", "")
    if not api_key:
        sys.exit("Set PBX_API_KEY or pass --from-render")

    if args.command == "flash":
        result = campaign(args, api_key, {"messagesType": "RH"})
    elif args.command == "voice":
        fields = {"audioText": args.text, "dialRetries": args.retries, "betweenRetries": 5}
        if args.ring_only:
            fields["ringOnly"] = "yes"
        result = campaign(args, api_key, fields)
    elif args.command == "code":
        params = {"phone": args.phone, "cid": args.cid, "mode": args.mode}
        if args.voice_text:
            params["voiceText"] = args.voice_text
        if args.caller_id:
            params["callerId"] = args.caller_id
        if not args.live:
            result = {"dry_run": True, "would_send": {"action": "makeCall", **params}}
        else:
            result = make_call(params, api_key)
    else:
        result = post_json(
            "campaignApi.php",
            {"action": "campaignReport", "apiKey": api_key, "campaignId": args.campaign_id},
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = str(result.get("status", "OK")).upper() != "OK" or str(result.get("errorCode", "0")) != "0"
    sys.exit(1 if failed and not result.get("dry_run") else 0)


if __name__ == "__main__":
    main()
