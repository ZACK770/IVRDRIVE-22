# Technoline raw-channel probe

Discovers the undocumented wire protocol of the Technoline PBX streaming
("raw") channel. One real test call is enough to answer every open question.

The probe never rejects a connection and never assumes an encoding. It accepts
whatever arrives, records it byte for byte, and reports what it saw.

## What one test call tells you

| Open question | Where the answer shows up |
|---|---|
| Binary frames or JSON+base64? | `frame_kinds` in the summary; text frames are dumped verbatim |
| PCM16 LE/BE, mu-law or A-law? | `codec_verdict.ranked` — plus four WAV renderings to listen to |
| Frame size and cadence | `common_frame_sizes`, `inferred_frame_ms`, per-frame `dt_ms` |
| How the Bearer secret is sent | `handshake.headers` (redacted), `query_params`, first text frame |
| Is there a handshake with caller ID? | `first_text_frames` |
| Does the outbound direction work? | echo loopback — if the caller hears themselves, framing is right |

## Codec detection

Speech sampled at 8kHz is smooth: neighbouring samples are close together. A
wrong endianness or a wrong companding law scrambles the low bits and turns the
waveform into noise. The probe scores each candidate by mean absolute
sample-to-sample delta over RMS (`roughness`); the real encoding scores well
below 1.0 while the wrong ones sit far above it. The WAV renderings are the
human-audible confirmation — exactly one will sound like a voice.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
python tools/fake_pbx.py --mode binary-mulaw   # or binary-pcm16le / json-base64
```

Then open <http://127.0.0.1:8000/> for the capture list.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PROBE_ECHO` | `loopback` | `off`, `loopback` (echo inbound frames back), `tone` (periodic 440Hz) |
| `PROBE_ECHO_DELAY_MS` | `700` | Delay before echoing, so the caller can tell it apart from sidetone |
| `PROBE_TONE_CODEC` | `mulaw` | Encoding for `tone` mode: `mulaw`, `pcm16le`, `pcm16be` |
| `PROBE_BEARER_SECRET` | unset | Expected Bearer value; only reported, not enforced |
| `PROBE_ENFORCE_BEARER` | `0` | Set to `1` only after the protocol is known — rejecting during discovery hides data |
| `PROBE_CAPTURE_DIR` | `captures` | Where captures are written |

## Test call procedure

1. Deploy, confirm `/healthz`.
2. Point the PBX streaming channel at `wss://<host>/ws/ivr` (already registered).
3. Call in. Speak continuously for ~10 seconds — count out loud, do not stay
   silent, since silence is smooth under every candidate encoding and makes
   detection inconclusive. Listen for your own voice echoed back.
4. Open `/`, click the call, read the verdict and listen to the four WAVs.
