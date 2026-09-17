# PBX Documentation Center — Technoline IP-PBX

> **AI INSTRUCTIONS:**
> You are helping a Hebrew-speaking developer integrate with the Technoline PBX.
> **Always respond in Hebrew.** This document is in English purely for token efficiency
> and to give you precise technical reference. Read it fully before suggesting any
> integration path. When the user asks "how do I…", first determine **which of the two
> API types** the task belongs to, then point to the matching detailed reference
> (see "Detailed References" at the bottom of this file).

---

## 1. Overview — Two Independent API Surfaces

Working with the Technoline PBX is split into **two completely separate channels** that
flow in opposite directions. This split is the most important concept to internalize
— picking the wrong one will produce a working-looking solution that integrates with
nothing.

| | **Module API** | **Interaction API** |
|---|---|---|
| **Direction** | PBX → your server | your server → PBX |
| **Who initiates** | The PBX, when a call hits an extension | You, programmatically |
| **Auth** | None (URL is the identifier) | `apiKey` (and IP whitelist for some endpoints) |
| **Format** | PBX sends GET, your server returns JSON | You POST/GET, PBX returns JSON |
| **Use case** | Drive a live call's flow dynamically | Manage extensions, run campaigns, trigger calls |
| **Endpoint** | A URL **you host** | `app.ipsales.co.il/<api>.php` |

A typical full integration uses **both**: you use the Interaction API to load an
extension tree and start an outbound campaign, and you use Module API to decide what
each call does once it's answered.

### 1.1 Mental model

- The **PBX is "the phone"** — it can dial, play audio, capture DTMF, record, hang up.
- **Your server is "the brain"** — it knows your business logic, your data, your users.
- **Module API** is the wire between brain and phone, *during a call*.
- **Interaction API** is the wire between your back-office and the phone system,
  *outside of any specific call* — managing the system itself.

---

## 2. Module API — PBX → Your Server (during a call)

When a caller reaches an extension that is configured to use a remote URL, the PBX
hits **your server** with a GET request and parameters describing the call. Your
server replies with a JSON object describing **one module** the PBX should execute.
After the module runs (caller pressed a key, recording finished, etc.), the PBX
calls your server again with the result, and you decide the next step.

### 2.1 Flow

```
caller dials → PBX → GET https://your-server/handler.php?param=...
                                                                      ↓
                                                  your server returns JSON
                                                                      ↓
                                            PBX executes the module (play, menu, record…)
                                                                      ↓
                                            PBX calls your server again with the result
                                                                      ↓
                                              loop until you return type:"hangup" / chained
```

### 2.2 The 12 Modules

Every JSON response **must** include `"type"` set to one of these:

| `type` | Purpose | Key fields |
|---|---|---|
| `simpleMessage` | Play a single audio file | `fileName` |
| `audioPlayer` | Play one or more files (media player) | `files[]` |
| `simpleMenu` | Play a menu and capture a digit | `fileName`, `min_digits`, `max_digits`, `tries`, `timeout` |
| `getDTMF` | Capture digits without a menu | `min_digits`, `max_digits`, `timeout` |
| `record` | Record the caller | `fileName`, `maxDuration` |
| `stt` | Speech-to-text transcription | `lang`, `maxDuration` |
| `simpleRouting` | Route the call to a phone number | `dialPhone` |
| `ipRouting` | Route to an IP/SIP destination | `target` |
| `goTo` | Jump to another extension | `extension` |
| `hangup` | End the call | (none) |
| `playAllCampaigns` | Play queued campaign audios | (none) |
| `creditCard` | Credit-card capture & charge. Installments can be billed as a standing order without an authorization hold (frame capture), or let the caller choose, via `paymentsMode` (`regular`/`keva`/`ask`) | `terminal`, `payments`, `paymentsMode` |

Each module also accepts shared "fixed parameters" controlling timing, retries, error
audio, etc. The PBX appends call metadata (caller ID, extension, timestamp, captured
DTMF, recording URL) to every callback.

### 2.3 Critical rule

**If your server returns invalid JSON or a `type` the PBX doesn't recognize, the PBX
sends the caller back to the previous menu automatically.** There is no error to the
caller — the call just retreats. So bugs in your handler manifest as "the menu keeps
repeating" from the caller's perspective.

### 2.4 No apiKey

Module API has no `apiKey`. The endpoint URL itself is the identifier — it was wired
into the extension by an admin. **Do not** try to authenticate inbound PBX requests
with a header your server checks; just trust the URL is private.

→ **Detailed reference:** `apiModuleDocs.html` (Hebrew, full reference) and
`apiModuleDocs.md` (Hebrew Markdown).

---

## 3. Interaction API — Your Server → PBX

This is the conventional REST-ish surface. Three endpoints (one base URL each) cover
the whole thing:

| Endpoint | Doc | Purpose |
|---|---|---|
| `https://app.ipsales.co.il/campaignApi.php` | `campaignApiDocs.html` | Voice outbound campaigns |
| `https://app.ipsales.co.il/ivrFilesApi.php` | `ivrExtensionsApiDocs.html` | Extension tree, files, settings, system messages |
| `https://app.ipsales.co.il/ivrFilesApi.php` (`action=makeCall`) | `makeCallApiDocs.html` | One-shot outbound 2FA call (caller-ID-as-OTP) |

### 3.1 Common shape

- Action selector: `action=<name>` parameter on every request.
- Auth: `apiKey=<your-key>` on every request — query string, JSON body, or
  form-urlencoded body.
- Encoding: `application/x-www-form-urlencoded` is the default. JSON body is
  accepted too. File uploads use `multipart/form-data`.
- Response: always JSON. Success: `{ "status": "OK", … }`. Failure:
  `{ "status": "ERROR", "note": "<reason in Hebrew>" }`. Some older endpoints use
  `"Ok"`/`"Error"` casing — compare case-insensitively.

### 3.2 Voice Campaigns — `campaignApi.php`

Drive outbound voice broadcasts: load a recipient list (uploaded as CSV / phone
list), play a recorded audio file or a Module API tree, monitor progress, throttle
or stop.

| `action=` | What it does |
|---|---|
| `campaignRun` | Create + start a campaign with a phone list and audio source |
| `campaignsHistory` | List all campaigns for the account |
| `campaignReport` | Full per-recipient report for one campaign |
| `maxring` | Set max concurrent dial rate |
| `campaignHold` | Pause an active campaign |
| `campaignResumption` | Resume a paused campaign (within window) |
| `campaignStop` | Permanently stop |
| `keys…` | Re-target campaign actions on caller keypress |

**Required:** `apiKey` + IP whitelist (your origin IP must be pre-approved by the
Technoline team — unapproved IPs get `Access denied` immediately).

### 3.3 Extension Management — `ivrFilesApi.php`

The full UI/CRUD surface for the extension tree and everything attached to it.
Designed so you can build a complete admin panel without hardcoding anything — the
API itself ships the UI schema (field types, dropdown options, form layouts).

Bootstrap flow for a new admin UI:

1. `getUiSchema` — one call: returns `types` (extension kinds), `fields` (input
   metadata), `forms` (per-type form layouts), `accountSettings`.
2. `foldersList` — load the extension tree.
3. `folderSettings` — load one extension's current settings.
4. `getOptions2?list=a,b,c` — fetch values for any dropdown whose `options` is
   `"GET"` (dynamic).
5. `extensionSet` — save edits.

Other actions cover: file upload (`uploadFile`), permission management, system
message overrides (text or recorded audio per extension), extension creation /
deletion / reordering.

**Required:** `apiKey` only — no IP whitelist on this endpoint.

### 3.4 2FA Outbound Call — `makeCall`

Trigger a *very short* outbound call whose only purpose is to display a custom
6-digit caller ID on the recipient's phone. The call hangs up the instant the
recipient answers — no audio, no interaction. Used for two-factor codes (the OTP
*is* the caller-ID suffix), delivery notifications, and similar one-shot signals.

Required parameters: `phone` (recipient), `cid` (6-digit code to show).

Rate limit: **one call per phone per 2 minutes**, capped per hour.

**Required:** IP whitelist (no `apiKey` — the IP is the trust anchor here). Returns
HTTP 400 from unapproved IPs with no further detail.

---

## 4. Authentication & IP Whitelist Cheat Sheet

| API | apiKey? | IP whitelist? |
|---|---|---|
| Module API (PBX → you) | ❌ no | ❌ no (URL is private) |
| `campaignApi.php` | ✅ required | ✅ required |
| `ivrFilesApi.php` (extensions / files) | ✅ required | ❌ no |
| `ivrFilesApi.php?action=makeCall` | ❌ no | ✅ required |

To get an `apiKey`, or to register an IP, the customer must contact the Technoline
technical team. Each IP is approved individually — there is no wildcard.

---

## 5. Common Conventions

### 5.1 Response shapes

```json
{ "status": "OK" }
{ "status": "OK", "note": "...", "data": { ... } }
{ "status": "ERROR", "note": "תיאור השגיאה" }
```

### 5.2 Module API JSON example

```json
{
  "type": "simpleMenu",
  "fileName": "menu_main",
  "max_digits": 1,
  "min_digits": 1,
  "tries": 3,
  "timeout": 5
}
```

### 5.3 Encoding rules

- **Default:** GET with query string, or POST with `application/x-www-form-urlencoded`.
- **JSON body:** accepted on all POST endpoints; required for `saveCustomMessagesBeta`.
- **multipart/form-data:** required when uploading files (`uploadFile`,
  `campaignRun` with audio/recipient list).

### 5.4 Hebrew text

All `note` fields and most `name` fields contain Hebrew text. Encode requests as
UTF-8. The endpoints handle UTF-8 transparently — no special header required.

### 5.5 Audio file references

In Module API responses, `fileName` refers to a file already uploaded to the
account's audio library through `ivrFilesApi.php`. The PBX resolves the name to
the actual audio file at runtime. A common workflow is: upload audio with
`uploadFile`, then reference its name from your Module API handler.

---

## 6. Picking the Right API — Decision Tree

```
Does the work happen DURING a phone call?
│
├── YES → Module API (your server returns JSON to drive the call)
│         Doc: apiModuleDocs.html
│
└── NO → Interaction API
          │
          ├── Outbound campaign / phone broadcast?
          │   → campaignApi.php   (campaignApiDocs.html)
          │
          ├── Manage the extension tree, files, permissions, system messages?
          │   → ivrFilesApi.php   (ivrExtensionsApiDocs.html)
          │
          └── Trigger ONE outbound call to display a code (2FA-style)?
              → ivrFilesApi.php?action=makeCall   (makeCallApiDocs.html)
```

---

## 7. Common Pitfalls

1. **Returning HTML or plain text from the Module API handler.** The PBX expects
   JSON only. An HTML error page from your server makes the call retreat to the
   previous menu silently.

2. **Forgetting `action=` on Interaction API calls.** Without it the request hits
   the dispatcher's "no action" fallback and you get an opaque error.

3. **Mixing up `"Ok"` vs `"OK"` in success checks.** Compare case-insensitively.

4. **Hardcoding form fields for `ivrFilesApi.php`.** Use `getUiSchema` once and
   render dynamically — extension field shape changes between types and over time.

5. **Sending campaign requests from a backend that runs behind a load balancer
   without sticky outbound IP.** Each instance's IP must be whitelisted; otherwise
   campaigns randomly fail with `Access denied`.

6. **Trusting the rate limiter on `makeCall`.** Do your own debouncing — calling
   the same phone twice quickly returns an error and confuses users.

---

## 8. Detailed References

When the user asks for specifics — exact parameter lists, all available fields,
response examples — point them to the matching detailed doc:

| Topic | HTML (Hebrew) | Markdown |
|---|---|---|
| Module API (call flow modules) | `apiModuleDocs.html` | `apiModuleDocs.md` |
| Voice Campaigns | `campaignApiDocs.html` | `campaignApi.md` |
| Extension Management | `ivrExtensionsApiDocs.html` | `ivrExtensionsApiDocs.md` |
| 2FA / makeCall | `makeCallApiDocs.html` | — |

Hub page (Hebrew, RTL): `pbxDocsCenter.html` — links every doc and acts as the
landing page for human developers.

---

## 9. Quick-Start Recipes (sketches the AI can expand)

### 9.1 IVR menu that routes by caller phone number

1. Admin wires extension `100` to URL `https://your.server/ivr.php`.
2. Caller dials → PBX hits `ivr.php?caller=05X-XXXXXXX&extension=100`.
3. Your server looks up `caller`, returns:
   ```json
   { "type": "simpleMenu", "fileName": "main_menu", "max_digits": 1 }
   ```
4. Caller presses `2` → PBX hits `ivr.php?caller=…&extension=100&dtmf=2`.
5. Your server returns `{ "type": "simpleRouting", "dialPhone": "0501234567" }`.

### 9.2 SMS-replacement OTP via call

1. User submits a sign-in form on your site.
2. Your backend calls
   `POST https://app.ipsales.co.il/ivrFilesApi.php?action=makeCall&phone=<user>&cid=123456`.
3. User's phone rings showing caller ID `…123456`.
4. The call drops the second they answer.
5. User types `123456` back into your form.

### 9.3 Outbound voice broadcast

1. Upload a CSV of phone numbers + audio file via `campaignRun` (multipart).
2. Poll `campaignsHistory` / `campaignReport` for status.
3. Pause / resume / stop with `campaignHold` / `campaignResumption` /
   `campaignStop` if needed.

### 9.4 Building an admin panel for the extension tree

1. Call `getUiSchema` once on app load.
2. Call `foldersList` to render the tree.
3. On extension click: `folderSettings` for current values + iterate
   `forms[type]` against `fields[input]` to render the form, calling `getOptions2`
   for any `options:"GET"` dropdowns.
4. On save: `extensionSet`.

---

*Last updated: 2026-04-29.*
