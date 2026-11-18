// Devin phone line: a Technoline PBX "api" extension that lets Isaac call the QA Bot,
// hear its status, leave spoken instructions, and get a missed call when it needs him.

const PBX_API = "https://api.tlivr.com";
const DEVIN_API = "https://api.devin.ai/v3";
const UA = "devin-phone-line/1.0";
const MENU =
  "לשמיעה חוזרת של העדכון הקש 1. להשארת הוראה, תשובה או שאלה לדבין הקש 2. לשמיעת התשובות האחרונות של דבין הקש 3. לסיום הקש 0.";
const NO_STATUS = "עוד אין לי עדכון מוכן. אפשר להשאיר הוראה לדבין בהקשה על 2.";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") return json({ ok: true });
    if (url.searchParams.get("key") !== env.CALLBACK_KEY || !env.CALLBACK_KEY) {
      return json({ error: "forbidden" }, 403);
    }
    if (url.pathname === "/pbx") {
      const params = await readParams(request, url);
      return json(await handleCall(params, env, ctx));
    }
    if (url.pathname === "/refresh" && request.method === "POST") {
      try {
        return json(await refreshStatus(env, { notify: url.searchParams.get("notify") === "1" }));
      } catch (error) {
        return json({ error: String(error.message || error) }, 502);
      }
    }
    return json({ error: "not found" }, 404);
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(refreshStatus(env, { notify: true }));
  },
};

export async function handleCall(p, env, ctx) {
  if (env.PBX_SYSTEM && p.PBXnum && p.PBXnum !== env.PBX_SYSTEM) return { type: "hangup" };
  if (p.PBXcallStatus === "HANGUP") {
    if (p.FILEID_msg) ctx.waitUntil(relayRecording(p, env));
    return {};
  }
  if (!allowedCaller(p.PBXphone, env) || p.bye) return { type: "hangup" };

  const consumed = ["choice", "msg", "FILE_msg", "FILEID_msg", "PATH_msg", "SIZE_msg", "DURATION_msg", "DIGIT_msg", "TEXT_msg"];

  if (p.FILEID_msg) {
    ctx.waitUntil(relayRecording(p, env));
    return menu("ההודעה התקבלה ומועברת לדבין. בעוד דקה או שתיים אפשר לשמוע את התשובה בהקשה על 3. " + MENU, consumed);
  }

  const choice = p.choice;
  if (!choice) {
    const status = await currentStatus(env, ctx);
    return menu(`שלום אייזיק, כאן דבין. ${status} ${MENU}`, consumed);
  }
  if (choice === "1") return menu(`${await currentStatus(env, ctx)} ${MENU}`, consumed);
  if (choice === "2") {
    return {
      type: "record",
      name: "msg",
      max: 180,
      min: 1,
      confirm: "no",
      hangupSave: "yes",
      sttSoft: true,
      fileName: "devin_{{PBXcallId}}",
      files: [{ text: "דבר אחרי הצליל, ובסיום הקש סולמית." }],
      removeParams: ["choice"],
    };
  }
  if (choice === "3") return menu(`${await latestReplies(env)} ${MENU}`, consumed);
  return { type: "simpleMessage", files: [{ text: "להתראות." }], removeParams: consumed, addParams: { bye: "1" } };
}

function menu(text, removeParams) {
  return {
    type: "simpleMenu",
    name: "choice",
    files: speechFiles(text),
    times: 2,
    timeout: 8,
    errorReturn: "0",
    post: true,
    removeParams,
  };
}

export function speechFiles(text) {
  const parts = [];
  let current = "";
  for (const sentence of String(text).split(/(?<=[.!?:])\s+/)) {
    if (current && (current + " " + sentence).length > 350) {
      parts.push(current);
      current = sentence;
    } else {
      current = current ? `${current} ${sentence}` : sentence;
    }
  }
  if (current) parts.push(current);
  return parts.map((part) => ({ text: part }));
}

export function allowedCaller(phone, env) {
  const allowed = String(env.ALLOWED_CALLERS || "").split(",").map(normalizePhone).filter(Boolean);
  return allowed.length > 0 && allowed.includes(normalizePhone(phone));
}

export function normalizePhone(phone) {
  let digits = String(phone || "").replace(/\D/g, "");
  if (digits.startsWith("972")) digits = "0" + digits.slice(3);
  return digits;
}

async function readParams(request, url) {
  const params = Object.fromEntries(url.searchParams);
  delete params.key;
  if (request.method === "POST") {
    const type = request.headers.get("content-type") || "";
    if (type.includes("application/json")) Object.assign(params, await request.json().catch(() => ({})));
    else Object.assign(params, Object.fromEntries(new URLSearchParams(await request.text())));
  }
  return params;
}

async function currentStatus(env, ctx) {
  const cached = await env.STATE.get("status", "json");
  const age = cached ? Date.now() - cached.at : Infinity;
  if (cached && age < 10 * 60_000) return cached.speech;
  const fresh = withTimeout(refreshStatus(env, { notify: false }), 7000);
  if (!cached) {
    const result = await fresh.catch(() => null);
    return result?.speech || NO_STATUS;
  }
  ctx.waitUntil(fresh.catch(() => null));
  return `העדכון האחרון מלפני ${Math.round(age / 60_000)} דקות. ${cached.speech}`;
}

// ---- Devin API -------------------------------------------------------------

async function devin(env, path, init = {}) {
  const res = await fetch(`${DEVIN_API}/organizations/${env.DEVIN_ORG_ID}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${env.DEVIN_API_KEY}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!res.ok) throw new Error(`Devin API ${res.status} ${path.split("?")[0]}`);
  return res.json();
}

const sessionId = (id) => (id.startsWith("devin-") ? id : `devin-${id}`);

// Messages are returned oldest first; keep a cursor and a rolling buffer in KV.
async function syncMessages(env) {
  const state = (await env.STATE.get("messages", "json")) || { cursor: null, items: [] };
  for (let page = 0; page < 20; page++) {
    const query = new URLSearchParams({ first: "200" });
    if (state.cursor) query.set("after", state.cursor);
    const data = await devin(env, `/sessions/${sessionId(env.BOT_SESSION_ID)}/messages?${query}`);
    for (const item of data.items || []) {
      state.items.push({ source: item.source, text: String(item.message || "").slice(0, 3000), at: Number(item.created_at) * 1000 });
    }
    if (data.end_cursor) state.cursor = data.end_cursor;
    if (!data.has_next_page) break;
  }
  state.items = state.items.slice(-60);
  await env.STATE.put("messages", JSON.stringify(state));
  return state.items;
}

async function childSessions(env) {
  const bot = await devin(env, `/sessions/${sessionId(env.BOT_SESSION_ID)}`);
  const ids = (bot.child_session_ids || []).slice(-12);
  const children = await Promise.all(
    ids.map((id) => devin(env, `/sessions/${sessionId(id)}`).catch(() => null)),
  );
  return children.filter(Boolean).map((s) => ({
    title: s.title,
    status: s.status_detail || s.status,
    updated: new Date(Number(s.updated_at) * 1000).toISOString(),
    pull_requests: (s.pull_requests || []).map((pr) => pr.pr_url || pr.url).filter(Boolean),
  }));
}

export async function refreshStatus(env, { notify }) {
  const [messages, children] = await Promise.all([syncMessages(env), childSessions(env).catch(() => [])]);
  const fingerprint = `${messages.length}:${messages.at(-1)?.at}:${children.map((c) => c.status + c.updated).join()}`;
  const cached = await env.STATE.get("status", "json");
  if (cached?.fingerprint === fingerprint) {
    cached.at = Date.now();
    await env.STATE.put("status", JSON.stringify(cached));
    return cached;
  }
  const transcript = messages
    .slice(-40)
    .map((m) => `[${new Date(m.at).toISOString()}] ${m.source === "devin" ? "דבין" : "אייזיק/מערכת"}: ${m.text}`)
    .join("\n");
  const summary = await gemini(env, [
    {
      text:
        "אתה דבין, עובד תוכנה שמדווח בטלפון לבוס שלו, אייזיק. לפניך השיחה האחרונה בצ'אט ורשימת המשימות שרצות. " +
        "כתוב עדכון קולי קצר בעברית מדוברת (עד 90 שניות הקראה, משפטים קצרים, בלי קישורים, בלי סימני markdown, בלי מזהים טכניים). " +
        "קודם מה מחכה לאייזיק (שאלות או אישורים שרק הוא יכול לתת), אחר כך מה התקדם בכל פרויקט, ומה הלאה. " +
        'החזר JSON: {"speech": string, "needs_isaac": boolean, "question_digest": string} כאשר question_digest הוא משפט קצר שמתאר את השאלה הפתוחה החשובה ביותר, או מחרוזת ריקה.' +
        `\n\nהשעה עכשיו: ${new Date().toISOString()}\n\nמשימות:\n${JSON.stringify(children)}\n\nצ'אט:\n${transcript}`,
    },
  ], true);
  const status = { ...summary, fingerprint, at: Date.now() };
  await env.STATE.put("status", JSON.stringify(status));
  if (notify) await maybeNotify(status, env);
  return status;
}

async function latestReplies(env) {
  const items = await withTimeout(syncMessages(env), 6000).catch(async () => (await env.STATE.get("messages", "json"))?.items || []);
  const since = Number((await env.STATE.get("lastRelayAt")) || 0);
  let replies = items.filter((m) => m.source === "devin" && m.at > since);
  if (!replies.length) {
    if (since) return "עדיין אין תשובה חדשה מדבין להודעה האחרונה. נסה שוב בעוד דקה.";
    replies = items.filter((m) => m.source === "devin").slice(-3);
  }
  if (!replies.length) return "אין עדיין הודעות מדבין.";
  const raw = replies.map((m) => m.text).join("\n\n");
  const spoken = await withTimeout(
    gemini(env, [{ text: "הפוך את ההודעות הבאות של דבין לטקסט להקראה בטלפון בעברית מדוברת, בלי קישורים ובלי סימני markdown, עד 60 שניות הקראה. החזר רק את הטקסט.\n\n" + raw }]),
    6000,
  ).catch(() => stripForSpeech(raw).slice(0, 900));
  return `התשובה של דבין: ${spoken}`;
}

export function stripForSpeech(text) {
  return String(text)
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_`#>|[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// ---- recordings --------------------------------------------------------------

async function relayRecording(p, env) {
  const fileId = p.FILEID_msg;
  const doneKey = `relayed:${fileId}`;
  if (await env.STATE.get(doneKey)) return;
  await env.STATE.put(doneKey, "1", { expirationTtl: 7 * 86400 });

  let text = "";
  try {
    const audio = await fetch(
      `${PBX_API}/ivrFilesApi.php?action=fileDownload&audio=${encodeURIComponent(fileId)}&apiKey=${env.PBX_API_KEY}`,
      { headers: { "User-Agent": UA } },
    );
    if (!(audio.headers.get("content-type") || "").includes("audio")) throw new Error("no audio");
    const data = base64(await audio.arrayBuffer());
    text = await gemini(env, [
      { text: "תמלל מילה במילה את ההקלטה (עברית, ייתכנו מילים באנגלית). החזר רק את התמלול." },
      { inline_data: { mime_type: "audio/mp3", data } },
    ]);
  } catch {
    text = p.TEXT_msg || "";
  }
  const when = new Date().toLocaleString("he-IL", { timeZone: "Asia/Jerusalem" });
  const message =
    `📞 הודעה קולית מאייזיק בשלוחת הטלפון (${when}, ${Math.round(Number(p.DURATION_msg) || 0)} שניות):\n\n` +
    `«${text.trim() || "התמלול נכשל — ההקלטה נשמרה במרכזייה, קובץ " + fileId}»\n\n` +
    "הוא יקשיב לתשובה שלך בטלפון (מקש 3), אז ענה כאן בעברית קצרה שמתאימה להקראה.";
  await devin(env, `/sessions/${sessionId(env.BOT_SESSION_ID)}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
  await env.STATE.put("lastRelayAt", String(Date.now()));
}

// ---- outbound missed call ----------------------------------------------------------

export function inQuietHours(date, start = 22, end = 8) {
  const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hour12: false, timeZone: "Asia/Jerusalem" }).format(date));
  return hour >= start || hour < end;
}

async function maybeNotify(status, env) {
  if (env.OUTBOUND_ENABLED !== "1" || !status.needs_isaac || !status.question_digest) return;
  if (inQuietHours(new Date())) return;
  const last = (await env.STATE.get("notified", "json")) || {};
  const minGap = Number(env.NOTIFY_MIN_GAP_MINUTES || 180) * 60_000;
  if (last.digest === status.question_digest || Date.now() - (last.at || 0) < minGap) return;
  const res = await fetch(`${PBX_API}/campaignApi.php`, {
    method: "POST",
    headers: { "User-Agent": UA, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      action: "campaignRun",
      apiKey: env.PBX_API_KEY,
      phones: [env.NOTIFY_PHONE],
      title: "דבין צריך אותך",
      messagesType: "RH",
    }),
  });
  await env.STATE.put("notified", JSON.stringify({ digest: status.question_digest, at: Date.now(), ok: res.ok }));
}

// ---- helpers -----------------------------------------------------------------

async function gemini(env, parts, asJson = false) {
  const model = env.GEMINI_MODEL || "gemini-flash-latest";
  const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
    body: JSON.stringify({
      contents: [{ role: "user", parts }],
      generationConfig: {
        thinkingConfig: { thinkingBudget: 0 },
        ...(asJson ? { responseMimeType: "application/json" } : {}),
      },
    }),
  });
  if (!res.ok) throw new Error(`Gemini ${res.status}`);
  const data = await res.json();
  const text = (data.candidates?.[0]?.content?.parts || []).map((part) => part.text || "").join("");
  if (!asJson) return text.trim();
  const parsed = JSON.parse(text);
  return { speech: stripForSpeech(parsed.speech || NO_STATUS), needs_isaac: !!parsed.needs_isaac, question_digest: parsed.question_digest || "" };
}

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);
}

function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}
