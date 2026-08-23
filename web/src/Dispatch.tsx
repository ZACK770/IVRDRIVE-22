import { useCallback, useState } from "react";
import { api, type BoardArea, type Tender, type TenderFilters } from "./api";
import { clock, usePoll } from "./usePoll";
import { Confirm, Modal, useToast } from "./ui";

const TENDER_STATUS: Record<string, string> = {
  open: "פתוח",
  awarded: "שובץ",
  expired: "פג ללא נהג",
  cancelled: "בוטל",
};

/** Who is where: fed by "finished a ride" and the once-a-day self report, so
 *  the age of each entry matters as much as the entry. */
export function AreaBoard() {
  const load = useCallback(() => api.driverBoard(), []);
  const { data, error } = usePoll<BoardArea[]>(load, 15);

  return (
    <>
      <h1>נהגים לפי אזור</h1>
      {error && <div className="error">{error}</div>}
      <div className="cards">
        {(data ?? []).map((area) => (
          <div className="card wide" key={area.area}>
            <b>{area.area}</b>
            <span>{area.drivers.length} נהגים</span>
            <ul>
              {area.drivers.map((driver) => (
                <li key={driver.id}>
                  {driver.name ?? driver.phone} · {driver.tier} · לפני {driver.minutes_ago} דק׳
                </li>
              ))}
            </ul>
          </div>
        ))}
        {(data ?? []).length === 0 && <span className="muted">אף נהג לא עדכן מיקום.</span>}
      </div>
    </>
  );
}

export function Tenders() {
  const load = useCallback(() => api.tenders(), []);
  const { data, error, refresh } = usePoll<Tender[]>(load, 3);
  const [orderId, setOrderId] = useState("");
  const [area, setArea] = useState("");
  const [seconds, setSeconds] = useState("");
  const [filters, setFilters] = useState<TenderFilters>({});
  const [detail, setDetail] = useState<any>(null);
  const [confirm, setConfirm] = useState<{ text: string; run: () => void } | null>(null);
  const toast = useToast();

  const open = () => {
    if (!orderId) {
      toast.error("יש להזין מספר הזמנה");
      return;
    }
    api
      .openTender(Number(orderId), {
        area: area || undefined,
        filters: Object.keys(filters).length ? filters : undefined,
        window_seconds: seconds ? Number(seconds) : undefined,
      })
      .then((result) => {
        toast.success(
          `מכרז נפתח: נשלח ל-${result.eligible} נהגים (${result.flash} צינתוק, ${result.voice} קוליים)`,
        );
        refresh();
      })
      .catch((err: Error) => toast.error(`פתיחת מכרז נכשלה: ${err.message}`));
  };

  return (
    <>
      <h1>מכרזי נסיעה</h1>
      {error && <div className="error">{error}</div>}
      <div className="panel">
        <h2>פתיחת מכרז</h2>
        <div className="grid">
          <label>
            מספר הזמנה
            <input value={orderId} onChange={(e) => setOrderId(e.target.value)} />
          </label>
          <label>
            אזור צינתוק
            <input value={area} onChange={(e) => setArea(e.target.value)} />
          </label>
          <label>
            חלון הצעות (שניות)
            <input
              type="number"
              placeholder="ברירת מחדל"
              value={seconds}
              onChange={(e) => setSeconds(e.target.value)}
            />
          </label>
          <label>
            רק רכב משנת
            <input
              type="number"
              onChange={(e) =>
                setFilters({ ...filters, min_car_year: Number(e.target.value) || undefined })
              }
            />
          </label>
          <label>
            רק נהג מגיל
            <input
              type="number"
              onChange={(e) =>
                setFilters({ ...filters, min_age: Number(e.target.value) || undefined })
              }
            />
          </label>
          <label>
            דירוג מינימלי
            <input
              type="number"
              step="0.1"
              onChange={(e) =>
                setFilters({ ...filters, min_rating: Number(e.target.value) || undefined })
              }
            />
          </label>
          <label>
            סמארטפון
            <select
              onChange={(e) =>
                setFilters({
                  ...filters,
                  smartphone: e.target.value === "" ? undefined : e.target.value === "yes",
                })
              }
            >
              <option value="">הכל</option>
              <option value="yes">רק עם סמארטפון</option>
              <option value="no">רק בלי סמארטפון</option>
            </select>
          </label>
        </div>
        <button className="action" onClick={open}>
          שלח צינתוק ופתח מכרז
        </button>
      </div>

      <table>
        <thead>
          <tr>
            <th>מכרז</th>
            <th>הזמנה</th>
            <th>אזור</th>
            <th>נשלח ל</th>
            <th>הצעות</th>
            <th>נסגר ב</th>
            <th>סטטוס</th>
            <th>נהג זוכה</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((tender) => (
            <tr key={tender.id}>
              <td data-label="מכרז">{tender.id}</td>
              <td data-label="הזמנה">{tender.order_id}</td>
              <td data-label="אזור">{tender.area ?? "—"}</td>
              <td data-label="נשלח ל">{tender.notified}</td>
              <td data-label="הצעות">{tender.bids}</td>
              <td data-label="נסגר ב">{clock(tender.closes_at)}</td>
              <td data-label="סטטוס">{TENDER_STATUS[tender.status] ?? tender.status}</td>
              <td data-label="נהג זוכה">{tender.awarded_driver_id ?? "—"}</td>
              <td>
                <button
                  onClick={() =>
                    api
                      .tender(tender.id)
                      .then(setDetail)
                      .catch((err: Error) => toast.error(err.message))
                  }
                >
                  פרטים
                </button>
                {tender.status === "open" && (
                  <>
                    <button
                      onClick={() =>
                        setConfirm({
                          text: `לסגור את מכרז ${tender.id} עכשיו ולהכריז על זוכה?`,
                          run: () =>
                            api
                              .closeTender(tender.id)
                              .then(() => {
                                toast.success(`מכרז ${tender.id} נסגר`);
                                refresh();
                              })
                              .catch((err: Error) => toast.error(err.message)),
                        })
                      }
                    >
                      סגור עכשיו
                    </button>
                    <button
                      onClick={() =>
                        setConfirm({
                          text: `לבטל את מכרז ${tender.id} ללא זוכה?`,
                          run: () =>
                            api
                              .cancelTender(tender.id)
                              .then(() => {
                                toast.success(`מכרז ${tender.id} בוטל`);
                                refresh();
                              })
                              .catch((err: Error) => toast.error(err.message)),
                        })
                      }
                    >
                      ביטול
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {confirm && (
        <Confirm
          text={confirm.text}
          onYes={() => {
            confirm.run();
            setConfirm(null);
          }}
          onNo={() => setConfirm(null)}
        />
      )}
      {detail && (
        <Modal title={`פרטי מכרז #${detail.tender.id}`} onClose={() => setDetail(null)} wide>
          <p className="muted">
            הזמנה #{detail.tender.order_id} | אזור {detail.tender.area ?? "—"} | סטטוס{" "}
            {detail.tender.status}
          </p>

          <h3>צינתוקים שנשלחו ({detail.called.length})</h3>
          <table>
            <thead>
              <tr>
                <th>נהג</th>
                <th>טלפון</th>
                <th>סטטוס</th>
                <th>מזהה שיחה</th>
              </tr>
            </thead>
            <tbody>
              {detail.called.map((c: any, idx: number) => (
                <tr key={idx}>
                  <td>{c.driver_name ?? "—"}</td>
                  <td>{c.phone}</td>
                  <td>{c.status}</td>
                  <td>{c.cid ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>הצעות שהתקבלו ({detail.bids.length})</h3>
          <table>
            <thead>
              <tr>
                <th>נהג</th>
                <th>טלפון</th>
                <th>ציון</th>
                <th>זוכה</th>
              </tr>
            </thead>
            <tbody>
              {detail.bids.map((b: any) => (
                <tr key={b.driver_id}>
                  <td>{b.driver_name ?? "—"}</td>
                  <td>{b.driver_phone}</td>
                  <td>{b.score.toFixed(1)}</td>
                  <td>{b.won ? "כן" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}
