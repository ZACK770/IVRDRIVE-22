# Devin phone line (PBX 076-567-3575, key 0)

Isaac calls **076-567-3575 and presses 0** to reach the QA Bot by phone.

| Key | What happens |
| --- | --- |
| (entry) | Spoken status: what is waiting on Isaac, what progressed, what is next |
| 1 | Replay the status |
| 2 | Record an instruction / answer / question (up to 3 min, `#` to finish; hanging up also sends it) |
| 3 | Hear Devin's replies since the last recorded message |
| 0 / no input | Goodbye |

Recordings are transcribed (Gemini, falling back to the PBX soft STT) and posted to the Bot's
conversation through the Devin API as a message from Isaac. Every 5 minutes a cron refreshes the
status summary; when the Bot has an open question for Isaac it calls him and reads the question
(TTS campaign; `NOTIFY_MODE=flash` for a missed call instead) — at most once per question,
3 hours apart, never 22:00–08:00 Israel time.

## Pieces

- Cloudflare Worker `devin-phone-line` (`src/worker.js`), KV binding `STATE`.
  Endpoints: `/pbx?key=…` (PBX module API callback), `POST /refresh?key=…`, `/healthz`.
- PBX extension `/0` (`דבין · עוזר אישי`, id 14142, type `api`, `paramsMode=chain`) under the
  root menu 10863. Only key 0 was added; the root settings and every other child are unchanged.
  Access: PBX `phoneFilter` + the Worker's `ALLOWED_CALLERS`, both `0527180504`.

## Config

`wrangler.toml` holds the non-secret vars (`BOT_SESSION_ID`, `ALLOWED_CALLERS`, `NOTIFY_PHONE`,
`OUTBOUND_ENABLED`, …). Secrets are set with `wrangler secret put`: `CALLBACK_KEY` (also in the
PBX extension URL), `DEVIN_API_KEY`, `GEMINI_API_KEY`, `PBX_API_KEY` (the system key,
`TLIVR_SYSTEM_API_KEY` in the IVR-DRIVER Render env).

If the Bot's main conversation is replaced, update `BOT_SESSION_ID` and redeploy.

```bash
npm test
CLOUDFLARE_ACCOUNT_ID=da53b4cd888030bba791621ea1a39f8c npx wrangler@3 deploy
```
