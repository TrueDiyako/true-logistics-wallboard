"use client";
import React, { useEffect, useRef, useState } from "react";
import { DASHBOARDS, Data } from "@/components/dashboards";
import { fmtClockDate, fmtTime } from "@/lib/fmt";

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
  // updates run Mon-Fri: order lists every 30 min 07:00-16:30, KPIs at 06:15
  const mins = now.getHours() * 60 + now.getMinutes();
  const weekday = now.getDay() >= 1 && now.getDay() <= 5;
  if (weekday && mins >= 7 * 60 + 20 && mins <= 17 * 60 && hoursSince(l?.at) > 1.25)
    w.push("Order lists not updated for over an hour (live job)");
  if (weekday && mins >= 7 * 60 && hoursSince(n?.at) > 20)
    w.push("KPI graphs not updated this morning (nightly job)");
  for (const h of [n, l]) for (const e of h?.errors || []) w.push(`${h.job}: ${e.section} failing`);
  const exp = n?.graph_secret_expires;
  if (exp) {
    const days = (new Date(exp).getTime() - Date.now()) / 8.64e7;
    if (days < 30) w.push(`Microsoft Graph secret expires in ${Math.max(0, Math.floor(days))} days`);
  }
  return w;
}

const PAUSE_MS = 10 * 60 * 1000;            // a manual page choice holds rotation for 10 minutes

export default function Wallboard() {
  const [data, setData] = useState<Data | null>(null);
  const [fails, setFails] = useState(0);
  const [idx, setIdx] = useState(0);
  const [now, setNow] = useState(new Date());
  const [pausedUntil, setPausedUntil] = useState(0);   // 0 = rotating, Infinity = paused until resumed
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
    const b = setInterval(() => setNow(new Date()), 15 * 1000);
    const c = setTimeout(() => window.location.reload(), RELOAD_MS);
    return () => { clearInterval(a); clearInterval(b); clearTimeout(c); };
  }, []);

  // go to a page by hand: rotation waits 10 minutes, then carries on from there
  const go = (i: number) => { setIdx((i + DASHBOARDS.length) % DASHBOARDS.length); setPausedUntil((p) => (p === Infinity ? p : Date.now() + PAUSE_MS)); };
  const togglePause = () => setPausedUntil((p) => (p === Infinity || p > Date.now() ? 0 : Infinity));

  // a click inside a panel (e.g. a filter) holds the rotation like a page choice
  useEffect(() => {
    const hold = () => setPausedUntil((p) => (p === Infinity ? p : Date.now() + PAUSE_MS));
    window.addEventListener("wallboard:interact", hold);
    return () => window.removeEventListener("wallboard:interact", hold);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "PageDown") go(idx + 1);
      if (e.key === "ArrowLeft" || e.key === "PageUp") go(idx - 1);
      if (e.key === " ") { e.preventDefault(); togglePause(); }
      const n = Number(e.key);
      if (n >= 1 && n <= DASHBOARDS.length) go(n - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const paused = pinned.current !== null || pausedUntil === Infinity || pausedUntil > now.getTime();
  useEffect(() => {
    if (pinned.current !== null) return;
    const wait = Math.max(DASHBOARDS[idx].seconds * 1000, pausedUntil > Date.now() && pausedUntil !== Infinity ? pausedUntil - Date.now() : 0);
    if (pausedUntil === Infinity) return;
    const t = setTimeout(() => { setPausedUntil(0); setIdx((i) => (i + 1) % DASHBOARDS.length); }, wait);
    return () => clearTimeout(t);
  }, [idx, pausedUntil]);

  const dash = DASHBOARDS[idx];
  const warn = warnings(data, fails);
  const updated = data?.live?.generated_at || data?.kpi?.generated_at;
  const resumeAt = pausedUntil !== Infinity && pausedUntil > now.getTime() ? fmtTime(new Date(pausedUntil)) : "";

  return (
    <main className="wall">
      <header>
        <nav className="tabs">
          <button className="arrow" onClick={() => go(idx - 1)} aria-label="Previous page">‹</button>
          {DASHBOARDS.map((d, i) => (
            <button key={d.id} className={"tab" + (i === idx ? " on" : "")} onClick={() => go(i)}>{d.tab}</button>
          ))}
          <button className="arrow" onClick={() => go(idx + 1)} aria-label="Next page">›</button>
          <button className={"pause" + (paused ? " on" : "")} onClick={togglePause}
                  title={paused ? "Resume rotation" : "Pause rotation"}>{paused ? "▶" : "❚❚"}</button>
        </nav>
        <div className="meta">
          {paused && <span className="paused">{pausedUntil === Infinity ? "paused" : resumeAt ? `rotation resumes ${resumeAt}` : "pinned"} · </span>}
          {updated && <>updated {fmtTime(new Date(updated))} · </>}
          {fmtClockDate(now)} {fmtTime(now)}
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
