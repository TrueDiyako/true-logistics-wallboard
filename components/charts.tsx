import React from "react";

const W = 1000, H0 = 420, L = 70, R = 30, T = 20, B = 50;

const weekLabel = (w: string) => (w.includes("-W") ? "W" + w.split("-W")[1] : w);

export type Series = { name: string; values: (number | null)[]; color: string; width?: number };

// default: 0-100 %; with `count` the axis runs 0..(a round max above the data) without "%"
export function LineChart({ labels, series, height = H0, count = false, fitPct = false }:
  { labels: string[]; series: Series[]; height?: number; count?: boolean; fitPct?: boolean }) {
  const H = height;
  const dataMax = Math.max(1, ...series.flatMap((s) => s.values.filter((v) => v != null) as number[]));
  const step = count ? Math.max(1, Math.ceil(dataMax / 4 / (dataMax > 20 ? 5 : 1)) * (dataMax > 20 ? 5 : 1)) : 25;
  // fitPct: a % axis that ends just above the data (10, 20, 25, 50 or 100%) so small shares stay readable
  const fit = [10, 20, 25, 50, 100].find((t) => t >= dataMax * 1.15) ?? 100;
  const top = count ? step * 4 : fitPct ? fit : 100;
  const unit = count ? "" : "%";
  // end-of-line labels: keep at least GAP apart so values like 48% / 44% never overlap
  const GAP = 30;
  const ends = series.map((s, i) => {
    const li = s.values.map((v, k) => (v == null ? -1 : k)).filter((k) => k >= 0).pop();
    return { i, li, y: li === undefined ? 0 : T + (1 - (s.values[li] as number) / top) * (H - T - B) + 9 };
  }).filter((e) => e.li !== undefined).sort((a, b) => a.y - b.y);
  for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < GAP) ends[k].y = ends[k - 1].y + GAP;
  const over = ends.length ? ends[ends.length - 1].y - (H - B + 4) : 0;   // pushed below the axis: shift all up
  if (over > 0) ends.forEach((e) => (e.y -= over));
  const labelY: Record<number, number> = {};
  ends.forEach((e) => (labelY[e.i] = e.y));
  const n = Math.max(labels.length, 1);
  const x = (i: number) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const y = (v: number) => T + (1 - v / top) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, 1, 2, 3, 4].map((i) => i * (top / 4)).map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} className="grid" />
          <text x={L - 12} y={y(g) + 8} className="axis" textAnchor="end">{g}{unit}</text>
        </g>
      ))}
      {labels.map((l, i) => (
        <text key={l} x={x(i)} y={H - 14} className="axis" textAnchor="middle">{weekLabel(l)}</text>
      ))}
      {series.map((s, si) => {
        const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)] as [number, number]));
        const segs: string[] = [];
        let cur = "";
        pts.forEach((p) => {
          if (!p) { if (cur) segs.push(cur); cur = ""; return; }
          cur += (cur ? " L " : "M ") + p[0].toFixed(1) + " " + p[1].toFixed(1);
        });
        if (cur) segs.push(cur);
        const lastIdx = s.values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop();
        return (
          <g key={s.name}>
            {segs.map((d, i) => (
              <path key={i} d={d} fill="none" stroke={s.color} strokeWidth={s.width || 5}
                    strokeLinejoin="round" strokeLinecap="round" />
            ))}
            {pts.map((p, i) => p && <circle key={i} cx={p[0]} cy={p[1]} r={(s.width || 5) + 1} fill={s.color} />)}
            {lastIdx !== undefined && (
              <text x={x(lastIdx) + 12} y={labelY[si] ?? y(s.values[lastIdx] as number) + 9}
                    className="pointlabel" fill={s.color}>{Math.round(s.values[lastIdx] as number)}{unit}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export type Band = { key: string; label: string; color: string };

export function StackedBars({ weeks, bands }: { weeks: Record<string, any>[]; bands: Band[] }) {
  const H = H0;
  const n = Math.max(weeks.length, 1);
  const slot = (W - L - R) / n;
  const bw = slot * 0.68;
  const y = (v: number) => T + (1 - v / 100) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, 50, 100].map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} className="grid" />
          <text x={L - 12} y={y(g) + 8} className="axis" textAnchor="end">{g}%</text>
        </g>
      ))}
      {weeks.map((w, i) => {
        let acc = 0;
        const x0 = L + i * slot + (slot - bw) / 2;
        return (
          <g key={w.week}>
            {bands.map((b) => {
              const v = Number(w[b.key] || 0);
              const top = acc + v;
              const r = <rect key={b.key} x={x0} width={bw} y={y(top)} height={Math.max(0, y(acc) - y(top))} fill={b.color} />;
              acc = top;
              return r;
            })}
            <text x={x0 + bw / 2} y={H - 14} className="axis" textAnchor="middle">{weekLabel(w.week)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function HBar({ parts, height = 54 }: { parts: { label: string; value: number; color: string; ink: string }[]; height?: number }) {
  const total = parts.reduce((a, p) => a + (p.value || 0), 0) || 1;
  return (
    <div className="hbar" style={{ height }}>
      {parts.map((p) => (
        <div key={p.label} style={{ width: `${(100 * (p.value || 0)) / total}%`, background: p.color, color: p.ink }}>
          {(p.value || 0) >= 7 ? `${Math.round(p.value)}%` : ""}
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label}><i style={{ background: i.color }} />{i.label}</span>
      ))}
    </div>
  );
}

// Clustered bars: per week one bar per series, side by side; % axis fitted to the data.
export function ClusteredBars({ labels, series, height = H0 }: { labels: string[]; series: Series[]; height?: number }) {
  const H = height;
  const dataMax = Math.max(1, ...series.flatMap((s) => s.values.filter((v) => v != null) as number[]));
  const top = [10, 20, 25, 50, 100].find((t) => t >= dataMax * 1.1) ?? 100;
  const n = Math.max(labels.length, 1);
  const slot = (W - L - R) / n;
  const gw = slot * 0.78, bw = gw / Math.max(series.length, 1);
  const y = (v: number) => T + (1 - v / top) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, 1, 2, 3, 4].map((i) => i * (top / 4)).map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} className="grid" />
          <text x={L - 12} y={y(g) + 8} className="axis" textAnchor="end">{g}%</text>
        </g>
      ))}
      {labels.map((l, i) => (
        <g key={l}>
          <text x={L + slot * (i + 0.5)} y={H - 14} className="axis" textAnchor="middle">{weekLabel(l)}</text>
          {series.map((s, si) => {
            const v = s.values[i];
            if (v == null) return null;
            const x0 = L + slot * i + (slot - gw) / 2 + si * bw;
            return <rect key={si} x={x0 + 1} y={y(v)} width={Math.max(bw - 2, 1)} height={Math.max(y(0) - y(v), 0)}
                         rx={2} fill={s.color} />;
          })}
        </g>
      ))}
      {labels.length > 0 && series.map((s, si) => {
        const i = labels.length - 1, v = s.values[i];
        if (v == null) return null;
        const x0 = L + slot * i + (slot - gw) / 2 + si * bw + bw / 2;
        return <text key={si} x={x0} y={y(v) - 8} className="pointlabel small" fill={s.color} textAnchor="middle">{Math.round(v)}%</text>;
      })}
    </svg>
  );
}

// Stacked bars on a % axis fitted to the data: per week the segments stack to the week's total.
export type Seg = { value: number | null; color: string };
export function StackedTotals({ labels, stacks, height = H0 }: { labels: string[]; stacks: Seg[][]; height?: number }) {
  const H = height;
  const totals = stacks.map((st) => st.reduce((a, s) => a + (s.value || 0), 0));
  const dataMax = Math.max(1, ...totals);
  const top = [10, 20, 25, 50, 100].find((t) => t >= dataMax * 1.04) ?? 100;
  const n = Math.max(labels.length, 1);
  const slot = (W - L - R) / n;
  const bw = slot * 0.62;
  const y = (v: number) => T + (1 - v / top) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, 1, 2, 3, 4].map((i) => i * (top / 4)).map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} className="grid" />
          <text x={L - 12} y={y(g) + 8} className="axis" textAnchor="end">{Math.round(g * 10) / 10}%</text>
        </g>
      ))}
      {labels.map((l, i) => {
        const x0 = L + slot * i + (slot - bw) / 2;
        let acc = 0;
        return (
          <g key={l}>
            <text x={L + slot * (i + 0.5)} y={H - 14} className="axis" textAnchor="middle">{weekLabel(l)}</text>
            {stacks[i].map((s, k) => {
              if (!s.value) return null;
              const y1 = y(acc + s.value), y0 = y(acc);
              acc += s.value;
              return <rect key={k} x={x0} y={y1} width={bw} height={Math.max(y0 - y1, 0)} fill={s.color} />;
            })}
            {i === labels.length - 1 && totals[i] > 0 && (
              <text x={x0 + bw / 2} y={y(totals[i]) - 10} className="pointlabel small" textAnchor="middle" fill="currentColor">
                {Math.round(totals[i])}%</text>)}
          </g>
        );
      })}
    </svg>
  );
}
