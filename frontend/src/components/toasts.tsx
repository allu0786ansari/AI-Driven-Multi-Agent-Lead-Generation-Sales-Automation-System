import { X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

interface Toast {
  id: number;
  text: string;
  tone: "info" | "hot" | "error";
  to?: string;
}

interface ToastApi {
  show: (text: string, opts?: { tone?: Toast["tone"]; to?: string }) => void;
}

const Ctx = createContext<ToastApi>({ show: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const show = useCallback<ToastApi["show"]>(
    (text, opts) => {
      const id = Date.now() + Math.random();
      setToasts((t) => [...t.slice(-3), { id, text, tone: opts?.tone ?? "info", to: opts?.to }]);
      setTimeout(() => dismiss(id), opts?.tone === "error" ? 8000 : 6000);
    },
    [dismiss],
  );
  const api = useMemo(() => ({ show }), [show]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed top-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="arrive pointer-events-auto flex items-start gap-3 rounded-md border border-rule bg-sheet p-3 text-sm shadow-lg"
            style={{ borderLeft: `4px solid ${t.tone === "hot" ? "var(--hot)" : t.tone === "error" ? "var(--danger)" : "var(--ink-2)"}` }}
          >
            {t.to ? (
              <Link to={t.to} className="flex-1 font-medium text-ink hover:underline" onClick={() => dismiss(t.id)}>
                {t.text}
              </Link>
            ) : (
              <span className="flex-1 text-ink">{t.text}</span>
            )}
            <button type="button" onClick={() => dismiss(t.id)} className="text-ink-3 hover:text-ink" aria-label="Dismiss">
              <X className="size-4" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
