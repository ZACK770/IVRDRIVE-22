import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import {
  api,
  tokenStore,
  type Call,
  type CallDetail,
  type Customer,
  type Order,
  type OrderStatus,
  type Price,
  type Summary,
} from "./api";
import "./styles.css";

const TABS = {
  board: "לוח סדרן",
  calls: "שיחות",
  prices: "מחירון",
  customers: "לקוחות",
  prompt: "פרומפט",
} as const;

type Tab = keyof typeof TABS;

const STATUS_LABEL: Record<OrderStatus, string> = {
  new: "חדשה",
  assigned: "שובצה",
  on_route: "בדרך",
  done: "בוצעה",
  cancelled: "בוטלה",
};

const clock = (iso: string) =>
  new Date(iso).toLocaleString("he-IL", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

/** Polling beats a websocket here: the board is one small query and the
 *  dispatcher tolerates five seconds of staleness. */
function usePoll<T>(load: () => Promise<T>, seconds: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");

  const refresh = useCallback(() => {
    load()
      .then((value) => {
        setData(value);
        setError("");
      })
      .catch((err: Error) => setError(err.message));
  }, [load]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, seconds * 1000);
    return () => clearInterval(timer);
  }, [refresh, seconds]);

  return { data, error, refresh };
}

function Board() {
  const loadSummary = useCallback(() => api.summary(), []);
  const loadOrders = useCallback(() => api.orders(), []);
  const summary = usePoll<Summary>(loadSummary, 10);
  const orders = usePoll<Order[]>(loadOrders, 5);
  const [filter, setFilter] = useState<OrderStatus | "all">("all");
  const [error, setError] = useState("");

  const shown = useMemo(
    () =>
      (orders.data ?? []).filter((o) => filter === "all" || o.status === filter),
    [orders.data, filter],
  );

  const patch = (order: Order, change: Partial<Order>) => {
    api
      .updateOrder(order.id, change)
      .then(() => orders.refresh())
      .catch((err: Error) => setError(err.message));
  };

  return (
    <>
      <h1>לוח סדרן</h1>
      {(error || orders.error) && <div className="error">{error || orders.error}</div>}
      <div className="cards">
        <div className="card">
          <b>{summary.data?.orders_24h ?? "—"}</b>
          <span>הזמנות ב-24 שעות</span>
        </div>
        <div className="card">
          <b>{summary.data?.by_status.new ?? "—"}</b>
          <span>ממתינות לשיבוץ</span>
        </div>
        <div className="card">
          <b>{summary.data?.calls_24h ?? "—"}</b>
          <span>שיחות ב-24 שעות</span>
        </div>
        <div className="card">
          <b>${(summary.data?.cost_usd_24h ?? 0).toFixed(2)}</b>
          <span>עלות הבוט ב-24 שעות</span>
        </div>
      </div>

      <div className="row">
        <select value={filter} onChange={(e) => setFilter(e.target.value as OrderStatus | "all")}>
          <option value="all">הכל</option>
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="muted">מתרענן אוטומטית כל 5 שניות</span>
      </div>

      {/* On a phone this table reflows into one card per order (see styles.css),
          so the driver and status controls stay reachable without sideways
          scrolling — the dispatcher works from the road as often as a desk. */}
      <table className="board">
        <thead>
          <tr>
            <th>מועד</th>
            <th>טלפון</th>
            <th>מוצא</th>
            <th>יעד</th>
            <th>נוסעים</th>
            <th>לאיסוף</th>
            <th>מחיר</th>
            <th>נהג</th>
            <th>סטטוס</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((order) => (
            <tr key={order.id}>
              <td data-label="מועד">{clock(order.created_at)}</td>
              <td data-label="טלפון">{order.phone}</td>
              <td data-label="מוצא">{order.origin}</td>
              <td data-label="יעד">{order.destination}</td>
              <td data-label="נוסעים">{order.passengers}</td>
              <td data-label="לאיסוף">{order.pickup_time ?? "—"}</td>
              <td data-label="מחיר">{order.price === null ? "—" : `${order.price.toFixed(0)} ₪`}</td>
              <td data-label="נהג">
                <input
                  defaultValue={order.driver_name ?? ""}
                  placeholder="שם נהג"
                  size={10}
                  onBlur={(e) => {
                    if (e.target.value !== (order.driver_name ?? "")) {
                      patch(order, { driver_name: e.target.value });
                    }
                  }}
                />
              </td>
              <td data-label="סטטוס">
                <select
                  value={order.status}
                  onChange={(e) => patch(order, { status: e.target.value as OrderStatus })}
                >
                  {Object.entries(STATUS_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
          {shown.length === 0 && (
            <tr>
              <td colSpan={9} className="muted">
                אין הזמנות להצגה.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

function Calls() {
  const load = useCallback(() => api.calls(), []);
  const { data, error } = usePoll<Call[]>(load, 15);
  const [open, setOpen] = useState<CallDetail | null>(null);

  const total = (data ?? []).reduce((sum, call) => sum + call.cost_usd, 0);

  return (
    <>
      <h1>שיחות</h1>
      {error && <div className="error">{error}</div>}
      <div className="cards">
        <div className="card">
          <b>${total.toFixed(2)}</b>
          <span>עלות {data?.length ?? 0} השיחות האחרונות</span>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>מתי</th>
            <th>מתקשר</th>
            <th>תוצאה</th>
            <th>עלות</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((call) => (
            <tr key={call.id}>
              <td>{clock(call.started_at)}</td>
              <td>{call.phone ?? "—"}</td>
              <td>{call.summary ?? "—"}</td>
              <td>${call.cost_usd.toFixed(4)}</td>
              <td>
                <button
                  className="action"
                  onClick={() => api.call(call.id).then(setOpen)}
                >
                  תמליל
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && (
        <>
          <h1>שיחה {open.call_id}</h1>
          <p className="muted">
            תורים: {open.stats.turns ?? 0} · קטיעות: {open.stats.interruptions ?? 0} · זמני
            תגובה: {(open.stats.reply_latency_ms ?? []).join(", ") || "—"} ms · עלות: $
            {(open.stats.usage?.cost_usd ?? 0).toFixed(4)}
          </p>
          <pre>{open.transcript ?? "אין תמליל"}</pre>
        </>
      )}
    </>
  );
}

function Prices() {
  const load = useCallback(() => api.prices(), []);
  const { data, error } = usePoll<Price[]>(load, 60);
  return (
    <>
      <h1>מחירון</h1>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>מוצא</th>
            <th>יעד</th>
            <th>מחיר</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((price) => (
            <tr key={price.id}>
              <td>{price.origin}</td>
              <td>{price.destination}</td>
              <td>{price.price.toFixed(0)} ₪</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Customers() {
  const load = useCallback(() => api.customers(), []);
  const { data, error } = usePoll<Customer[]>(load, 60);
  return (
    <>
      <h1>לקוחות</h1>
      {error && <div className="error">{error}</div>}
      <table>
        <thead>
          <tr>
            <th>טלפון</th>
            <th>שם</th>
            <th>כתובת איסוף</th>
            <th>הערות</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((customer) => (
            <tr key={customer.id}>
              <td>{customer.phone}</td>
              <td>{customer.name ?? "—"}</td>
              <td>{customer.default_pickup ?? "—"}</td>
              <td>{customer.notes ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function PromptEditor() {
  const [content, setContent] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    api.prompt().then(setContent).catch((err: Error) => setNote(err.message));
  }, []);

  return (
    <>
      <h1>פרומפט</h1>
      {note && <div className="error">{note}</div>}
      <textarea value={content} onChange={(e) => setContent(e.target.value)} />
      <div className="row" style={{ marginTop: "0.75rem" }}>
        <button
          className="action"
          onClick={() =>
            api
              .savePrompt(content)
              .then(() => setNote("נשמר. נכנס לתוקף בשיחה הבאה."))
              .catch((err: Error) => setNote(err.message))
          }
        >
          שמור
        </button>
        <span className="muted">{note}</span>
      </div>
    </>
  );
}

const VIEWS: Record<Tab, () => ReactElement> = {
  board: Board,
  calls: Calls,
  prices: Prices,
  customers: Customers,
  prompt: PromptEditor,
};

export default function App() {
  const [tab, setTab] = useState<Tab>("board");
  const [token, setToken] = useState(tokenStore.get());
  const View = VIEWS[tab];

  return (
    <div className="app" dir="rtl">
      <nav className="sidebar">
        <div className="brand">
          דרייברים
          <small>מוקד הסעות</small>
        </div>
        {(Object.keys(TABS) as Tab[]).map((key) => (
          <button key={key} data-active={tab === key} onClick={() => setTab(key)}>
            {TABS[key]}
          </button>
        ))}
        <div className="token">
          <input
            type="password"
            placeholder="קוד גישה"
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              tokenStore.set(e.target.value);
            }}
          />
        </div>
      </nav>
      <main>
        <View />
      </main>
    </div>
  );
}
