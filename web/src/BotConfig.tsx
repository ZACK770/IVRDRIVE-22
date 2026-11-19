import { useCallback, useEffect, useState } from "react";
import { api, type BotConfig as Config } from "./api";

const EMPTY_Q: NonNullable<Config["questionnaire"]>[number] = {
  id: "",
  question: "",
  instructions: "",
};

const EMPTY_FAQ: NonNullable<Config["q_and_a"]>[number] = {
  question: "",
  answer: "",
  action: "",
};

const LANGUAGES = ["עברית", "אנגלית", "ערבית", "רוסית"];
const VOICES = ["Charon", "Puck", "Aoede"];

const ACTION_OPTIONS = [
  { key: "save_order", label: "שמירת הזמנה ופתיחת מכרז" },
  { key: "hangup_call", label: "ניתוק שיחה" },
  { key: "transfer_to_representative", label: "העברה לנציג" },
  { key: "get_recent_call", label: "השיחה האחרונה" },
  { key: "get_points", label: "מועדון נוסעים / נקודות" },
  { key: "create_referral", label: "שיוך מספר חדש" },
  { key: "redeem_order", label: "מימוש נסיעת חינם" },
];

function normalize(config: Config | null): Config {
  return {
    name: config?.name ?? "",
    identity: config?.identity ?? "",
    iron_rules: config?.iron_rules ?? "",
    guidelines: config?.guidelines ?? "",
    opening_sentence: config?.opening_sentence ?? "",
    knowledge: config?.knowledge ?? "",
    language: config?.language ?? "עברית",
    voice: config?.voice ?? "Charon",
    representative_phone: config?.representative_phone ?? "",
    allowed_actions: config?.allowed_actions?.length ? config.allowed_actions : [],
    questionnaire: (config?.questionnaire?.length ? config.questionnaire : []).map((q) => ({
      id: q.id ?? "",
      question: q.question ?? "",
      instructions: q.instructions ?? "",
    })),
    q_and_a: (config?.q_and_a?.length ? config.q_and_a : []).map((q) => ({
      question: q.question ?? "",
      answer: q.answer ?? "",
      action: q.action ?? "",
    })),
  };
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel" style={{ marginBottom: "1rem" }}>
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      {children}
    </section>
  );
}

export function BotConfig() {
  const [config, setConfig] = useState<Config>(normalize(null));
  const [prompt, setPrompt] = useState<string>("");
  const [note, setNote] = useState<string>("");
  const [error, setError] = useState<string>("");

  const load = useCallback(() => {
    api.botconfig().then((c) => setConfig(normalize(c))).catch((err: Error) => setError(err.message));
    api.prompt().then((p) => setPrompt(p.content)).catch(() => setPrompt(""));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const update = (patch: Partial<Config>) => setConfig((prev) => ({ ...prev, ...patch }));

  const setText = (key: keyof Config, value: string) => update({ [key]: value } as Partial<Config>);

  const toggleAction = (key: string) => {
    const current = new Set(config.allowed_actions ?? []);
    if (current.has(key)) current.delete(key);
    else current.add(key);
    update({ allowed_actions: Array.from(current) });
  };

  const setQuestion = (i: number, patch: Partial<NonNullable<Config["questionnaire"]>[number]>) => {
    const list = config.questionnaire ? [...config.questionnaire] : [];
    list[i] = { ...list[i], ...patch };
    update({ questionnaire: list });
  };

  const addQuestion = () => {
    const list = config.questionnaire ? [...config.questionnaire] : [];
    list.push({ ...EMPTY_Q });
    update({ questionnaire: list });
  };

  const removeQuestion = (i: number) => {
    const list = config.questionnaire ? [...config.questionnaire] : [];
    list.splice(i, 1);
    update({ questionnaire: list });
  };

  const setFaq = (i: number, patch: Partial<NonNullable<Config["q_and_a"]>[number]>) => {
    const list = config.q_and_a ? [...config.q_and_a] : [];
    list[i] = { ...list[i], ...patch };
    update({ q_and_a: list });
  };

  const addFaq = () => {
    const list = config.q_and_a ? [...config.q_and_a] : [];
    list.push({ ...EMPTY_FAQ });
    update({ q_and_a: list });
  };

  const removeFaq = (i: number) => {
    const list = config.q_and_a ? [...config.q_and_a] : [];
    list.splice(i, 1);
    update({ q_and_a: list });
  };

  const save = () => {
    api
      .saveBotconfig(config)
      .then((saved) => {
        setConfig(normalize(saved));
        setNote("נשמר");
        setError("");
        api.prompt().then((p) => setPrompt(p.content));
      })
      .catch((err: Error) => {
        setNote("");
        setError(err.message);
      });
  };

  const reset = () => {
    if (!confirm("לשחזר את ברירת המחדל? כל העריכה תאבד.")) return;
    api
      .resetBotconfig()
      .then((saved) => {
        setConfig(normalize(saved));
        setNote("שוחזר לברירת מחדל");
        setError("");
        api.prompt().then((p) => setPrompt(p.content));
      })
      .catch((err: Error) => {
        setNote("");
        setError(err.message);
      });
  };

  return (
    <>
      <h1>עריכת בוט AI</h1>
      {error && <div className="error">{error}</div>}
      {note && <div className="muted">{note}</div>}

      <div className="row" style={{ marginBottom: "1rem" }}>
        <button className="action" onClick={save}>
          שמור הגדרות
        </button>
        <button onClick={reset}>שחזר ברירת מחדל</button>
        <button onClick={load}>טען מחדש</button>
      </div>

      <Section title="זהות וקול">
        <div className="grid">
          <label>
            שם הבוט
            <input
              value={config.name}
              onChange={(e) => setText("name", e.target.value)}
            />
          </label>
          <label>
            שפה
            <select
              value={config.language}
              onChange={(e) => setText("language", e.target.value)}
            >
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            קול
            <select value={config.voice} onChange={(e) => setText("voice", e.target.value)}>
              {VOICES.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            מספר נציג להעברה
            <input
              value={config.representative_phone}
              onChange={(e) => setText("representative_phone", e.target.value)}
            />
          </label>
        </div>
        <label className="wide">
          משפט פתיחה
          <input
            value={config.opening_sentence}
            onChange={(e) => setText("opening_sentence", e.target.value)}
          />
        </label>
      </Section>

      <Section title="פרומפט כללי — מי אני והתפקיד שלי">
        <textarea
          value={config.identity}
          onChange={(e) => setText("identity", e.target.value)}
          rows={4}
        />
      </Section>

      <Section title="חוקי ברזל">
        <textarea
          value={config.iron_rules}
          onChange={(e) => setText("iron_rules", e.target.value)}
          rows={6}
        />
      </Section>

      <Section title="קווים מנחים">
        <textarea
          value={config.guidelines}
          onChange={(e) => setText("guidelines", e.target.value)}
          rows={4}
        />
      </Section>

      <Section title="מידע / ידע לנציג (מחירים וכללים)">
        <textarea
          value={config.knowledge}
          onChange={(e) => setText("knowledge", e.target.value)}
          rows={10}
        />
      </Section>

      <Section title="פעולות שהבוט רשאי לבצע">
        <div className="grid">
          {ACTION_OPTIONS.map((a) => (
            <label key={a.key} className="check">
              <input
                type="checkbox"
                checked={(config.allowed_actions ?? []).includes(a.key)}
                onChange={() => toggleAction(a.key)}
              />
              {a.label}
            </label>
          ))}
        </div>
      </Section>

      <Section title="שאלון (הבוט שואל לפי הסדר)">
        {(config.questionnaire ?? []).map((q, i) => (
          <div className="grid" key={i} style={{ alignItems: "end", marginBottom: "0.75rem" }}>
            <label>
              מזהה שאלה
              <input value={q.id} onChange={(e) => setQuestion(i, { id: e.target.value })} />
            </label>
            <label className="wide">
              שאלה
              <input value={q.question} onChange={(e) => setQuestion(i, { question: e.target.value })} />
            </label>
            <label className="wide">
              הוראות / תנאים
              <input
                value={q.instructions}
                onChange={(e) => setQuestion(i, { instructions: e.target.value })}
              />
            </label>
            <button onClick={() => removeQuestion(i)}>הסר</button>
          </div>
        ))}
        <button onClick={addQuestion}>+ הוסף שאלה</button>
      </Section>

      <Section title="שאלות ותשובות — עם פעולה אופציונלית">
        {(config.q_and_a ?? []).map((q, i) => (
          <div className="grid" key={i} style={{ alignItems: "end", marginBottom: "0.75rem" }}>
            <label className="wide">
              שאלה
              <input value={q.question} onChange={(e) => setFaq(i, { question: e.target.value })} />
            </label>
            <label className="wide">
              תשובה
              <input value={q.answer} onChange={(e) => setFaq(i, { answer: e.target.value })} />
            </label>
            <label>
              פעולה (אופציונלי)
              <input value={q.action} onChange={(e) => setFaq(i, { action: e.target.value })} />
            </label>
            <button onClick={() => removeFaq(i)}>הסר</button>
          </div>
        ))}
        <button onClick={addFaq}>+ הוסף Q&A</button>
      </Section>

      <Section title="תצוגה מקדימה של הפרומפט שנשלח ל-AI">
        <pre
          style={{
            background: "var(--panel-2)",
            padding: "1rem",
            borderRadius: "0.5rem",
            whiteSpace: "pre-wrap",
            maxHeight: "24rem",
            overflow: "auto",
          }}
        >
          {prompt}
        </pre>
      </Section>
    </>
  );
}
