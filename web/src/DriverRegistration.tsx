import { useCallback, useMemo, useState } from "react";
import { api, type Area } from "./api";
import { useToast } from "./ui";
import { usePoll } from "./usePoll";

type FormState = {
  phone: string;
  name: string;
  car_model: string;
  seats: string;
  areas: string[];
  has_documents: boolean;
  accepts_rides_limit: boolean;
  terms_accepted: boolean;
};

const initialForm: FormState = {
  phone: "",
  name: "",
  car_model: "",
  seats: "4",
  areas: [],
  has_documents: false,
  accepts_rides_limit: false,
  terms_accepted: false,
};

const normalizePhone = (value: string) => value.replace(/\D/g, "");

export function DriverRegistration() {
  const toast = useToast();
  const loadAreas = useCallback(() => api.areas(), []);
  const { data: areas = [] } = usePoll<Area[]>(loadAreas, 60);
  const [form, setForm] = useState<FormState>(initialForm);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const areaNames = useMemo(() => (areas ?? []).map((area) => area.name), [areas]);
  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const toggleArea = (name: string) =>
    setField(
      "areas",
      form.areas.includes(name)
        ? form.areas.filter((area) => area !== name)
        : [...form.areas, name],
    );

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const phone = normalizePhone(form.phone);
    const seats = Number(form.seats);
    if (phone.length < 9 || phone.length > 11) {
      setError("יש להזין מספר טלפון תקין.");
      return;
    }
    if (!form.name.trim() || !form.car_model.trim() || !form.areas.length) {
      setError("יש למלא שם, דגם רכב ולבחור לפחות אזור אחד.");
      return;
    }
    if (!Number.isInteger(seats) || seats < 1) {
      setError("מספר המושבים חייב להיות מספר שלם חיובי.");
      return;
    }
    if (!form.has_documents || !form.accepts_rides_limit || !form.terms_accepted) {
      setError("יש לאשר את כל סעיפי התקנון כדי להמשיך.");
      return;
    }

    setSubmitting(true);
    api
      .saveDriver({
        phone,
        name: form.name.trim(),
        car_model: form.car_model.trim(),
        seats,
        home_area: form.areas[0],
        areas: form.areas,
        status: "pending",
        smartphone: true,
        voice_offers: true,
        has_documents: true,
        accepts_rides_limit: true,
        terms_accepted: true,
        terms_version: "driver-1",
      })
      .then(() => {
        setForm(initialForm);
        toast.success("הנהג נרשם בהצלחה");
      })
      .catch((err: Error) => {
        setError(err.message);
        toast.error(`רישום הנהג נכשל: ${err.message}`);
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <section className="registration-page">
      <div className="page-heading">
        <div>
          <h1>רישום נהג חדש</h1>
          <p className="muted">הפרטים יישמרו במערכת ויהיו זמינים לשיבוץ במכרזים.</p>
        </div>
      </div>
      <form className="panel registration-form" onSubmit={submit} noValidate>
        {error && <div className="error" role="alert">{error}</div>}
        <div className="form-section">
          <h2>פרטי הנהג</h2>
          <div className="grid">
            <label>
              טלפון *
              <input
                required
                type="tel"
                inputMode="tel"
                dir="ltr"
                autoComplete="tel"
                value={form.phone}
                placeholder="0501234567"
                onChange={(event) => setField("phone", event.target.value)}
              />
            </label>
            <label>
              שם מלא *
              <input
                required
                autoComplete="name"
                value={form.name}
                onChange={(event) => setField("name", event.target.value)}
              />
            </label>
          </div>
        </div>

        <div className="form-section">
          <h2>פרטי הרכב</h2>
          <div className="grid">
            <label>
              דגם רכב *
              <input
                required
                list="registration-car-models"
                value={form.car_model}
                placeholder="לדוגמה: Mercedes Vito"
                onChange={(event) => setField("car_model", event.target.value)}
              />
            </label>
            <label>
              מספר מושבים *
              <input
                required
                type="number"
                min={1}
                step={1}
                value={form.seats}
                onChange={(event) => setField("seats", event.target.value)}
              />
            </label>
          </div>
        </div>

        <div className="form-section">
          <h2>אזורי פעילות *</h2>
          <p className="muted">בחר אזור אחד או יותר שבהם הנהג מעוניין לקבל נסיעות.</p>
          {areaNames.length ? (
            <div className="registration-areas">
              {areaNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={form.areas.includes(name) ? "chip on" : "chip"}
                  onClick={() => toggleArea(name)}
                  aria-pressed={form.areas.includes(name)}
                >
                  {name}
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">עדיין לא הוגדרו אזורים במערכת. ניתן להוסיף אותם במסך האזורים.</p>
          )}
        </div>

        <div className="form-section">
          <h2>תנאי הצטרפות *</h2>
          <p className="muted">
            הנהג מאשר שהמערכת היא פלטפורמת תיווך בלבד ואינה נושאת באחריות
            לפעילות הנסיעה עצמה. האחריות לביצוע נסיעות חוקי ובטוח, ולקיום כל
            מסמך, רישיון, הסמכה וביטוח, מוטלת על הנהג בלבד.
          </p>
          <div className="terms-box">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.has_documents}
                onChange={(e) => setField("has_documents", e.target.checked)}
              />
              <span>יש לי את כל המסמכים, הרשיונות, ההסמכות והביטוחים הדרושים.</span>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.accepts_rides_limit}
                onChange={(e) => setField("accepts_rides_limit", e.target.checked)}
              />
              <span>
                אני מודע ומתחייב שלא לקחת יותר משתי נסיעות שיתופיות ביום,
                ולעקוב אחרי ההגבלות החוקיות.
              </span>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.terms_accepted}
                onChange={(e) => setField("terms_accepted", e.target.checked)}
              />
              <span>קראתי את התקנון ואני מסכים/מה לתנאיו.</span>
            </label>
          </div>
        </div>

        <datalist id="registration-car-models">
          <option value="Mercedes Vito" />
          <option value="Volkswagen Caravelle" />
          <option value="Toyota Proace" />
        </datalist>

        <div className="form-actions">
          <button className="action primary-action" type="submit" disabled={submitting}>
            {submitting ? "שומר..." : "רשום נהג"}
          </button>
          <button type="reset" disabled={submitting} onClick={() => { setForm(initialForm); setError(""); }}>
            נקה טופס
          </button>
        </div>
      </form>
    </section>
  );
}
