import { useCallback, useState } from "react";
import { api, type Books, type DriverRides, type Expense, type Statement } from "./api";
import { clock, usePoll } from "./usePoll";

export function Accounting() {
  const [days, setDays] = useState(30);
  const loadBooks = useCallback(() => api.books(days), [days]);
  const loadDrivers = useCallback(() => api.driverRides(days), [days]);
  const books = usePoll<Books>(loadBooks, 60);
  const perDriver = usePoll<DriverRides[]>(loadDrivers, 60);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [note, setNote] = useState("");
  const [pay, setPay] = useState({ amount: "", method: "", notes: "" });

  return (
    <>
      <h1>הנהלת חשבונות</h1>
      {(books.error || note) && (
        <div className={books.error ? "error" : "muted"}>{books.error || note}</div>
      )}
      <div className="row">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>7 ימים</option>
          <option value={30}>30 ימים</option>
          <option value={90}>90 ימים</option>
          <option value={365}>שנה</option>
        </select>
      </div>
      <div className="cards">
        <div className="card">
          <b>{books.data?.rides_done ?? "—"}</b>
          <span>נסיעות שבוצעו</span>
        </div>
        <div className="card">
          <b>{(books.data?.commission_income ?? 0).toFixed(0)} ₪</b>
          <span>הכנסות מעמלות</span>
        </div>
        <div className="card">
          <b>{(books.data?.expenses ?? 0).toFixed(0)} ₪</b>
          <span>הוצאות</span>
        </div>
        <div className="card">
          <b>{(books.data?.profit ?? 0).toFixed(0)} ₪</b>
          <span>רווח</span>
        </div>
        <div className="card">
          <b>{books.data?.point_rides ?? "—"}</b>
          <span>נסיעות בקרדיט</span>
        </div>
        <div className="card">
          <b>{books.data?.points_outstanding ?? "—"}</b>
          <span>קרדיטים שטרם נוצלו ({books.data?.points_liability_rides ?? 0} נסיעות)</span>
        </div>
      </div>

      <h2>נסיעות לפי נהג</h2>
      <table>
        <thead>
          <tr>
            <th>נהג</th>
            <th>טלפון</th>
            <th>נסיעות</th>
            <th>מחזור</th>
            <th>עמלה</th>
            <th>חוב מצטבר</th>
            <th>תקבולים</th>
            <th>יתרה</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(perDriver.data ?? []).map((row) => (
            <tr key={row.driver_id}>
              <td data-label="נהג">{row.name ?? "—"}</td>
              <td data-label="טלפון">{row.phone ?? "—"}</td>
              <td data-label="נסיעות">{row.rides}</td>
              <td data-label="מחזור">{row.fares.toFixed(0)} ₪</td>
              <td data-label="עמלה">{row.commission.toFixed(0)} ₪</td>
              <td data-label="חוב מצטבר">{row.total_charges.toFixed(0)} ₪</td>
              <td data-label="תקבולים">{row.total_payments.toFixed(0)} ₪</td>
              <td data-label="יתרה" className={row.balance > 0 ? "negative" : "positive"}>
                {row.balance.toFixed(0)} ₪
              </td>
              <td>
                <button onClick={() => api.statement(row.driver_id, days).then(setStatement)}>
                  פירוט
                </button>
                <button
                  onClick={() =>
                    api
                      .sendStatement(row.driver_id, days)
                      .then((sent) => {
                        setStatement(sent);
                        setNote(sent.sent ? "הפירוט נשלח לנהג" : "אין יעד לשליחה — הפירוט מוכן");
                      })
                      .catch((err: Error) => setNote(err.message))
                  }
                >
                  שלח דרישת תשלום
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {statement && (
        <div className="panel">
          <div className="row">
            <h2>
              {statement.driver.name ?? statement.driver.phone} · יתרה{" "}
              <span className={statement.balance > 0 ? "negative" : "positive"}>
                {statement.balance.toFixed(0)} ₪
              </span>
            </h2>
            <button onClick={() => setStatement(null)}>סגור</button>
          </div>
          <div className="cards">
            <div className="card">
              <b>{statement.total_commission.toFixed(0)} ₪</b>
              <span>חובות מצטברות</span>
            </div>
            <div className="card">
              <b>{statement.total_payments.toFixed(0)} ₪</b>
              <span>תקבולים מצטברים</span>
            </div>
            <div className="card">
              <b>{statement.rides.length}</b>
              <span>נסיעות בתקופה</span>
            </div>
          </div>
          <h3>נסיעות</h3>
          <table>
            <thead>
              <tr>
                <th>מתי</th>
                <th>מוצא</th>
                <th>יעד</th>
                <th>מחיר</th>
                <th>עמלה</th>
              </tr>
            </thead>
            <tbody>
              {statement.rides.map((ride) => (
                <tr key={ride.order_id}>
                  <td data-label="מתי">{clock(ride.date)}</td>
                  <td data-label="מוצא">{ride.origin}</td>
                  <td data-label="יעד">{ride.destination}</td>
                  <td data-label="מחיר">
                    {ride.paid_with_points ? "בקרדיט" : `${ride.price.toFixed(0)} ₪`}
                  </td>
                  <td data-label="עמלה">{ride.commission.toFixed(0)} ₪</td>
                </tr>
              ))}
            </tbody>
          </table>
          {statement.payments.length > 0 && (
            <>
              <h3>תקבולים</h3>
              <table>
                <thead>
                  <tr>
                    <th>מתי</th>
                    <th>סכום</th>
                    <th>אמצעי</th>
                    <th>הערה</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.payments.map((p) => (
                    <tr key={p.id}>
                      <td data-label="מתי">{clock(p.paid_at)}</td>
                      <td data-label="סכום">{p.amount.toFixed(0)} ₪</td>
                      <td data-label="אמצעי">{p.method ?? "—"}</td>
                      <td data-label="הערה">{p.notes ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <h3>רשום תקבול</h3>
          <div className="row">
            <input
              placeholder="סכום"
              type="number"
              value={pay.amount}
              onChange={(e) => setPay({ ...pay, amount: e.target.value })}
            />
            <input
              placeholder="אמצעי תשלום"
              value={pay.method}
              onChange={(e) => setPay({ ...pay, method: e.target.value })}
            />
            <input
              placeholder="הערה"
              value={pay.notes}
              onChange={(e) => setPay({ ...pay, notes: e.target.value })}
            />
            <button
              className="action"
              onClick={() => {
                const amount = Number(pay.amount);
                if (!amount || amount <= 0) return setNote("סכום התקבול חיובי");
                api
                  .addDriverPayment(statement.driver.id, amount, pay.method, pay.notes)
                  .then(() =>
                    api.statement(statement.driver.id, days).then((s) => {
                      setStatement(s);
                      setPay({ amount: "", method: "", notes: "" });
                      setNote("התקבול נרשם");
                      perDriver.refresh();
                    })
                  )
                  .catch((err: Error) => setNote(err.message));
              }}
            >
              רשום תקבול
            </button>
          </div>
        </div>
      )}

      <Expenses onChange={() => books.refresh()} />
    </>
  );
}

function Expenses({ onChange }: { onChange: () => void }) {
  const load = useCallback(() => api.expenses(), []);
  const { data, error, refresh } = usePoll<Expense[]>(load, 60);
  const [form, setForm] = useState({ category: "", amount: "", note: "" });

  return (
    <>
      <h2>הוצאות</h2>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <input
          placeholder="קטגוריה"
          value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value })}
        />
        <input
          placeholder="סכום"
          type="number"
          value={form.amount}
          onChange={(e) => setForm({ ...form, amount: e.target.value })}
        />
        <input
          placeholder="הערה"
          value={form.note}
          onChange={(e) => setForm({ ...form, note: e.target.value })}
        />
        <button
          className="action"
          onClick={() =>
            api.addExpense(form.category, Number(form.amount), form.note).then(() => {
              setForm({ category: "", amount: "", note: "" });
              refresh();
              onChange();
            })
          }
        >
          רשום הוצאה
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>מתי</th>
            <th>קטגוריה</th>
            <th>סכום</th>
            <th>הערה</th>
          </tr>
        </thead>
        <tbody>
          {(data ?? []).map((row) => (
            <tr key={row.id}>
              <td data-label="מתי">{clock(row.spent_on)}</td>
              <td data-label="קטגוריה">{row.category}</td>
              <td data-label="סכום">{row.amount.toFixed(0)} ₪</td>
              <td data-label="הערה">{row.note ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
