"use client";
import React, { useEffect, useRef, useState } from "react";
import { DASHBOARDS, Data } from "@/components/dashboards";

const REFRESH_MS = 5 * 60 * 1000;          // data
const RELOAD_MS = 6 * 60 * 60 * 1000;      // full page reload (picks up new deployments)

function hoursSince(iso?: string) {
  return iso ? (Date.now() - new Date(iso).getTime()) / 3.6e6 : Infinity;
}

function warnings(d: Data | null, fetchFails: number): string[] {
  const w: string[] = [];
  if (fetchFails >= 2) w.push("Cannot reach the wallboard server");
  if (!d) return w;
  const n = d.health?.nightly, l = d.health?.live;
  const now = new Date();
  const working = now.getDay() >= 1 && now.getDay() <= 5 && now.getHours() >= 7 && now.getHours() < 18;
  if (hoursSince(n?.at) > 30) w.push("KPI graphs not updated for over a day (nightly job)");
  if (working && hoursSince(l?.at) > 2.5) w.push("Order lists not updated for over 2 hours (live job)");
  for (const h of [n, l]) for (const e of h?.errors || []) w.push(`${h.job}: ${e.section} failing`);
  const exp = n?.graph_secret_expires;
  if (exp) {
    const days = (new Date(exp).getTime() - Date.now()) / 8.64e7;
    if (days < 30) w.push(`Microsoft Graph secret expires in ${Math.max(0, Math.floor(days))} days`);
  }
  return w;
}

export default function Wallboard() {
  const [data, setData] = useState<Data | null>(null);
  const [fails, setFails] = useState(0);
  const [idx, setIdx] = useState(0);
  const [now, setNow] = useState(new Date());
  const pinned = useRef<number | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("d");
    if (q && !isNaN(Number(q))) { pinned.current = Math.max(0, Math.min(DASHBOARDS.length - 1, Number(q) - 1)); setIdx(pinned.current); }
    const load = async () => {
      try {
        const r = await fetch("/api/data", { cache: "no-store" });
        if (!r.ok) throw new Error(String(r.status));
        setData(await r.json()); setFails(0);
      } catch { setFails((f) => f + 1); }
    };
    load();
    const a = setInterval(load, REFRESH_MS);
    const b = setInterval(() => setNow(new Date()), 30 * 1000);
    const c = setTimeout(() => window.location.reload(), RELOAD_MS);
    return () => { clearInterval(a); clearInterval(b); clearTimeout(c); };
  }, []);

  useEffect(() => {
    if (pinned.current !== null) return;
    const t = setTimeout(() => setIdx((i) => (i + 1) % DASHBOARDS.length), DASHBOARDS[idx].seconds * 1000);
    return () => clearTimeout(t);
  }, [idx]);

  const dash = DASHBOARDS[idx];
  const warn = warnings(data, fails);
  const updated = data?.live?.generated_at || data?.kpi?.generated_at;

  return (
    <main className="wall">
      <header>
        <div className="title">Logistics · {dash.title}</div>
        <div className="dots">{DASHBOARDS.map((d, i) => <i key={d.id} className={i === idx ? "on" : ""} />)}</div>
        <div className="meta">
          {updated && <>updated {new Date(updated).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })} · </>}
          {now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}{" "}
          {now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
        </div>
      </header>
      {warn.length > 0 && <div className="banner">{warn.join("  ·  ")}</div>}
      <div className="grid" style={{ gridTemplateAreas: dash.layout, gridTemplateColumns: dash.columns, gridTemplateRows: dash.rows }}>
        {Object.entries(dash.panels).map(([area, Panel]) => (
          <div key={area} style={{ gridArea: area, minWidth: 0, minHeight: 0 }}>
            {data ? Panel(data) : <section className="panel"><div className="missing">Loading…</div></section>}
          </div>
        ))}
      </div>
    </main>
  );
}
