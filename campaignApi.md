# Campaign API — Reference

Reference for third-party developers and **LLM coding assistants** integrating with the **Voice Campaign API** endpoint (`campaignApi.php`).

This document is the complete LLM-friendly equivalent of [campaignApiDocs.html](campaignApiDocs.html) — every action documented in the HTML reference is here in full. For the email-campaigns counterpart see [campaignEmailApi.md](campaignEmailApi.md).

> **Read this first if you're an AI agent debugging integration errors:** jump to [Recipient-number rules](#recipient-number-rules--read-this-before-using-campaignrun) and [FAQ for AI agents](#faq--common-ai-agent-questions). Most "how do I get past errorCode 6 / how do I approve a number / how do I do a single outbound call" questions are answered there explicitly.

---

## Base URL

```
https://app.ipsales.co.il/campaignApi.php
```

All actions are served by this single endpoint. The action is selected by the `action` parameter (e.g. `action=campaignRun`). **Exactly one action per request.**

## HTTP method

**All requests must be `POST`.** Body content type can be:

- `application/json`
- `application/x-www-form-urlencoded`
- `multipart/form-data` — required when attaching a file (`audioFile` binary or a comma-separated `phones` text file)

## Authentication

Every request must include an API key, either in the query string or in the POST body:

| Location           | Example                                  |
|--------------------|------------------------------------------|
| Query string       | `?apiKey=YOUR_API_KEY`                   |
| POST body (JSON)   | `{ "apiKey": "YOUR_API_KEY", ... }`      |

The API key identifies the **tenant account**. Treat it like a password — never embed in browser code or public repos.

## Source IP — must be whitelisted

> **Required before ANY request will succeed.** The IP address of the calling server must be approved in advance by the technical team. Requests from a non-whitelisted IP are rejected up front with `errorCode: -99` / `Access denied`, regardless of how correct everything else is.
>
> This is account-level configuration done once per origin server. If you're getting `-99` you do not have a code problem — you have a missing IP allowlist entry. Contact support.

---

## Action catalogue

| Action               | Purpose                                                |
|----------------------|--------------------------------------------------------|
| `campaignRun`        | Create + start an outbound voice campaign              |
| `campaignsHistory`   | List campaigns in a date range                         |
| `campaignReport`     | Full per-recipient report for one campaign             |
| `max_ringSet`        | Change parallel-dial cap on a running campaign         |
| `campaignHold`       | Pause a running campaign                               |
| `campaignResumption` | Resume a paused campaign                               |
| `campaignStop`       | Hard-stop a campaign (irreversible)                    |
| `addDid`             | Register a new outbound Caller-ID (DID) on the account |

---

## Recipient-number rules — read this before using `campaignRun`

> **Common AI-agent confusion.** The `phones` list in `campaignRun` is **not** a whitelist. There is **no API endpoint to "approve" recipient numbers** in advance. There is no equivalent of "add to clientes". The system does not require recipient numbers to be pre-registered anywhere.
>
> If you got `{"errorCode":6,"messige":"לא נמצאו מספרים תקינים/מאושרים לקבלת הודעות"}`, it means **every number in your `phones` array was rejected by one of the two rules below** — not that you're missing a registration step.

A number you put in `phones` is **accepted** iff **both** of these are true:

### 1. Israeli phone format

Non-digit characters are stripped. A leading `0` is enforced. The cleaned string must match **one** of these patterns:

| Type                     | Regex                                | Example       | Total digits |
|--------------------------|--------------------------------------|---------------|--------------|
| Landline (regional)      | `0[2|3|4|8|9]XXXXXXX`                | `0212345678`  | 9            |
| `07` range / shared      | `07[0-9]XXXXXXX`                     | `0723456789`  | 10           |
| Mobile                   | `05[0|1|2|3|4|5|6|8|9]XXXXXXX`       | `0501234567`  | 10           |

**Anything else is rejected.** No international format, no `+972`, no partial numbers, no `057` (not a real mobile prefix), no PSTN without leading `0`. The API is Israel-only.

### 2. Not on the account's opt-out list

When a recipient presses key `9` during a campaign call, the system adds their number to `distributionPhoneList` with `type='blocked'` for this account. On every subsequent `campaignRun`, anyone on that list is silently dropped from `phones`. This is an opt-out registry — there is no "add to whitelist" counterpart.

### What "מאושרים" means in the error message

The Hebrew word "מאושרים" in the error is a translation accident — it sounds like "approved" but the system has no approval concept for recipients. What it actually means is "**passes the two rules above**". An AI agent reading the error literally will go hunting for an `approveNumber` endpoint that does not exist. **Don't.**

### How to fix `errorCode: 6`

1. **Check format first** — every number must match one of the three patterns above. Watch for: missing leading `0`, international format like `972501234567`, 8-digit numbers, prefixes like `057` that aren't real.
2. **Then check opt-out** — if you're recycling a recipient list, some numbers may have hit `9` in a previous campaign. Their being filtered is intentional.
3. The response's `errorPhones` and `blockedPhones` counts (returned even on partial success) tell you which bucket your losses fell into.

### `addDid` is unrelated to recipient numbers

`addDid` registers a **Caller-ID** — the number that **displays on the recipient's screen** as the "from" of the call. It has **nothing** to do with the `phones` list (the "to"). These are orthogonal concepts. AI agents commonly confuse them; don't.

---

## `campaignRun` — start a campaign

Creates and dispatches an outbound voice campaign. The same endpoint handles **everything from a single-number "ring my agent" call to a 100k-number broadcast** — there is no separate "click-to-call" or "single dial" action in this API.

### Request parameters

| Param                  | Required | Default          | Notes |
|------------------------|----------|------------------|-------|
| `action`               | yes      | —                | `campaignRun` |
| `apiKey`               | yes      | —                | Tenant key |
| `phones`               | yes      | —                | **(a)** JSON array of Israeli phones, OR **(b)** comma-separated text file via `multipart/form-data`. See [Recipient-number rules](#recipient-number-rules--read-this-before-using-campaignrun). |
| `audioFile`            | yes\*    | —                | Audio file attached as `multipart/form-data`. *Provide exactly one of `audioFile` / `audioText` / `messagesType=extensionActivation` / `messagesType=apiUrl`.* |
| `audioText`            | yes\*    | —                | Source text. System runs TTS automatically. Mutually exclusive with `audioFile`. |
| `messagesType`         | no       | `audioFile`      | `audioFile` (default — play a recording), `extensionActivation` (skip audio, route the answered call straight to a PBX extension), or `apiUrl` (skip audio, hand the answered call to your own external URL — see [API model](#api-model-messagestypeapiurl)). |
| `extensionActivation`  | yes\*\*  | —                | PBX extension ID. **Required when `messagesType=extensionActivation`.** |
| `apiUrl`               | yes\*\*\* | —                | HTTPS URL of your call-control endpoint. **Required when `messagesType=apiUrl`.** Must be `http(s)://`, ≤ 500 chars. See [API model](#api-model-messagestypeapiurl). |
| `callId`               | no       | account default  | **Outbound Caller-ID.** Must be a phone previously registered via [`addDid`](#adddid--register-an-outbound-caller-id). Omit to use the account's default DID. |
| `callLength`           | no       | `25`             | Per-call dial-length cap (seconds). Valid range **6–30**. |
| `dialRetries`          | no       | `1`              | Max retries on unanswered. Cap **5**. |
| `betweenRetries`       | yes      | `20`             | **Minutes** between retries. Range **5–120**. |
| `reasonableHours`      | no       | `no`             | `yes` = auto-pause between 23:00 and 08:00 (Israel time). |
| `sendTime`             | no       | now              | Scheduled start, format `yy-mm-dd hh:ii:ss`. Max **21 days** ahead. |
| `title`                | no       | —                | Free-text label visible in dashboards. |
| `maxRing`              | no       | unlimited        | Concurrent-dial cap (integer). |
| `runType`              | no       | —                | `hold` = create the campaign in paused state; start it later with `campaignResumption`. |
| `externalID`           | no       | —                | Your own correlation id (≤ 50 chars). |
| `externalDescription`  | no       | —                | Free description (≤ 100 chars). |
| `keysAction-N`         | no       | —                | DTMF routing for digit `N` (1–8). See [keysAction values](#keysaction-values). |
| `demo`                 | no       | `no`             | `yes` = validate + price the call but do **not** dispatch. Use to dry-run a payload. |
| `DNCTest`              | no       | `no`             | Currently inert — Israeli DNC integration is disabled in code. |

### `keysAction-N` values

Configure what happens when the recipient presses digit `N` during playback. `N` is `1`–`8`. **Digit `9` is hardcoded to "remove me from this account's list" and cannot be reassigned.**

| Value                                | Behaviour                                                                    | Example                                            |
|--------------------------------------|------------------------------------------------------------------------------|----------------------------------------------------|
| `replay`                             | Replay the audio from the start                                              | `"keysAction-3": "replay"`                         |
| `digits`                             | Capture additional digits the user enters (saved to call record)             | `"keysAction-2": "digits"`                         |
| `routing-0XXXXXXXXX`                 | Bridge the live call to an Israeli phone number                              | `"keysAction-1": "routing-0521234567"`             |
| `pbxRouting-EXT`                     | Bridge to a PBX queue/extension on your account                              | `"keysAction-1": "pbxRouting-200"`                 |
| `sipRouting-sip@host`                | Bridge to a SIP URI                                                          | `"keysAction-1": "sipRouting-sip@my-ivr.example"`  |
| `nedarimDonations-X1-X2-X3-X4-X5`    | Open a Nedarim Plus donation flow. `X1`=terminal · `X2`=fixed amount · `X3`=`yes`/`no` (amount locked) · `X4`=`yes`/`no` (require CVV) · `X5`=category name | `"keysAction-1": "nedarimDonations-12345-100-no-no-כללי"` |

### Choosing what to play — four mutually exclusive options

| Option                                                          | When to use                                            |
|-----------------------------------------------------------------|--------------------------------------------------------|
| `audioFile`                                                     | You have a pre-recorded WAV/MP3 to upload              |
| `audioText`                                                     | You want the system to TTS a string                    |
| `messagesType=extensionActivation` + `extensionActivation=EXT`  | No greeting — just route the answered call to a PBX extension |
| `messagesType=apiUrl` + `apiUrl=https://…`                      | No greeting — hand the answered call to your own external URL ("API model"). See [API model](#api-model-messagestypeapiurl). |

**Send only one.** Mixing them is undefined.

### Example — simple TTS broadcast

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":          "campaignRun",
    "apiKey":          "YOUR_API_KEY",
    "audioText":       "שלום, זוהי הודעה חשובה מחברתנו",
    "phones":          ["0501234567", "0521234567", "0541234567"],
    "title":           "קמפיין ינואר",
    "callLength":      25,
    "dialRetries":     2,
    "betweenRetries":  20,
    "reasonableHours": "yes"
  }'
```

### Example — single outbound "ring my agent" with key-routing to my IVR

A single recipient (the agent), short audio that says "press 1 to take the call", key `1` bridges the live audio to the agent's SIP IVR. **This is the canonical pattern for ad-hoc click-to-call** — there is no other endpoint for it.

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":          "campaignRun",
    "apiKey":          "YOUR_API_KEY",
    "audioText":       "בקשת שירות חדשה. הקש 1 לחיבור ללקוח",
    "phones":          ["0501112222"],
    "title":           "Ring agent",
    "callLength":      25,
    "betweenRetries":  5,
    "dialRetries":     2,
    "keysAction-1":    "sipRouting-sip@my-ivr.example.com"
  }'
```

> **Two ways to reach your own IVR.**
> 1. **`keysAction-N` routings** bridge the *live audio call* after the recipient presses a digit — via PSTN (`routing-`), PBX extension (`pbxRouting-`), or SIP URI (`sipRouting-`). There is **no** `urlRouting-` here: a `keysAction` cannot point at an HTTP URL.
> 2. **`messagesType=apiUrl`** hands the *whole answered call* to an HTTPS endpoint of yours from the moment it is answered (instead of playing audio). This is the way to drive an answered call from your own web service. See [API model](#api-model-messagestypeapiurl).

## API model (`messagesType=apiUrl`)

Instead of playing a recording or routing to a PBX extension, you can hand the **whole answered call** to an HTTPS endpoint you control — your **"API model"**. From the moment the recipient answers, the platform calls your URL with the call context, and your endpoint drives what the caller hears and does.

This is the option to use when the call flow is dynamic (personalised per recipient, decided in real time by your own service) rather than a fixed recording.

### How to use

Set two fields on `campaignRun`:

| Field           | Value                                                              |
|-----------------|-------------------------------------------------------------------|
| `messagesType`  | `apiUrl`                                                           |
| `apiUrl`        | Your endpoint, e.g. `https://your-service.example.com/voice-model` |

Rules:
- `apiUrl` must be a valid `http(s)://` URL, **≤ 500 characters**. HTTPS is strongly recommended.
- Do **not** send `audioFile` / `audioText` — they are mutually exclusive with `apiUrl`.
- `callLength` still applies as the per-call dial-length cap.
- `fileLong` (optional, seconds) sets the billed/processing length; default is `60`.
- An invalid/empty `apiUrl` returns `errorCode 27`.

### What your endpoint receives

When a recipient answers, the platform sends an HTTP request to your `apiUrl` with the call context. Typical parameters:

| Param            | Meaning                                              |
|------------------|------------------------------------------------------|
| `campaignId`     | The campaign id                                      |
| `campaignCallId` | Unique id of this specific call                      |
| `phone`          | The recipient who answered                           |
| `callId`         | The outbound Caller-ID (DID) used                    |
| `callLength`     | Per-call dial-length cap (seconds)                   |
| `event`          | Call stage (`answered`, then `dtmf` / `hangup`)      |

Secure your endpoint (e.g. verify a shared token) — treat the request as coming from the dialer.

### What your endpoint returns

Return JSON describing the call-control actions to run, in order. Minimal shape:

```json
{
  "actions": [
    { "say": "שלום, הגעתם למוקד" },
    { "playUrl": "https://your-service.example.com/welcome.wav" },
    { "gather": { "maxDigits": 1, "timeout": 5, "submitTo": "https://your-service.example.com/next" } },
    { "dial": "0312345678" },
    { "hangup": true }
  ]
}
```

- `say` — text-to-speech a string.
- `playUrl` — play an audio file from a URL.
- `gather` — collect DTMF digits, then POST them to `submitTo` (same contract, looped).
- `dial` — bridge the call to a phone/SIP target.
- `hangup` — end the call.

### Example — `campaignRun` with an API model

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":         "campaignRun",
    "apiKey":         "YOUR_API_KEY",
    "messagesType":   "apiUrl",
    "apiUrl":         "https://your-service.example.com/voice-model",
    "phones":         ["0501234567", "0521234567"],
    "title":          "API model campaign",
    "callLength":     25,
    "betweenRetries": 20,
    "dialRetries":    2
  }'
```

### Example — keysAction with a phone-routing menu

```json
{
  "action":         "campaignRun",
  "apiKey":         "YOUR_API_KEY",
  "audioFile":      "message123",
  "phones":         ["0501234567"],
  "title":          "קמפיין מכירות",
  "betweenRetries": 20,
  "keysAction-1":   "routing-0521111111",
  "keysAction-2":   "digits",
  "keysAction-3":   "replay",
  "externalID":     "CAMP-2025-001"
}
```

### Success response

```json
{
  "status":        "OK",
  "errorCode":     0,
  "campaignId":    4521,
  "phones":        1500,
  "errorPhones":   3,
  "blockedPhones": 12,
  "billing":       "625.00",
  "accountSum":    "1,234.56",
  "fileLong":      25
}
```

| Key             | Meaning                                                                  |
|-----------------|--------------------------------------------------------------------------|
| `campaignId`    | New campaign id — use it for report/hold/resume/stop                     |
| `phones`        | Recipients accepted into the run                                         |
| `errorPhones`   | Recipients rejected by format validation                                 |
| `blockedPhones` | Recipients filtered by the per-account opt-out list                      |
| `billing`       | Estimated cost in units                                                  |
| `accountSum`    | Account balance before deduction                                         |
| `fileLong`      | Audio length in seconds                                                  |

With `demo=yes`, the same fields are returned but `campaignId` is the literal string `"demo"` and **no dispatch happens**. Use it as a dry-run.

### Error codes

| `errorCode` | Reason                              | What to do |
|-------------|-------------------------------------|------------|
| `-99`       | `Access denied` — IP not whitelisted | Whitelist your server IP with support — this is not a code fix |
| `-88`       | Missing essential connection info   | Check auth + required fields |
| `1`         | `callId` not authorised on this account | Use a DID previously registered via [`addDid`](#adddid--register-an-outbound-caller-id), or omit to use account default |
| `2`         | `callLength` out of range            | 6–30 seconds |
| `3`         | `dialRetries` over the account cap   | Up to 5 |
| `4`         | `betweenRetries` out of range        | 5–120 minutes |
| `5`         | Audio too short                      | Minimum 5 seconds |
| `6`         | **No valid recipients.** Every number in `phones` failed format validation, or every one is on the account opt-out list. | See [Recipient-number rules](#recipient-number-rules--read-this-before-using-campaignrun). **There is no whitelist-add endpoint** — fix the phone format. |
| `7`         | Insufficient balance                 | Response includes `accountSum` (current) + `billing` (required) so you can compute the gap |
| `9`         | `sendTime` more than 21 days out     | Pick a closer time |
| `13`        | `keysAction-N` routing number invalid | Check the phone in `routing-XXXX` |
| `18`        | Invalid audio file                   | Check format |
| `19`        | Audio > 300s or upload error         | Trim or re-upload |
| `27`        | Invalid `apiUrl`                     | When `messagesType=apiUrl`, send a valid `http(s)://` URL, ≤ 500 chars. See [API model](#api-model-messagestypeapiurl). |

---

## `campaignsHistory` — list campaigns

Returns an array of campaign rows for a date range.

### Request

| Param      | Required | Default          | Notes |
|------------|----------|------------------|-------|
| `action`   | yes      | —                | `campaignsHistory` |
| `apiKey`   | yes      | —                | Tenant key |
| `fromDate` | no       | today            | `yyyy-mm-dd` |
| `toDate`   | no       | 30 days back     | `yyyy-mm-dd` |

### Example

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":   "campaignsHistory",
    "apiKey":   "YOUR_API_KEY",
    "fromDate": "2025-01-01",
    "toDate":   "2025-01-31"
  }'
```

### Response

```json
[
  {
    "id":              4521,
    "title":           "קמפיין ינואר 2025",
    "active":          "ended",
    "insert_time":     "01-01-2025 10:00",
    "start_time":      "01-01-2025 10:05",
    "end_time":        "01-01-2025 11:30",
    "run_time":        "01:25:00",
    "timed":           false,
    "total_phones":    1500,
    "total_sent":      1480,
    "new_phones":      20,
    "answerd_calls":   943,
    "dialing_calls":   3,
    "retries_list":    57,
    "billing":         "625.00",
    "max_ring":        10,
    "max_ring_view":   "10",
    "between_retries": 20,
    "donations_sum":   0,
    "donations_count": 0,
    "audioUrl":        "audio.php?type=ivr&audio=FILENAME"
  }
]
```

| Key                | Meaning                                                       |
|--------------------|---------------------------------------------------------------|
| `id`               | Campaign id (use for report/hold/resume/stop)                  |
| `active`           | `active` / `hold` / `ended` / `stoped`                         |
| `timed`            | `true` = scheduled for the future                              |
| `total_phones`     | Total recipient count                                          |
| `total_sent`       | How many have been dialled so far                              |
| `new_phones`       | How many are still to dial                                     |
| `answerd_calls`    | Answered calls                                                 |
| `dialing_calls`    | Currently dialling (active campaign only)                      |
| `retries_list`     | Waiting for retry                                              |
| `run_time`         | Actual run length, `hh:mm:ss`                                  |
| `max_ring_view`    | `"ללא הגבלה"` if no cap was set                                 |
| `between_retries`  | Minutes between retries                                        |

> Fields `answerd_calls`, `total_sent`, `dialing_calls`, `retries_list` come back as the string `"ממתין למידע..."` when the campaign was just created and hasn't started dialling yet.

---

## `campaignReport` — full per-recipient report

Returns **all the data shown in the campaign report** (CSV / HTML view) for a single campaign — campaign metadata + one row per recipient. Values are **formatted and enriched** (not raw DB rows): formatted phone, customer name (joined from `clientes`), call duration as `mm:ss`, Q.850 hangup-cause translated to Hebrew, human-readable times.

### Request

| Param        | Required | Description                                                     |
|--------------|----------|-----------------------------------------------------------------|
| `action`     | yes      | `campaignReport`                                                |
| `apiKey`     | yes      | Tenant API key                                                  |
| `campaignId` | yes      | Numeric campaign id (from `campaignsHistory` `id` field)        |

### Example

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":     "campaignReport",
    "apiKey":     "YOUR_API_KEY",
    "campaignId": 4521
  }'
```

### Response

A JSON object with two blocks. **Keys are stable English identifiers**; values follow the report (Hebrew status strings, translated Q.850, formatted phone/duration).

```json
{
  "campaign": {
    "id":                     "4521",
    "title":                  "קמפיין ינואר",
    "callId":                 "abc-xyz",
    "status":                 "הסתיים",
    "recipients":             "1500",
    "dialRetries":            "3",
    "betweenRetriesMinutes":  "20",
    "callLength":             "45",
    "billing":                "625.00",
    "ipRoutingUnits":         "12",
    "sttUnits":               "7",
    "answeredCalls":          "943"
  },
  "calls": [
    {
      "phone":     "050-1234567",
      "name":      "כהן ישראל",
      "status":    "answered",
      "lastCall":  "10:23:45",
      "retries":   "1",
      "duration":  "00:23",
      "digits":    "3",
      "trunk":     "",
      "sipCode":   "200",
      "q850Text":  "ניתוק תקין (BYE)",
      "sipText":   "OK",
      "had180":    "yes",
      "had183":    "no"
    }
  ]
}
```

### Field reference

#### `campaign`

| Key                       | Meaning                                                                  |
|---------------------------|--------------------------------------------------------------------------|
| `id`                      | Campaign id                                                              |
| `title`                   | Campaign description                                                     |
| `callId`                  | Internal call id                                                         |
| `status`                  | `"הסתיים"` (ended) or `"טרם הסתיים"` (not ended)                         |
| `recipients`              | Total recipient count                                                    |
| `dialRetries`             | Configured max dial retries                                              |
| `betweenRetriesMinutes`   | Wait between retries — **in minutes** (not seconds)                      |
| `callLength`              | Configured per-call length cap                                           |
| `billing`                 | Total units charged for the campaign                                     |
| `ipRoutingUnits`          | Units used by IP routing — **present only if used**                      |
| `sttUnits`                | Units used by speech-to-text — **present only if used**                  |
| `answeredCalls`           | Count of answered calls                                                  |

#### `calls[]`

| Key        | Meaning                                                                                                                                |
|------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `phone`    | Formatted phone number                                                                                                                 |
| `name`     | Customer name from `clientes` (empty if not found)                                                                                     |
| `status`   | Call status (`answered`, `noAnswer`, `busy`, …)                                                                                        |
| `lastCall` | Last dial time, `HH:MM:SS` (Asia/Jerusalem)                                                                                            |
| `retries`  | Retry count for this specific recipient                                                                                                |
| `duration` | Call duration as `mm:ss`                                                                                                               |
| `digits`   | Key the listener pressed (if any)                                                                                                      |
| `trunk`    | Trunk used for the call                                                                                                                |
| `sipCode`  | Final SIP response code                                                                                                                |
| `q850Text` | Q.850 hangup cause, **translated to Hebrew** via the internal map (passes through verbatim if unknown)                                 |
| `sipText`  | Final SIP response text                                                                                                                |
| `had180`   | `"yes"`/`"no"` — did the destination ever return **SIP 180 Ringing** (the phone actually rang). Empty `""` if the gateway didn't supply it. |
| `had183`   | `"yes"`/`"no"` — did the destination ever return **SIP 183 Session Progress** (early media — typically a recorded message / ring-back from the carrier, *before* the call was answered). Empty `""` if not supplied. |

### Notes

- **Source table:** `campaigns_calls` for active campaigns, `campaignsEnd_YYMM` (by `end_time`) for ended ones — picked automatically.
- All values are returned as **strings** (the structured output mirrors the CSV/HTML report row-by-row).

### SIP / Q.850 failure reference

`sipCode` is the final SIP response code from the destination. `q850Text` is the ITU Q.850 hangup cause, **translated to Hebrew** by the system before being returned.

#### Successful

| SIP   | `q850Text` (returned)        | Operational meaning                                       |
|-------|------------------------------|-----------------------------------------------------------|
| `200` | `ניתוק תקין (BYE)`            | Call answered and ended normally (one side hung up).      |
| —     | `סיום רגיל (לא צוין)`        | Normal teardown without a specific cause code.            |

#### Number / dialing problem

| SIP   | `q850Text` (returned)                       | Operational meaning                                                  |
|-------|---------------------------------------------|----------------------------------------------------------------------|
| `404` | `מספר לא מוקצה (404)`                       | Number does not exist at destination carrier — **stop retrying**.    |
| `484` | `מספר שגוי / כתובת חלקית (484)`             | Number was sent partial / in invalid format.                         |
| `488` | `יעד לא תואם (488)`                          | Far end doesn't support the codec / call type — compatibility issue. |

#### Recipient unavailable

| SIP   | `q850Text` (returned)               | Operational meaning                                                  |
|-------|-------------------------------------|----------------------------------------------------------------------|
| `486` | `משתמש תפוס (486)`                  | Line is busy on another call — worth a retry.                        |
| `408` | `אין תגובת משתמש (408)`             | Far end didn't respond to the SIP request in time (timeout).         |
| `480` | `אין מענה (480)`                    | Device reachable but no one answered (off / no coverage / DND).      |
| `603` | `השיחה נדחתה (603)`                  | Recipient actively rejected — typically a "Reject" press or a block. |

#### Network / infrastructure

| SIP   | `q850Text` (returned)              | Operational meaning                                                  |
|-------|------------------------------------|----------------------------------------------------------------------|
| `502` | `יעד לא פעיל (502)`                | Destination Gateway not responding or returned an error.             |
| `503` | `אין ערוץ זמין (503)`              | No free channels at the carrier.                                     |
| `503` | `תקלה ברשת (503)`                  | General SIP-network failure at destination.                          |
| `503` | `כשל זמני (503)`                   | Temporary failure — retry is reasonable.                             |
| `503` | `עומס בציוד מיתוג (503)`           | Switching equipment too loaded to accept additional calls.           |
| `503` | `משאב לא זמין (503)`               | A required resource (codec, trunk…) isn't available.                 |

#### Internal timeout

| SIP   | `q850Text` (returned)                  | Operational meaning                                  |
|-------|----------------------------------------|------------------------------------------------------|
| `504` | `התאוששות עקב פג זמן (504)`           | Internal timeout — call was auto-aborted.            |

#### Other

| SIP | `q850Text` (returned)        | Operational meaning                                                                                  |
|-----|------------------------------|------------------------------------------------------------------------------------------------------|
| —   | `שירות מבוקש לא מנוי`        | The requested service (extension, destination, international…) isn't subscribed on the account.      |
| —   | `שגיאת פרוטוקול`             | SIP-level protocol error — malformed message / incompatibility.                                      |
| —   | `בין-רשתי`                   | Interworking issue — usually crossing between SIP and PSTN.                                          |
| —   | `לא ידוע`                    | Unclassified cause — check `sipCode` + `sipText` for more detail.                                    |

> If `q850Text` returns a value **not in this list**, that's the original English string returned by the Gateway (no auto-translation). Check `sipText` for additional context.

### Dialing indicators — `had180` / `had183`

Two boolean fields (`"yes"` / `"no"`; empty string `""` when the gateway didn't supply the signal) that help distinguish failure modes when `sipCode` alone is ambiguous — e.g. did the recipient's phone actually ring, or did the carrier play a recorded announcement before the call was rejected.

| Field    | Meaning                                                                                              | Operational use                                                                                                                                                |
|----------|------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `had180` | **SIP 180 Ringing** was received during dialing — the recipient's phone **actually rang**.            | `"yes"` + no-answer ⇒ the device was reachable but unanswered (off / DND / missed) — a retry is reasonable. `"no"` ⇒ the carrier rejected before any ring (network/number-level block). |
| `had183` | **SIP 183 Session Progress** was received — early media (carrier ring-back tone or pre-answer recorded announcement) was played before the call was answered. | `"yes"` + no-answer often indicates a carrier announcement ("subscriber unavailable", "number disconnected", etc.) — retrying immediately is unlikely to help. |

> **Availability:** these flags rely on signaling indicators (`RINGTIME` / `PROGRESSTIME`) from the upstream provider. Not all providers report them — when missing, the value is the empty string `""`.

---

## `max_ringSet` — change concurrency on a running campaign

Updates the live parallel-dial cap of an active campaign.

### Request

| Param         | Required | Notes |
|---------------|----------|-------|
| `action`      | yes      | `max_ringSet` |
| `apiKey`      | yes      | Tenant key |
| `campaignId`  | yes      | Integer |
| `max_ringSet` | yes      | New concurrency limit (integer) |

### Example

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":      "max_ringSet",
    "apiKey":      "YOUR_API_KEY",
    "campaignId":  4521,
    "max_ringSet": 10
  }'
```

### Response

```json
{ "errorCode": 0 }
```

```json
{ "errorCode": 1, "note": "סכום לא תקין" }
```

The error variant fires when the value exceeds 99999.

---

## `campaignHold` — pause a campaign

Pauses an active campaign. Unsent recipients are preserved for resumption.

| Param        | Required | Notes |
|--------------|----------|-------|
| `action`     | yes      | `campaignHold` |
| `apiKey`     | yes      | Tenant key |
| `campaignId` | yes      | Integer |

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{ "action":"campaignHold", "apiKey":"YOUR_API_KEY", "campaignId":4521 }'
```

Success: `{ "errorCode": 0 }`. The campaign's `active` field becomes `hold`.

---

## `campaignResumption` — resume a paused campaign

Restarts a campaign currently in `hold` state.

| Param        | Required | Notes |
|--------------|----------|-------|
| `action`     | yes      | `campaignResumption` |
| `apiKey`     | yes      | Tenant key |
| `campaignId` | yes      | Integer |

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{ "action":"campaignResumption", "apiKey":"YOUR_API_KEY", "campaignId":4521 }'
```

| Response                   | Meaning                                |
|----------------------------|----------------------------------------|
| `{ "errorCode": 0 }`       | Resumed                                |
| `{ "errorCode": 1 }`       | Campaign is not in `hold` state        |
| `{ "errorCode": 2 }`       | Campaign too old (> 21 days)           |

---

## `campaignStop` — hard-stop a campaign

**Irreversible.** Closes the campaign permanently. Pending recipients are discarded. Use [`campaignHold`](#campaignhold--pause-a-campaign) for reversible pauses.

| Param        | Required | Notes |
|--------------|----------|-------|
| `action`     | yes      | `campaignStop` |
| `apiKey`     | yes      | Tenant key |
| `campaignId` | yes      | Integer |

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{ "action":"campaignStop", "apiKey":"YOUR_API_KEY", "campaignId":4521 }'
```

Success: `{ "errorCode": 0 }`.

---

## `addDid` — register an outbound Caller-ID

Registers a new outbound caller-ID (DID) on the account. Ownership of the number is proven via a **silent caller-ID verification**: the system places a call whose own caller-ID display contains a one-time code, and the user reads the **last 6 digits of the calling number off their phone screen** and submits them.

> **The recipient does not need to answer the call.** No audio, no TTS, no voicemail involved — the verification code is the last 6 digits of the displayed number. Just look at the screen and copy them.

> **`addDid` ≠ recipient approval.** This action registers the **"from"** of the call (Caller-ID). It has no effect on the `phones` list (the "to"). Recipients need no registration — see [Recipient-number rules](#recipient-number-rules--read-this-before-using-campaignrun).

### Two-step flow

```
┌─────────────────────────┐         ┌──────────────────────────────────┐         ┌────────────────────────┐
│ 1. Request verification │  ───▶   │ 2. Phone rings — calling number  │  ───▶   │ 3. Submit last 6 digits│
│    (action=addDid +     │         │    on the screen ends with the   │         │    of that number      │
│    PhoneVerificationSend│         │    6-digit code. No need to      │         │    (action=addDid +    │
│    + phone)             │         │    answer.                       │         │    PhoneVerification)  │
└─────────────────────────┘         └──────────────────────────────────┘         └────────────────────────┘
```

Both steps share the same endpoint and the same `action=addDid` — what differs is whether `PhoneVerificationSend` or `PhoneVerification` is present.

### Step 1 — trigger the verification call (`PhoneVerificationSend`)

Places a call to `phone`. The **caller-ID displayed on the recipient's screen ends with a one-time 6-digit code**. The recipient simply reads those digits — there is no need to answer or listen to anything.

#### Request

| Param                     | Required | Description                                                              |
|---------------------------|----------|--------------------------------------------------------------------------|
| `action`                  | yes      | `addDid`                                                                 |
| `apiKey`                  | yes      | Tenant API key                                                           |
| `phone`                   | yes      | Phone number to register (Israeli format, e.g. `0501234567`)             |
| `PhoneVerificationSend`   | yes      | Any truthy value (e.g. `1`) — selects the "send" branch                  |

#### Example

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":                 "addDid",
    "apiKey":                 "YOUR_API_KEY",
    "phone":                  "0501234567",
    "PhoneVerificationSend":  "1"
  }'
```

#### Response — success

```json
{ "Status": "Ok", "ErrorCode": "0" }
```

The system places the call within seconds. The recipient sees an incoming call from a number whose **last 6 digits are the verification code** — they only need to read those digits off the display. The code is valid for **10 minutes**.

#### Response — errors

| Body                                                                                      | When                                                                  |
|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `{"Status":"Error","note":"המספר כבר קיים"}`                                              | The phone is already registered as a caller-ID on this account. **Returned before** the `PhoneVerificationSend` check, so no call is placed. |
| `{"Status":"Error","ErrorCode":"1","note":"המספר אינו תקין"}`                             | The phone value is shorter than 5 digits / fails numeric sanity.       |
| `{"Status":"Error","ErrorCode":"1","note":"המערכת זיהתה מספר נסיונות מרובים בשעה האחרונה למספר שנבחר, נסה שנית מאוחר יותר"}` | More than 5 verification calls were placed to this number in the last 24 hours (rate limit). |

### Step 2 — confirm code (`PhoneVerification`)

Submits the **last 6 digits of the calling number** the recipient saw on their screen, and on success registers the phone as a caller-ID for the account.

#### Request

| Param                | Required | Description                                                                              |
|----------------------|----------|------------------------------------------------------------------------------------------|
| `action`             | yes      | `addDid`                                                                                 |
| `apiKey`             | yes      | Tenant API key                                                                           |
| `phone`              | yes      | Same phone sent in step 1                                                                |
| `PhoneVerification`  | yes      | The **last 6 digits of the displayed calling number**. Non-digit characters are stripped. |

#### Example

```bash
curl -X POST https://app.ipsales.co.il/campaignApi.php \
  -H "Content-Type: application/json" \
  -d '{
    "action":             "addDid",
    "apiKey":             "YOUR_API_KEY",
    "phone":              "0501234567",
    "PhoneVerification":  "482917"
  }'
```

#### Response — success

```json
{ "Status": "Ok", "ErrorCode": "0" }
```

The number is now appended to the account's caller-ID list and may be used as `callId` in `campaignRun`.

#### Response — failure

```json
{ "Status": "Error", "note": "האימות נכשל" }
```

Returned when the (`phone`, `code`) pair doesn't match any record. Re-trigger step 1 to receive a new code.

### Notes

- **Verification is by caller-ID display only** — no answer required, no audio plays. This survives DND, silent mode, and rejected calls equally well.
- **Response shape uses capital-S `Status`** (`"Ok"` / `"Error"`) — note the capitalisation differs from `campaignRun`'s lowercase `status`.
- **Rate limit:** max 5 verification calls per phone per 24h — applies before any code is generated.
- **Code TTL:** 10 minutes from issue. Treat that as the contract.
- **Idempotency:** placing step 1 again before consuming the previous code does **not** invalidate the previous code — the latest code is the one to use.

---

## FAQ — common AI-agent questions

### "I got `errorCode: 6` — how do I 'approve' a number / add it to clientes / whitelist it?"

**You don't, and there is no such endpoint.** errorCode 6 does not mean "missing approval" — it means **every number in your `phones` array was rejected by format validation or the per-account opt-out list**. The word "מאושרים" in the Hebrew error is a misleading translation; the system has no recipient-approval concept. See [Recipient-number rules](#recipient-number-rules--read-this-before-using-campaignrun). To fix: ensure every number matches one of the three Israeli patterns (`02/03/04/08/09` 9-digit, `07X` 10-digit, `05X` 10-digit) and is not on the account opt-out list.

### "Is there a separate API for a single outbound call (click-to-call), not a broadcast?"

**No, and you don't need one.** `campaignRun` with `phones: ["0501234567"]` (one number) is the canonical pattern. Same endpoint, same shape. See [Example — single outbound "ring my agent"](#example--single-outbound-ring-my-agent-with-key-routing-to-my-ivr).

### "How do I connect the answered call to my own IVR / `main.php`?"

You have two options, depending on whether you want to bridge *after a keypress* or drive the *whole call*:

**A) Bridge the live call after a keypress** — use `keysAction-N`:
- `routing-0XXXXXXXXX` — bridge to a phone number (PSTN)
- `pbxRouting-EXT` — bridge to a PBX extension on your account
- `sipRouting-sip@host` — bridge to a SIP URI

A `keysAction` **cannot** point at an HTTP URL (no `urlRouting-`). If your IVR is a web URL, front it with one of those three, or host it on this platform and `pbxRouting-` to its extension.

**B) Drive the whole answered call from your web service** — use `messagesType=apiUrl` with `apiUrl=https://…`. The platform hands the call to your HTTPS endpoint from the moment it is answered. See [API model](#api-model-messagestypeapiurl).

### "What's the difference between `addDid` and `phones`?"

- `phones` = **the people you're calling** (the "to" of the call). Just put valid Israeli numbers in the array.
- `addDid` registers a **Caller-ID** (the "from" — what shows on the recipient's screen). Used later via `callId` in `campaignRun`. Most accounts have a default DID, so `callId` is optional.

These are orthogonal — fixing one does not affect the other.

### "What parameters does `campaignRun` require, exactly?"

The strict minimum for a working call:

| Required | Notes |
|---|---|
| `action` | `campaignRun` |
| `apiKey` | Tenant key |
| `phones` | At least one valid Israeli number |
| One of: `audioFile` / `audioText` / `messagesType=extensionActivation`+`extensionActivation` / `messagesType=apiUrl`+`apiUrl` | What to do on answer |
| `betweenRetries` | 5–120 minutes (effectively required — there's no implicit safe default) |

Everything else is optional. Full parameter list above in [campaignRun → Request parameters](#request-parameters).

### "Will my server's IP work?"

Only if it's been **whitelisted by support** in advance. Any IP that wasn't pre-approved gets `errorCode: -99 / Access denied` regardless of how correct your auth is. This is account-level config, not something you fix in code.

### "How do I supply a phone list bigger than fits in a JSON body?"

Submit `phones` as a **`multipart/form-data` file upload** — a plain text file with comma-separated numbers. Use the same field name `phones`.

---

## Quick reference — all actions at a glance

| Action               | Required params (beyond `action`+`apiKey`)                                  | Purpose                                            |
|----------------------|------------------------------------------------------------------------------|----------------------------------------------------|
| `campaignRun`        | `phones`, one of `audioFile`/`audioText`/`extensionActivation`/`apiUrl`, `betweenRetries` | Create + start a campaign                          |
| `campaignsHistory`   | —                                                                            | List campaigns in a date range                     |
| `campaignReport`     | `campaignId`                                                                 | Full per-recipient report                          |
| `max_ringSet`        | `campaignId`, `max_ringSet`                                                  | Change concurrency on a running campaign           |
| `campaignHold`       | `campaignId`                                                                 | Pause                                              |
| `campaignResumption` | `campaignId`                                                                 | Resume a paused campaign                           |
| `campaignStop`       | `campaignId`                                                                 | Hard-stop (irreversible)                           |
| `addDid`             | `phone` + (`PhoneVerificationSend` for step 1, `PhoneVerification` for step 2) | Register an outbound Caller-ID                     |

> Reminder: all requests are POST to `https://app.ipsales.co.il/campaignApi.php` with `apiKey` on every call, from a pre-whitelisted server IP.
