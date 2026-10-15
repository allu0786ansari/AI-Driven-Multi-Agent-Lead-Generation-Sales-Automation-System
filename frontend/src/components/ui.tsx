import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

type Tone = "primary" | "quiet" | "danger" | "ghost";

const TONES: Record<Tone, string> = {
  primary: "bg-ink text-sheet hover:opacity-90 disabled:opacity-40",
  quiet: "bg-sheet text-ink border border-rule hover:bg-sheet-2 disabled:opacity-50",
  danger: "bg-sheet text-danger border border-rule hover:bg-sheet-2 disabled:opacity-50",
  ghost: "text-ink-2 hover:text-ink hover:bg-sheet-2 disabled:opacity-40",
};

export function Button({
  tone = "quiet",
  busy = false,
  icon,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone; busy?: boolean; icon?: ReactNode }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed ${TONES[tone]} ${className}`}
    >
      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-ink-3">{hint}</p>}
    </div>
  );
}

const control =
  "rounded-md border border-rule bg-sheet px-3 text-[15px] text-ink placeholder:text-ink-3 focus:border-ink-2 focus:outline-none";

/** Controls are full width unless the caller sets a width (both classes would apply and w-full would win). */
function controlClass(base: string, extra?: string, defaultHeight = true): string {
  const own = extra ?? "";
  const width = /(^|\s)(?:[a-z0-9]+:)?w-/.test(own) ? "" : "w-full";
  const height = defaultHeight && !/(^|\s)h-/.test(own) ? "h-10" : "";
  return [height, width, base, control, own].filter(Boolean).join(" ");
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={controlClass("", props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={controlClass("py-2", props.className, false)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={controlClass("", props.className)} />;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-ink-3" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 px-6 py-10 text-ink-2">
      <p className="text-[17px] font-semibold text-ink">{title}</p>
      {children && <div className="max-w-prose text-sm leading-relaxed">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const text = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <p role="alert" className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
      {text}
    </p>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-rule px-5 pt-6 pb-4 md:px-8">
      <h1 className="wide text-2xl font-extrabold">{title}</h1>
      {children}
    </header>
  );
}
