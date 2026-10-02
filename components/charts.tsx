import React from "react";

const W = 1000, H = 420, L = 70, R = 30, T = 20, B = 50;

const weekLabel = (w: string) => (w.includes("-W") ? "W" + w.split("-W")[1] : w);

export type Series = { name: string; values: (number | null)[]; color: string; width?: number };

export function LineChart({ labels, series }: { labels: string[]; series: Series[] }) {
  const n = Math.max(labels.length, 1);
  const x = (i: number) => L + (n === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const y = (v: number) => T + (1 - v / 100) * (H - T - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img">
      {[0, 25, 50, 75, 100].map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} className="grid" />
          <text x={L - 12} y={y(g) + 8} className="axis" textAnchor="end">{g}%</text>
        </g>
      ))}
      {labels.map((l, i) => (
        <text key={l} x={x(i)} y={H - 14} className="axis" textAnchor="middle">{weekLabel(l)}</text>
      ))}
      {series.map((s) => {
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
              <text x={x(lastIdx) + 12} y={y(s.values[lastIdx] as number) + 9}
                    className="pointlabel" fill={s.color}>{Math.round(s.values[lastIdx] as number)}%</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export type Band = { key: string; label: string; color: string };

export function StackedBars({ weeks, bands }: { weeks: Record<string, any>[]; bands: Band[] }) {
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
