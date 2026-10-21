import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

/** Width of an element, kept up to date (charts draw in real pixels so text never stretches). */
function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** A clean axis maximum: 1, 2, 2.5, 5 or 10 times a power of ten, at or above `max`. */
export function niceMax(max: number): number {
  if (max <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(max));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= max) return m * p;
  return 10 * p;
}

function compact(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

export interface Column {
  key: string;
  label: string; // shown on the axis and in the tooltip, e.g. "18 Oct"
  value: number;
  display: string; // the value as text, e.g. "3 leads" or "₹1.73"
}

const HEIGHT = 168;
const PAD = { top: 12, right: 8, bottom: 22, left: 34 };

/**
 * Single-series column chart: thin columns (<= 24px) with 4px rounded tops, one baseline,
 * hairline grid, tooltip on hover and keyboard focus (arrow keys), and a table view.
 */
export function ColumnChart({ data, title, unit }: { data: Column[]; title: string; unit: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  useEffect(() => setActive(null), [data]);

  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const ticks = [0, max / 2, max];
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const slot = data.length ? plotW / data.length : 0;
  const barW = Math.max(2, Math.min(24, slot * 0.62));
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(plotW / 64))));
  const total = data.reduce((sum, d) => sum + d.value, 0);

  const onKey = (e: KeyboardEvent) => {
    if (!data.length) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const step = e.key === "ArrowRight" ? 1 : -1;
      setActive((i) => Math.min(data.length - 1, Math.max(0, (i ?? (step > 0 ? -1 : data.length)) + step)));
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setActive(e.key === "Home" ? 0 : data.length - 1);
    } else if (e.key === "Escape") setActive(null);
  };

  const a = active != null ? data[active] : null;
  const ax = active != null ? PAD.left + slot * active + slot / 2 : 0;

  return (
    <figure className="flex flex-col gap-2">
      <div
        ref={ref}
        className="relative outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]"
        tabIndex={0}
        role="img"
        aria-label={`${title}. ${data.length} days, ${compact(total)} ${unit} in total. Use the arrow keys, Home and End to read each day.`}
        onKeyDown={onKey}
        onBlur={() => setActive(null)}
        onPointerLeave={() => setActive(null)}
      >
        {width > 0 && (
          <svg width={width} height={HEIGHT} aria-hidden className="block">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--rule)" strokeWidth={1} />
                <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="tabular fill-[var(--ink-3)] text-[11px]">
                  {compact(t)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = PAD.left + slot * i + slot / 2;
              const top = y(d.value);
              const h = PAD.top + plotH - top;
              const r = Math.min(4, h, barW / 2);
              const x0 = cx - barW / 2;
              const x1 = cx + barW / 2;
              const base = PAD.top + plotH;
              return (
                <g key={d.key} onPointerEnter={() => setActive(i)} onPointerDown={() => setActive(i)}>
                  {/* hit target: the whole slot, bigger than the mark */}
                  <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={plotH} fill="transparent" />
                  {h > 0 && (
                    <path
                      d={`M${x0},${base} V${top + r} Q${x0},${top} ${x0 + r},${top} H${x1 - r} Q${x1},${top} ${x1},${top + r} V${base} Z`}
                      fill="var(--chart)"
                      opacity={active == null || active === i ? 1 : 0.55}
                    />
                  )}
                  {i % labelEvery === 0 && (
                    <text x={cx} y={HEIGHT - 6} textAnchor="middle" className="fill-[var(--ink-3)] text-[11px]">
                      {d.label}
                    </text>
                  )}
                </g>
              );
            })}
            {a && <line x1={ax} x2={ax} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--ink-3)" strokeWidth={1} />}
          </svg>
        )}
        {a && (
          <div
            className="pointer-events-none absolute top-0 z-10 rounded-md border border-rule bg-sheet px-2.5 py-1.5 text-sm shadow-sm"
            style={{ left: Math.min(Math.max(ax - 60, 0), Math.max(0, width - 130)), width: 130 }}
          >
            <p className="font-semibold text-ink">{a.display}</p>
            <p className="text-xs text-ink-3">{a.label}</p>
          </div>
        )}
      </div>
      <details className="text-sm text-ink-2">
        <summary className="cursor-pointer text-xs text-ink-3 hover:text-ink">Show as a table</summary>
        <table className="mt-2 w-full max-w-sm text-left">
          <tbody>
            {data.map((d) => (
              <tr key={d.key} className="border-b border-rule last:border-0">
                <td className="py-1 pr-4 text-ink-3">{d.label}</td>
                <td className="tabular py-1 text-right text-ink">{d.display}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Horizontal bars with the value at the tip: for a funnel or a breakdown (single series). */
export function BarList({ rows }: { rows: { label: string; value: number; display: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(6.5rem,9rem)_1fr] items-center gap-3 text-sm">
          <span className="truncate text-ink-2">{r.label}</span>
          <span className="flex items-center gap-2">
            <span
              className="h-3 rounded-r-[4px]"
              style={{ width: `${(r.value / max) * 85}%`, minWidth: r.value ? 3 : 0, background: "var(--chart)" }}
              aria-hidden
            />
            <span className="tabular shrink-0 text-ink">{r.display}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
