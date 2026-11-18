# Calling the owner through the Technoline PBX

`tools/pbx_notify.py` is a standard-library script that rings, flash-calls (צינתוק)
or leaves a TTS voice message through the Technoline system **0765673575**.
It is a dry run unless `--live` is passed.

## Credentials

- System apiKey: Render service IVR-DRIVER (`srv-da25qqvqj5pc73dl1be0`), env
  `PBX_API_KEY` (same value as `TLIVR_SYSTEM_API_KEY`). `--from-render` reads it with
  `RENDER_API_KEY_NEW`; it is never printed or stored.
- Host: `https://api.tlivr.com` (`app.ipsales.co.il` answers the same).
- Requests from a Devin VM were accepted (no IP whitelist error) on 2026-11-18.
- The PBX firewall answers 403 to the default `Python-urllib` user agent; the script
  sends its own.

## Commands

| Need | Command | PBX call | Cost |
|---|---|---|---|
| Flash call (rings, hangs up) | `flash <phone>` | `campaignApi.php` `campaignRun`, `messagesType=RH` | free for numbers that opted in to flash calls, otherwise 0.05 ₪ |
| Voice message (answers, reads text twice) | `voice <phone> "<text>"` | `campaignRun` with `audioText` (TTS) | about 0.12 ₪ |
| Flash call with message kept for call-back | `voice <phone> "<text>" --ring-only` | `campaignRun` with `audioText` and `ringOnly=yes` | as a voice message |
| Code as caller ID, or read aloud | `code <phone> --cid 1234 [--mode voice]` | `ivrFilesApi.php` `makeCall` | — |
| Result of a campaign | `report <campaignId>` | `campaignReport` | — |

```
python3 tools/pbx_notify.py --from-render flash 0527180504          # dry run (demo=yes)
python3 tools/pbx_notify.py --from-render --live voice 0527180504 "העבודה הסתיימה"
```

Dry runs send `demo=yes`: the PBX validates, filters and prices the request without
dialing. `code` has no demo mode, so its dry run only prints the request.

## Known state (2026-11-18)

- 052-7180504 is opted in to flash calls (`ringFree: 1`), but it is on the account's
  removal list for voice campaigns (someone pressed 9): `voice` to it returns
  `errorCode 6`. The removal list cannot be cleared through the documented API; it
  has to be cleared in the Technoline panel, or by the owner opting back in by phone.
- Balance at the time: 193.62 ₪.

Official docs: https://pbx.tlivr.com/campaignApiDocs.html ,
https://pbx.tlivr.com/makeCallApiDocs.html
