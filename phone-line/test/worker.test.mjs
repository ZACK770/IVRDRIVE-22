import assert from "node:assert/strict";
import { test } from "node:test";
import worker, { allowedCaller, handleCall, inQuietHours, normalizePhone, notifyFields, speechFiles, stripForSpeech } from "../src/worker.js";

function kv(initial = {}) {
  const store = new Map(Object.entries(initial).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]));
  return {
    store,
    async get(key, type) {
      const value = store.get(key);
      return value === undefined ? null : type === "json" ? JSON.parse(value) : value;
    },
    async put(key, value) {
      store.set(key, value);
    },
  };
}

const env = (extra = {}) => ({
  CALLBACK_KEY: "k",
  PBX_SYSTEM: "0765673575",
  ALLOWED_CALLERS: "0527180504",
  STATE: kv({ status: { speech: "הכל מתקדם.", at: Date.now() } }),
  ...extra,
});
const ctx = () => ({ tasks: [], waitUntil(p) { this.tasks.push(p.catch(() => {})); } });
const call = (extra) => ({ PBXphone: "0527180504", PBXnum: "0765673575", PBXcallId: "c1", ...extra });

test("phone normalisation and allowlist", () => {
  assert.equal(normalizePhone("+972-52-718-0504"), "0527180504");
  assert.ok(allowedCaller("972527180504", env()));
  assert.ok(!allowedCaller("0501234567", env()));
  assert.ok(!allowedCaller("0527180504", { ALLOWED_CALLERS: "" }));
});

test("rejects bad key and other systems", async () => {
  const res = await worker.fetch(new Request("https://x/pbx?key=bad"), env(), ctx());
  assert.equal(res.status, 403);
  assert.deepEqual(await handleCall(call({ PBXnum: "0761111111" }), env(), ctx()), { type: "hangup" });
  assert.deepEqual(await handleCall(call({ PBXphone: "0501234567" }), env(), ctx()), { type: "hangup" });
});

test("entry plays cached status and the menu", async () => {
  const res = await worker.fetch(new Request("https://x/pbx?key=k&PBXphone=0527180504&PBXnum=0765673575&PBXcallId=c1"), env(), ctx());
  const body = await res.json();
  assert.equal(body.type, "simpleMenu");
  assert.equal(body.name, "choice");
  const spoken = body.files.map((f) => f.text).join(" ");
  assert.match(spoken, /הכל מתקדם/);
  assert.match(spoken, /הקש 2/);
});

test("choice 2 records, recording is relayed once and returns to the menu", async () => {
  const rec = await handleCall(call({ choice: "2" }), env(), ctx());
  assert.equal(rec.type, "record");
  assert.equal(rec.name, "msg");
  assert.equal(rec.hangupSave, "yes");

  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("fileDownload")) return new Response(new Uint8Array([1, 2, 3]), { headers: { "content-type": "audio/mp3" } });
    if (String(url).includes("generativelanguage")) return Response.json({ candidates: [{ content: { parts: [{ text: "תבדוק את הוויז" }] } }] });
    return Response.json({ ok: true });
  };
  try {
    const e = env({ DEVIN_API_KEY: "d", DEVIN_ORG_ID: "org", BOT_SESSION_ID: "devin-bot", PBX_API_KEY: "p", GEMINI_API_KEY: "g" });
    const c = ctx();
    const after = await handleCall(call({ choice: "", FILEID_msg: "F1", DURATION_msg: "4" }), e, c);
    assert.equal(after.type, "simpleMenu");
    assert.ok(after.removeParams.includes("FILEID_msg"));
    await handleCall(call({ PBXcallStatus: "HANGUP", FILEID_msg: "F1" }), e, c);
    await Promise.all(c.tasks);
    const posts = calls.filter((x) => x.url.endsWith("/sessions/devin-bot/messages"));
    assert.equal(posts.length, 1);
    assert.match(JSON.parse(posts[0].init.body).message, /תבדוק את הוויז/);
    assert.ok(await e.STATE.get("lastRelayAt"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("0 says goodbye then hangs up", async () => {
  const bye = await handleCall(call({ choice: "0" }), env(), ctx());
  assert.equal(bye.type, "simpleMessage");
  assert.deepEqual(bye.addParams, { bye: "1" });
  assert.deepEqual(await handleCall(call({ bye: "1" }), env(), ctx()), { type: "hangup" });
});

test("speech helpers", () => {
  assert.equal(stripForSpeech("**PR** https://github.com/x [link](y)"), "PR link y");
  const files = speechFiles("א. ".repeat(300));
  assert.ok(files.length > 1 && files.every((f) => f.text.length <= 360));
  assert.ok(inQuietHours(new Date("2026-11-18T22:30:00Z")));
  assert.ok(!inQuietHours(new Date("2026-11-18T10:00:00Z")));
});

test("outbound call reads the question unless flash mode", () => {
  const voice = notifyFields("צריך אישור על **PR**", {});
  assert.match(voice.audioText, /צריך אישור על PR/);
  assert.ok(!voice.messagesType);
  assert.deepEqual(notifyFields("x", { NOTIFY_MODE: "flash" }), { messagesType: "RH" });
});
