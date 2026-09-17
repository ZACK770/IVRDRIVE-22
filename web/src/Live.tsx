import { useCallback, useEffect, useState } from "react";
import { api, type Call, type Order, type Tender } from "./api";
import { clock, usePoll } from "./usePoll";
import { mmss, useCountdown } from "./ui";

const TENDER_STATUS: Record<string, string> = {
  open: "פתוח",
  awarded: "שובץ",
  expired: "פג ללא נהג",
  cancelled: "בוטל",
};

type TenderDetail = Awaited<ReturnType<typeof api.tender>>;

/** One open tender, drilled into live: countdown, who was rung, bids as they
 *  land and the current leader. */
function LiveTender({ tender }: { tender: Tender }) {
  const [detail, setDetail] = useState<TenderDetail | null>(null);
  const left = useCountdown(tender.closes_at);

  useEffect(() => {
    let alive = true;
    const pull = () => api.tender(tender.id).then((d) => alive && setDetail(d)).catch(() => {});
    pull();
    const timer = setInterval(pull, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [tender.id]);

  const bids = detail?.bids ?? [];
  const leader = bids.length
    ? bids.reduce((best, bid) => (bid.score > best.score ? bid : best), bids[0])
    : null;
  const order = detail?.order ?? null;

  return (
    <div className="panel live-tender">
      <div className="live-head">
        <h2>
          מכרז #{tender.id}
          <span className={`status-badge ${left > 0 ? "status-ok" : "status-muted"}`}>
            {left > 0 ? "פתוח" : TENDER_STATUS[tender.status] ?? tender.status}
          </span>
        </h2>
        <div className={`countdown${left > 0 && left <= 15 ? " urgent" : ""}`}>
          {left > 0 ? mmss(left) : "נסגר"}
        </div>
      </div>
      {order && (
        <p className="muted">
          {order.origin} ← {order.destination}
          {order.price != null && ` · ${order.price.toFixed(0)} ₪`}
          {` · נוסע ${order.phone}`}
        </p>
      )}
      <div className="live-columns">
        <div>
          <h3>צונתקו ({detail?.called.length ?? tender.notified})</h3>
          <ul className="live-list">
            {(detail?.called ?? []).map((c, i) => (
              <li key={i}>
                {c.driver_name ?? c.phone} · {c.status}
              </li>
            ))}
            {(detail?.called ?? []).length === 0 && <li className="muted">אף נהג לא צונתק.</li>}
          </ul>
        </div>
        <div>
          <h3>הצעות ({bids.length})</h3>
          <ul className="live-list">
            {bids.map((b) => (
              <li key={b.driver_id} className={leader && b.driver_id === leader.driver_id ? "leader" : ""}>
                {b.driver_name ?? b.driver_phone} · ציון {b.score.toFixed(1)}
                {leader && b.driver_id === leader.driver_id && (left > 0 ? " · מוביל" : b.won ? " · זכה" : "")}
              </li>
            ))}
            {bids.length === 0 && <li className="muted">עדיין אין הצעות.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

export function Live() {
  const loadTenders = useCallback(() => api.tenders(), []);
  const loadCalls = useCallback(() => api.calls(), []);
  const loadOrders = useCallback(() => api.orders(), []);
  const tenders = usePoll<Tender[]>(loadTenders, 2);
  const calls = usePoll<Call[]>(loadCalls, 10);
  const orders = usePoll<Order[]>(loadOrders, 5);

  const open = (tenders.data ?? []).filter((t) => t.status === "open");
  const recent = (tenders.data ?? []).filter((t) => t.status !== "open").slice(0, 5);
  const active = (orders.data ?? []).filter((o) => o.status === "assigned" || o.status === "on_route");

  return (
    <section className="live-screen">
      <h1>
        לייב
        <span className="live-dot" title="מתעדכן כל 2 שניות" />
      </h1>
      {tenders.error && <div className="error">{tenders.error}</div>}

      <h2>מכרזים פתוחים ({open.length})</h2>
      {open.map((tender) => (
        <LiveTender key={tender.id} tender={tender} />
      ))}
      {open.length === 0 && <p className="muted">אין מכרז פתוח כרגע.</p>}

      <h2>נסיעות בביצוע ({active.length})</h2>
      <table>
        <thead>
          <tr>
            <th>הזמנה</th>
            <th>מוצא</th>
            <th>יעד</th>
            <th>נהג</th>
            <th>סטטוס</th>
          </tr>
        </thead>
        <tbody>
          {active.map((o) => (
            <tr key={o.id}>
              <td data-label="הזמנה">{o.id}</td>
              <td data-label="מוצא">{o.origin}</td>
              <td data-label="יעד">{o.destination}</td>
              <td data-label="נהג">{o.driver_name ?? o.driver_phone ?? "—"}</td>
              <td data-label="סטטוס">{o.status === "assigned" ? "שובצה" : "בדרך"}</td>
            </tr>
          ))}
          {active.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                אין נסיעות בביצוע.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <h2>מכרזים אחרונים</h2>
      <table>
        <thead>
          <tr>
            <th>מכרז</th>
            <th>אזור</th>
            <th>הצעות</th>
            <th>סטטוס</th>
            <th>נסגר</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((t) => (
            <tr key={t.id}>
              <td data-label="מכרז">{t.id}</td>
              <td data-label="אזור">{t.area ?? "—"}</td>
              <td data-label="הצעות">{t.bids}</td>
              <td data-label="סטטוס">{TENDER_STATUS[t.status] ?? t.status}</td>
              <td data-label="נסגר">{clock(t.closes_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>שיחות אחרונות</h2>
      <table>
        <thead>
          <tr>
            <th>מתי</th>
            <th>מתקשר</th>
            <th>תקציר</th>
          </tr>
        </thead>
        <tbody>
          {(calls.data ?? []).slice(0, 8).map((call) => (
            <tr key={call.id}>
              <td data-label="מתי">{clock(call.started_at)}</td>
              <td data-label="מתקשר">{call.phone ?? "—"}</td>
              <td data-label="תקציר">{call.summary ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
