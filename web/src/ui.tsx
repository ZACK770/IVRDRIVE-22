import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/* ------------------------------------------------------------------ toasts */

export interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}

interface ToastApi {
  success: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
}

const ToastContext = createContext<ToastApi>({
  success: () => {},
  error: () => {},
  info: () => {},
});

export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = next.current++;
    setToasts((current) => [...current, { id, kind, text }]);
    // Errors stay longer: the dispatcher needs time to read the reason.
    const ttl = kind === "error" ? 8000 : 4000;
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), ttl);
  }, []);

  const api = useRef<ToastApi>({
    success: (text) => push("success", text),
    error: (text) => push("error", text),
    info: (text) => push("info", text),
  });

  return (
    <ToastContext.Provider value={api.current}>
      {children}
      <div className="toasts">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`}>
            {toast.kind === "success" ? "✓ " : toast.kind === "error" ? "✗ " : ""}
            {toast.text}
            <button
              className="toast-close"
              onClick={() => setToasts((current) => current.filter((t) => t.id !== toast.id))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/* ------------------------------------------------------------------ modal */

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " modal-wide" : ""}`} dir="rtl">
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="modal-x" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- confirm */

export function Confirm({
  text,
  onYes,
  onNo,
}: {
  text: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <Modal title="אישור פעולה" onClose={onNo}>
      <p>{text}</p>
      <div className="row">
        <button className="action danger" onClick={onYes}>
          כן, בצע
        </button>
        <button className="action" onClick={onNo}>
          ביטול
        </button>
      </div>
    </Modal>
  );
}

/* --------------------------------------------------------- countdown */

/** Seconds left until `iso` (UTC timestamp from the API), ticking every second. */
export function useCountdown(iso: string | null): number {
  const [, force] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  if (!iso) return 0;
  const closes = Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Math.max(0, Math.round((closes - Date.now()) / 1000));
}

export const mmss = (total: number) =>
  `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
