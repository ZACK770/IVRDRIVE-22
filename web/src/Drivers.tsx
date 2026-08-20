import { useCallback, useMemo, useState } from "react";
import { api, type Area, type Driver } from "./api";
import { clock, usePoll } from "./usePoll";

const STATUS_LABEL: Record<string, string> = {
  pending: "ממתין לאישור",
  active: "פעיל",
  paused: "מושהה",
  suspended: "מושעה",
};

const statusClass = (status: string) => {
  if (status === "active") return "status-ok";
  if (status === "pending") return "status-warn";
  if (status === "suspended" || status === "removed") return "status-bad";
  return "status-muted";
};

const ACTIVE_STATUSES = ["pending", "suspended"];
const NON_EDITABLE_STATUSES = ["removed"];

//: Just enough to make the field a picker rather than a blank box; anything
//: else is still typed by hand.
const CAR_MODELS = [
  "טויוטה קורולה",
  "יונדאי i35",
  "קיה ספורטג'",
  "מרצדס ויאנו",
  "פולקסווגן קאדי",
  "סקודה אוקטביה",
];

const empty = (): Partial<Driver> => ({
  phone: "",
  status: "pending",
  smartphone: true,
  seats: 4,
});

const statusOptions = () =>
  Object.entries(STATUS_LABEL).filter(([value]) => !NON_EDITABLE_STATUSES.includes(value));

function normalizePhone(phone: string) {
  return phone.replace(/\D/g, "");
}

function validateDriver(form: Partial<Driver>): string {
  const digits = normalizePhone(form.phone ?? "");
  if (!digits) return "יש להזין מספר טלפון";
  if (digits.length < 9 || digits.length > 11) return "מספר טלפון לא תקין";

  const year = (key: keyof Driver) => {
    const value = form[key];
    if (value === null || value === undefined || value === "") return true;
    const n = Number(value);
    return n >= 1900 && n <= 2099;
  };

  if (!year("car_year")) return "שנת רכב לא תקינה";
  if (!year("birth_year")) return "שנת לידה לא תקינה";

  const hour = (value: number | null | undefined | string) => {
    if (value === null || value === undefined || value === "") return true;
    const n = Number(value);
    return n >= 0 && n <= 23;
  };

  if (!hour(form.quiet_from)) return "שעת התחלת שעות שקט חייבת להיות 0-23";
  if (!hour(form.quiet_to)) return "שעת סיום שעות שקט חייבת להיות 0-23";

  return "";
}

/** Areas are typed as free text and only split on save, so a space or a
 *  half-typed name survives the keystroke that produced it. */
function parseAreas(text: string): string[] {
  const seen = new Set<string>();
  return text
    .split(",")
    .map((area) => area.trim())
    .filter((area) => area && !seen.has(area) && seen.add(area));
}

function DriverForm({
  driver,
  areaNames,
  onDone,
}: {
  driver: Partial<Driver>;
  areaNames: string[];
  onDone: () => void;
}) {
  const [form, setForm] = useState<Partial<Driver>>(driver);
  const [areasText, setAreasText] = useState((driver.areas ?? []).join(", "));
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const chosen = useMemo(() => parseAreas(areasText), [areasText]);

  const toggleArea = (name: string) =>
    setAreasText(
      (chosen.includes(name) ? chosen.filter((area) => area !== name) : [...chosen, name]).join(
        ", ",
      ),
    );

  const field = (key: keyof Driver, value: string | number | boolean | string[] | null) =>
    setForm((current) => ({ ...current, [key]: value }));

  const numeric = (key: keyof Driver, value: string) =>
    field(key, value === "" ? null : Number(value));

  const submit = () => {
    setError("");
    setSuccess("");
    const payload = { ...form, areas: chosen };
    const message = validateDriver(payload);
    if (message) {
      setError(message);
      return;
    }
    api
      .saveDriver(payload)
      .then(() => {
        setSuccess("הנהג נשמר בהצלחה");
        setTimeout(onDone, 900);
      })
      .catch((err: Error) => setError(err.message));
  };

  const isNew = !form.id;
  const currentStatus = form.status ?? "pending";

  return (
    <div className="panel">
      <h2>
        {isNew ? "נהג חדש" : `עריכת נהג ${form.phone}`}
        {!isNew && (
          <span className={`status-badge ${statusClass(currentStatus)}`}>
            {STATUS_LABEL[currentStatus] ?? currentStatus}
          </span>
        )}
      </h2>
      {error && <div className="error">{error}</div>}
      {success && <div className="success">{success}</div>}
      <div className="grid">
        <label>
          טלפון
          <input
            value={form.phone ?? ""}
            disabled={!isNew}
            type="tel"
            inputMode="tel"
            dir="ltr"
            autoComplete="tel"
            placeholder="למשל 0521234567"
            onChange={(e) => field("phone", e.target.value)}
          />
        </label>
        <label>
          שם
          <input
            value={form.name ?? ""}
            autoComplete="name"
            onChange={(e) => field("name", e.target.value)}
          />
        </label>
        <label>
          דגם רכב
          <input
            value={form.car_model ?? ""}
            list="car-models"
            placeholder="למשל סיאנה, טסלה, אקסנט"
            onChange={(e) => field("car_model", e.target.value)}
          />
        </label>
        <label>
          שנת רכב
          <input
            type="number"
            value={form.car_year ?? ""}
            placeholder="2020"
            onChange={(e) => numeric("car_year", e.target.value)}
          />
        </label>
        <label>
          מספר מושבים
          <input
            type="number"
            value={form.seats ?? ""}
            min={1}
            placeholder="4"
            onChange={(e) => numeric("seats", e.target.value)}
          />
        </label>
        <label>
          שנת לידה
          <input
            type="number"
            value={form.birth_year ?? ""}
            placeholder="1980"
            onChange={(e) => numeric("birth_year", e.target.value)}
          />
        </label>
        <label>
          אזור מגורים / בית
          <input
            value={form.home_area ?? ""}
            list="areas"
            placeholder="למשל ירושלים"
            onChange={(e) => field("home_area", e.target.value)}
          />
        </label>
        <label className="wide">
          אזורים מועדפים (מופרדים בפסיק)
          <input
            value={areasText}
            list="areas"
            placeholder="ירושלים, בני ברק, תל אביב"
            onChange={(e) => setAreasText(e.target.value)}
          />
          {areaNames.length > 0 && (
            <span className="chips">
              {areaNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={chosen.includes(name) ? "chip on" : "chip"}
                  onClick={() => toggleArea(name)}
                >
                  {name}
                </button>
              ))}
            </span>
          )}
        </label>
        <label>
          שעות שקט (משעה)
          <input
            type="number"
            min={0}
            max={23}
            value={form.quiet_from ?? ""}
            placeholder="22"
            onChange={(e) => numeric("quiet_from", e.target.value)}
          />
        </label>
        <label>
          שעות שקט (עד שעה)
          <input
            type="number"
            min={0}
            max={23}
            value={form.quiet_to ?? ""}
            placeholder="6"
            onChange={(e) => numeric("quiet_to", e.target.value)}
          />
        </label>
        <label>
          סטטוס
          <select value={form.status ?? "pending"} onChange={(e) => field("status", e.target.value)}>
            {statusOptions().map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(form.smartphone)}
            onChange={(e) => field("smartphone", e.target.checked)}
          />
          סמארטפון
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(form.voice_offers)}
            onChange={(e) => field("voice_offers", e.target.checked)}
          />
          הודעה קולית בתשלום (במקום צינתוק)
        </label>
        <label>
          הערות
          <input
            value={form.notes ?? ""}
            placeholder="למשל שפות, העדפות"
            onChange={(e) => field("notes", e.target.value)}
          />
        </label>
      </div>
      {/* The console is the only place these names are ever typed, so the
          suggestions come from the areas the office already defined. */}
      <datalist id="areas">
        {areaNames.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <datalist id="car-models">
        {CAR_MODELS.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
      <div className="row">
        <button className="action" onClick={submit}>
          שמור
        </button>
        <button onClick={onDone}>ביטול</button>
      </div>
    </div>
  );
}

export function Drivers() {
  const load = useCallback(() => api.drivers(), []);
  const { data, error, refresh } = usePoll<Driver[]>(load, 20);
  const areas = usePoll<Area[]>(
    useCallback(() => api.areas(), []),
    60,
  );
  const areaNames = useMemo(() => (areas.data ?? []).map((area) => area.name), [areas.data]);
  const [editing, setEditing] = useState<Partial<Driver> | null>(null);
  const [note, setNote] = useState("");
  const [noteType, setNoteType] = useState<"ok" | "error">("ok");

  const act = (promise: Promise<unknown>, message: string) =>
    promise
      .then(() => {
        setNote(message);
        setNoteType("ok");
        refresh();
      })
      .catch((err: Error) => {
        setNote(err.message);
        setNoteType("error");
      });

  return (
    <>
      <h1>נהגים</h1>
      {(error || note) && (
        <div className={error || noteType === "error" ? "error" : "success"}>{error || note}</div>
      )}
      <div className="row">
        <button className="action" onClick={() => setEditing(empty())}>
          נהג חדש
        </button>
      </div>
      {editing && (
        <DriverForm
          driver={editing}
          areaNames={areaNames}
          onDone={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
      <table>
        <thead>
          <tr>
            <th>טלפון</th>
            <th>שם</th>
            <th>רכב</th>
            <th>אזורים</th>
            <th>דירוג</th>
            <th>נסיעות</th>
            <th>מוניטין</th>
            <th>מיקום אחרון</th>
            <th>סטטוס</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((driver) => (
            <tr key={driver.id}>
              <td data-label="טלפון">{driver.phone}</td>
              <td data-label="שם">{driver.name ?? "—"}</td>
              <td data-label="רכב">
                {driver.car_model ?? "—"} {driver.car_year ?? ""}
              </td>
              <td data-label="אזורים">{driver.areas.join(", ") || "—"}</td>
              <td data-label="דירוג">
                {driver.rating ? `${driver.rating.toFixed(1)} (${driver.rating_count})` : "—"}
              </td>
              <td data-label="נסיעות">{driver.rides_done}</td>
              <td data-label="מוניטין">{driver.tier_label}</td>
              <td data-label="מיקום אחרון">
                {driver.last_area ?? "—"}
                {driver.last_area_at ? ` · ${clock(driver.last_area_at)}` : ""}
              </td>
              <td data-label="סטטוס">
                <span className={`status-badge ${statusClass(driver.status)}`}>
                  {STATUS_LABEL[driver.status] ?? driver.status}
                </span>
              </td>
              <td>
                <button onClick={() => setEditing(driver)}>עריכה</button>
                {ACTIVE_STATUSES.includes(driver.status) && (
                  <button
                    className="action"
                    onClick={() =>
                      act(api.saveDriver({ id: driver.id, status: "active" }), "הנהג אושר והופעל")
                    }
                  >
                    אשר
                  </button>
                )}
                <button onClick={() => act(api.driverFlash(driver.id), "צינתוק נשלח")}>
                  צינתוק
                </button>
                <button
                  onClick={() => {
                    if (window.confirm("השעיית נהג תמנע ממנו לקבל נסיעות. להמשיך?")) {
                      act(api.removeDriver(driver.id), "הנהג הושעה");
                    }
                  }}
                >
                  השעה
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Areas />
    </>
  );
}

function Areas() {
  const load = useCallback(() => api.areas(), []);
  const { data, error, refresh } = usePoll<Area[]>(load, 60);
  const [form, setForm] = useState({ name: "", callback_number: "", flash_cid: "" });

  const errors = useMemo(() => {
    const list: string[] = [];
    if (!form.name.trim()) list.push("שם אזור חובה");
    if (!form.callback_number.trim()) list.push("מספר לחיוג חוזר חובה");
    if (form.flash_cid.trim() && form.flash_cid.trim().length !== 6)
      list.push("מזהה מתקשר חייב להיות 6 ספרות");
    return list;
  }, [form]);

  const save = () => {
    if (errors.length) return;
    api
      .saveArea(form)
      .then(() => {
        setForm({ name: "", callback_number: "", flash_cid: "" });
        refresh();
      })
      .catch(() => {});
  };

  return (
    <>
      <h2>אזורי צינתוק</h2>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>אזור</th>
            <th>מספר לחיוג חוזר</th>
            <th>מזהה מתקשר</th>
            <th>פעיל</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((area) => (
            <tr key={area.id}>
              <td data-label="אזור">{area.name}</td>
              <td data-label="מספר לחיוג חוזר">{area.callback_number ?? "—"}</td>
              <td data-label="מזהה מתקשר">{area.flash_cid ?? "—"}</td>
              <td data-label="פעיל">{area.active ? "כן" : "לא"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <input
          placeholder="שם אזור"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <input
          placeholder="מספר לחיוג חוזר"
          value={form.callback_number}
          onChange={(e) => setForm({ ...form, callback_number: e.target.value })}
        />
        <input
          placeholder="מזהה מתקשר לצינתוק (6 ספרות)"
          value={form.flash_cid}
          onChange={(e) => setForm({ ...form, flash_cid: e.target.value })}
        />
        <button className="action" onClick={save} disabled={errors.length > 0}>
          שמור אזור
        </button>
      </div>
      {errors.length > 0 && <div className="error">{errors.join(" · ")}</div>}
    </>
  );
}
