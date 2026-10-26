import React, { useState } from "react";
import { LineChart, HBar, Legend } from "./charts";
import { fmtDay, fmtDayNum, fmtDayMonth, fmtDayMonthLong, fmtNum, fmtOneDecimal } from "@/lib/fmt";

export type Data = { kpi: any; live: any; health: any };
type PanelFn = (d: Data) => React.ReactNode;

// natural, warm, earthy palette (olive, ochre, clay, terracotta, umber)
const C = { otif: "#5E6B2E", onTime: "#8C5B3E", inFull: "#C9922E", dispatch: "#5E6B2E",
            credit: "#B4532A", reply: "#5E6B2E", noReply: "#A2442A" };
const BANDS = [
  { key: "within 1 day", label: "Within 1 day", color: "#5E6B2E", ink: "#F7F2E7" },
  { key: "within 2 days", label: "Within 2 days", color: "#93A37E", ink: "#26301A" },
  { key: "within 3 days", label: "Within 3 days", color: "#D8C3A0", ink: "#4A3B26" },
  { key: "over 3 days", label: "Over 3 days", color: "#D08A5E", ink: "#3D1F10" },
  { key: "no reply", label: "No reply", color: "#A2442A", ink: "#F7F2E7" },
];

const pct = (v: any) => (v == null ? "–" : `${Math.round(Number(v))}%`);
const dkk = (v: any) => (v == null ? "–" : `${fmtNum(Number(v) / 1000)}k`);
const money = (v: any) => (v == null ? "–" : Math.abs(Number(v)) >= 1e6
  ? `${fmtOneDecimal(Number(v) / 1e6)}M` : dkk(v));
const day = (s: string) => fmtDay(s);

function Panel({ title, children, note, badge, badgeBad, compact }:
  { title: string; children: React.ReactNode; note?: string; badge?: string; badgeBad?: boolean; compact?: boolean }) {
  return (
    <section className={"panel" + (compact ? " compact" : "")}>
      <h2>{title}{badge !== undefined && <span className={"badge" + (badgeBad ? " bad" : "")}>{badge}</span>}
        {note && <small>{note}</small>}</h2>
      <div className="panel-body">{children}</div>
    </section>
  );
}

function Missing() {
  return <div className="missing">Waiting for first data run</div>;
}

function Headlines({ items }: { items: { label: string; value: string; color?: string; onClick?: () => void }[] }) {
  return (
    <div className="headlines">
      {items.map((i) => (
        <div key={i.label} className={i.onClick ? "clickable" : ""} onClick={i.onClick}
             title={i.onClick ? "Click for the list" : undefined}>
          <b style={{ color: i.color }}>{i.value}</b><span>{i.label}</span></div>
      ))}
    </div>
  );
}

function List({ rows, max, render, empty }: { rows: any[]; max: number; render: (r: any) => React.ReactNode; empty: string }) {
  if (!rows || !rows.length) return <div className="empty">{empty}</div>;
  return (
    <div className="list">
      {rows.slice(0, max).map((r, i) => <div className="row" key={i}>{render(r)}</div>)}
      {rows.length > max && <div className="more">+ {rows.length - max} more</div>}
    </div>
  );
}

// ---------------- dashboard 1: performance ----------------
const otifPanel: PanelFn = ({ kpi }) => {
  const o = kpi?.otif;
  if (!o) return <Panel title="On time in full"><Missing /></Panel>;
  const w = o.weekly || [];
  return (
    <Panel title="On time in full" note="% of orders · last 13 weeks">
      <Headlines items={[
        { label: "OTIF", value: pct(o.headline.otif_pct), color: C.otif },
        { label: "On time", value: pct(o.headline.on_time_pct), color: C.onTime },
        { label: "In full", value: pct(o.headline.in_full_pct), color: C.inFull },
      ]} />
      <LineChart labels={w.map((x: any) => x.week)} series={[
        { name: "OTIF", values: w.map((x: any) => x.otif_pct), color: C.otif, width: 7 },
        { name: "On time", values: w.map((x: any) => x.on_time_pct), color: C.onTime, width: 4 },
        { name: "In full", values: w.map((x: any) => x.in_full_pct), color: C.inFull, width: 4 },
      ]} />
    </Panel>
  );
};

const shortsPanel: PanelFn = ({ kpi }) => {
  const s = kpi?.shorts;
  if (!s) return <Panel title="Short picks"><Missing /></Panel>;
  const w = s.weekly || [];
  return (
    <Panel title="Short picks" note="average % of each order's volume not picked · per week">
      <Headlines items={[
        { label: "of an order's volume short, on average", value: pct(s.avg_short_pct), color: C.credit },
        { label: "of orders have at least one short line", value: pct(s.orders_with_short_pct), color: C.inFull },
      ]} />
      <LineChart labels={w.map((x: any) => x.week)} series={[{ name: "Short", values: w.map((x: any) => x.pct), color: C.credit, width: 7 }]} />
    </Panel>
  );
};

// AI adoption: use of the three Claude skills, per week from week 42 (live job, skill logs on Drive)
const SKILL_COLORS: Record<string, string> = {
  "tracelink-order-closeout": "#5E6B2E", "internal-purchase": "#8C5B3E", "process-weclapp-order": "#C9922E",
  "pick-3pl-goods": "#4F7A80" };
const SKILL_LABEL: Record<string, string> = {
  "tracelink-order-closeout": "order closeouts", "internal-purchase": "internal purchases",
  "process-weclapp-order": "weclapp orders", "pick-3pl-goods": "3PL picks" };
const aiPanel: PanelFn = ({ live }) => {
  const a = live?.ai_adoption;
  if (!a) return <Panel title="AI adoption"><Missing /></Panel>;
  if (!a.connected) return <Panel title="AI adoption" note="Claude skills"><div className="missing">Skill log not connected yet</div></Panel>;
  const w = a.weekly || [];
  return (
    <Panel title="AI adoption" note="uses of the Claude skills · this week and per week">
      <Headlines items={a.skills.map((s: string) => ({ label: SKILL_LABEL[s] || s,
                                                      value: String(a.this_week[s] ?? 0), color: SKILL_COLORS[s] }))} />
      <LineChart count labels={w.map((x: any) => x.week)}
                 series={a.skills.map((s: string, i: number) => ({ name: s, values: w.map((x: any) => x[s]),
                                                                    color: SKILL_COLORS[s], width: i === 0 ? 7 : 5 }))} />
    </Panel>
  );
};

const responsePanel: PanelFn = ({ kpi }) => {
  const r = kpi?.response;
  if (!r) return <Panel title="Response time"><Missing /></Panel>;
  const t = r.trend || [];
  const nr = r.no_reply, oc = r.outside_copy;
  return (
    <Panel title="Response time" note="first reply to new customer emails · business days">
      <Headlines items={[
        { label: "answered within 1 day", value: pct(r.headline.within_1_day_pct), color: C.reply },
        { label: "no reply found", value: pct(r.headline.no_reply_pct), color: C.noReply },
      ]} />
      <LineChart labels={t.map((x: any) => x.week)} series={[{ name: "Within 1 day", values: t.map((x: any) => x.pct), color: C.reply, width: 7 }]} height={250} />
      {r.last4 && (
        <div className="split">
          <HBar height={34} parts={BANDS.map((b) => ({ label: b.label, value: r.last4[b.key], color: b.color, ink: b.ink }))} />
          <div className="splitrow"><span className="splitlabel">Last 4 weeks · {r.last4.waits} emails</span><Legend items={BANDS} /></div>
        </div>
      )}
      <div className="notes">
        {nr && nr.count > 0 && <span>No reply: <b>{pct(nr.order_in_tracelink_pct)}</b> were orders already in TraceLink (never confirmed)</span>}
        {oc && oc.count > 0 && <span>Answered without order@ in copy: <b>{pct(oc.pct)}</b> · {(oc.by_person || []).slice(0, 4).map((p: any) => `${p[0]} ${p[1]}`).join(" · ")}</span>}
      </div>
    </Panel>
  );
};

const OWNER_COLORS: Record<string, string> = {
  Valentina: "#4F7A80", Frederikke: "#C9922E", Andreas: "#8C5B3E", "No owner": "#A8A29A" };

// the credit notes behind the "not closed out" figure; opens on click (browser), closes on click
function CloseoutList({ co, onClose }: { co: any; onClose: () => void }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlaybox" onClick={(e) => e.stopPropagation()}>
        <div className="overlayhead"><b>Credit notes since week 41</b>
          <span>{co.without_closeout} of {co.credit_notes} on orders not closed out with the order-closeout skill</span>
          <button onClick={onClose}>Close</button></div>
        <table className="table">
          <thead><tr><th>Credit note</th><th>Date</th><th>Customer</th><th>Customer order</th><th>TraceLink order</th><th>Order closeout</th></tr></thead>
          <tbody>
            {(co.detail || []).map((r: any) => (
              <tr key={r.credit_note}>
                <td>{r.credit_note}</td><td>{r.date ? day(r.date) : ""}</td><td>{r.customer}</td><td className="po">{r.po}</td>
                <td>{(r.orders || []).join(", ") || "not found"}</td>
                <td className={r.closeout ? "good" : "bad"}>{r.closeout ? "used" : "not used"}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CreditPanel({ c }: { c: any }) {
  const [open, setOpen] = useState(false);
  const w = c.weekly || [];
  const co = c.closeout;
  const items: any[] = [{ label: `${c.headline.credits} of ${c.headline.invoices} invoices, 13 weeks`,
                          value: pct(c.headline.credit_pct), color: C.credit }];
  if (co && co.credit_notes)
    items.push({ label: "Percentage of credit notes not using order-closeout skill",
                 value: pct(co.pct_without_closeout), color: C.noReply, onClick: () => setOpen(true) });
  else if (c.closeout_error)
    items.push({ label: "Percentage of credit notes not using order-closeout skill (not available)",
                 value: "–", color: C.noReply });
  return (
    <Panel title="Credit notes" note="% of invoices credited">
      <Headlines items={items} />
      <LineChart labels={w.map((x: any) => x.week)}
                 series={[{ name: "Credited", values: w.map((x: any) => x.pct), color: C.credit, width: 7 }]} />
      {open && co && <CloseoutList co={co} onClose={() => setOpen(false)} />}
    </Panel>
  );
}

const creditPanel: PanelFn = ({ kpi }) => {
  const c = kpi?.credit;
  if (!c) return <Panel title="Credit notes"><Missing /></Panel>;
  return <CreditPanel c={c} />;
};

// ---------------- dashboard 2: action now ----------------
const emailsPanel: PanelFn = ({ live }) => {
  const rows = live?.emails_waiting;
  if (!rows) return <Panel title="Emails waiting over 1 day"><Missing /></Panel>;
  const by: Record<string, number> = {};
  rows.forEach((r: any) => { by[r.owner || "Unassigned"] = (by[r.owner || "Unassigned"] || 0) + 1; });
  const owners = Object.entries(by).sort((a, b) => (a[0] === "Unassigned" ? 1 : b[0] === "Unassigned" ? -1 : b[1] - a[1]));
  return (
    <Panel title="Emails waiting over 1 day" note={`${rows.length} waiting`}>
      <div className="chips">{owners.map(([o, n]) => <span key={o} className={o === "Unassigned" ? "chip muted" : "chip"}>{o} <b>{n}</b></span>)}</div>
      <List rows={rows} max={18} empty="No customer is waiting"
            render={(r) => (<><span className="owner">{r.owner || "Unassigned"}</span>
                              <span className="grow"><b>{r.company}</b> · {r.subject}</span>
                              <span className="bad">{Math.round(r.waiting_days)} d</span></>)} />
    </Panel>
  );
};

// clicking a counter filters the list (click again = all); it also holds the rotation
// for 10 minutes so the page doesn't change while someone reads it
type DueFilter = "all" | "overdue" | "today" | "next";
function DueList({ rows }: { rows: any[] }) {
  const [filter, setFilter] = useState<DueFilter>("all");
  const today = new Date().toISOString().slice(0, 10);
  const kind = (r: any): DueFilter => (r.overdue ? "overdue" : r.start === today ? "today" : "next");
  const n = (k: DueFilter) => rows.filter((r) => kind(r) === k).length;
  const pick = (k: DueFilter) => {
    setFilter((f) => (f === k ? "all" : k));
    window.dispatchEvent(new Event("wallboard:interact"));
  };
  const shown = rows.filter((r) => filter === "all" || kind(r) === filter)
    .sort((a, b) => (b.days_late || 0) - (a.days_late || 0) || (a.start < b.start ? -1 : 1));
  const chip = (k: DueFilter, label: string, cls = "") => (
    <button className={"chip clickable " + cls + (filter === k ? " active" : "")} onClick={() => pick(k)}>
      {label} <b>{n(k)}</b></button>);
  return (
    <>
      <div className="chips">{chip("overdue", "Overdue", "bad")}{chip("today", "Due today")}{chip("next", "Next 3 days")}</div>
      <List rows={shown} max={9} empty={filter === "all" ? "Everything due is ready" : "None"}
            render={(r) => (<><span className="grow">{r.customer} · {r.order}{r.po ? ` · ${r.po}` : ""}</span>
                              <span className="muted2">{r.status === "Partially ready for warehouse" ? "Partially ready" : "Not ready"}</span>
                              {r.overdue ? <span className="bad">{r.days_late} d late</span>
                                         : <span className="warn">{r.start === today ? "today" : day(r.start)}</span>}</>)} />
    </>
  );
}

const duePanel: PanelFn = ({ live }) => {
  const rows = live?.due_not_ready;
  if (!rows) return <Panel title="Due in 3 days, not ready"><Missing /></Panel>;
  return <Panel title="Due in 3 days, not ready" note="oldest first · click a counter to filter"><DueList rows={rows} /></Panel>;
};

const creditsCheckPanel: PanelFn = ({ kpi }) => (
  <Panel title="Credits to check">
    {!kpi?.credit ? <Missing /> :
      <List rows={kpi.credit.to_check || []} max={6} empty="Nothing to check"
            render={(r) => (<><span className="grow">{r.customer} · {r.chain || `invoice ${r.reverses_invoice}`} · {r.reason}</span>
                              <span className="bad">{dkk(-r.amount_dkk)}</span></>)} />}
  </Panel>
);

const customersPanel: PanelFn = ({ kpi }) => {
  const c = kpi?.customers;
  if (!c) return <Panel title="Customers"><Missing /></Panel>;
  const col = (title: string, rows: any[], cls: string) => (
    <div className="rankcol">
      <h3>{title}</h3>
      {rows.map((r, i) => (
        <div className="row" key={i}><span className="grow">{i + 1}. {r.customer}</span>
          <span className={cls}>{pct(r.otif_pct)}</span></div>
      ))}
    </div>
  );
  return (
    <Panel title="Customers" note={`OTIF, ${c.pool} customers with most orders`}>
      <Headlines items={[{ label: "order fill rate", value: pct(c.order_fill_rate_pct), color: C.inFull }]} />
      <div className="rank">{col("Best OTIF", c.best || [], "good")}{col("Worst OTIF", c.worst || [], "bad")}</div>
    </Panel>
  );
};

// weclapp (German market) orders with no TraceLink order yet - replaces the OTIF customer
// ranking on dashboard 2 (the ranking is still computed nightly: customersPanel, kept for later)
const weclappPanel: PanelFn = ({ live }) => {
  const w = live?.weclapp;
  if (!w) return <Panel title="weclapp not in TraceLink" compact><Missing /></Panel>;
  return (
    <Panel title="weclapp not in TraceLink" compact badge={`${w.missing_count} of ${w.orders}`} badgeBad={w.missing_count > 0}
           note={`created from ${fmtDayMonth(w.tracking_from)}`}>
      <List rows={w.missing} max={4} empty="Every weclapp order is in TraceLink"
            render={(r) => (<><span className="grow"><b>{r.customer}</b>
                                <span className="sub">PO {r.po || "missing"} · weclapp #{r.weclapp}</span></span>
                              <span className={r.age_days >= 2 ? "bad" : "warn"}>{r.age_days === 0 ? "today" : `${r.age_days} d`}</span></>)} />
    </Panel>
  );
};

// Hannover: Dachser has a delivery note (picked) but TraceLink still has reservations
const dachserPickPanel: PanelFn = ({ live }) => {
  const p = live?.dachser_pick;
  const title = "Picked by Dachser, not in TraceLink";
  if (!p) return <Panel title={title} compact><Missing /></Panel>;
  if (!p.connected) return <Panel title={title} compact><div className="missing">Dachser key not connected yet</div></Panel>;
  return (
    <Panel title={title} compact badge={String(p.count)} badgeBad={p.count > 0} note={"all customers · checked every 30 min" + (p.no_answer ? ` · ${p.no_answer} not answered by Dachser, retried next run` : "")}>
      <List rows={p.orders} max={6} empty="TraceLink is up to date with Dachser"
            render={(r) => (<><span className="grow"><b>{r.customer}</b>
                                <span className="sub">PO {r.po} · order {r.order} · {fmtNum(r.reserved_cases)} cs</span></span>
                              <span className={(r.note_days ?? 0) >= 1 || r.check_by_hand ? "bad" : "warn"}>
                                {r.check_by_hand ? "check PO" : r.note_days == null ? "" : r.note_days === 0 ? "today" : `${r.note_days} d`}</span></>)} />
    </Panel>
  );
};

// ---------------- dashboard 3: status board ----------------
const statusBoard: PanelFn = ({ live }) => {
  const b = live?.status_board;
  if (!b) return <Panel title="Open orders by status"><Missing /></Panel>;
  return (
    <Panel title="Open orders by status" note="start date within 2 weeks · overdue in red · shipped: last 2 months">
      <div className="board">
        {b.map((col: any) => (
          <div className={"boardcol" + (col.status === "Shipped - not closed" ? " shippedcol" : "")} key={col.status}>
            <h3>{col.status} <span>{col.orders.length}</span></h3>
            {col.orders.slice(0, 14).map((o: any, i: number) => (
              <div className={"card" + (o.overdue ? " overdue" : "") + (col.status === "Shipped - not closed" ? " shipped" : "")} key={i}>
                <div className="cardtop"><span className="grow">{o.customer}</span><span>{o.order}</span></div>
                <div className="cardpo">{o.po || "–"}</div>
              </div>
            ))}
            {col.orders.length > 14 && <div className="more">+ {col.orders.length - 14} more</div>}
          </div>
        ))}
      </div>
    </Panel>
  );
};

// ---------------- dashboard 4: biggest open orders ----------------
const TRANSPORT: Record<string, [string, string]> = {
  "delivered": ["Delivered", "good"], "in transit": ["In transit", "good"], "booked": ["Booked", "good"],
  "not booked": ["Not booked", ""], "unknown": ["Unknown", ""],
  "ex works": ["Ex works", ""], "hannover": ["Hannover · Dachser books", ""],
};

const biggest: PanelFn = ({ live }) => {
  const rows = live?.biggest;
  if (!rows) return <Panel title="Biggest open orders"><Missing /></Panel>;
  const s = live.biggest_summary;
  const note = live.dachser_tracking === "not subscribed" || live.dachser_tracking === "no key"
    ? " · transport unknown: Dachser tracking not enabled" : "";
  return (
    <div className="prodwrap">
      {s && <div className="callouts">
        <div className="callout2"><b>{money(s.top_value_dkk)} DKK</b><span>the {s.top_orders} biggest open orders</span></div>
        <div className="callout2"><b>{pct(s.top_share_pct)}</b><span>of all open order value ({money(s.open_value_dkk)} DKK, {s.open_orders} orders)</span></div>
        <div className="callout2"><b>{s.booked} of {s.top_orders}</b><span>booked with Dachser</span></div>
      </div>}
      <Panel title="Biggest open orders by revenue" note={"start date today or later" + note}>
        <table className="table">
          <thead><tr><th>Customer</th><th>Order</th><th>Customer order</th><th>Start</th><th>Delivery</th>
            <th className="num">Revenue DKK</th><th className="num">Picked</th><th>Status</th><th>Transport</th></tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => {
              const [label, cls] = TRANSPORT[r.transport] || ["Unknown", ""];
              return (
                <tr key={i}>
                  <td>{r.customer}</td><td>{r.order}</td><td className="po">{r.po}</td><td>{day(r.start)}</td>
                  <td>{r.delivery ? day(r.delivery) : "–"}</td>
                  <td className="num">{dkk(r.revenue_dkk)}</td><td className="num">{pct(r.pick_rate_pct)}</td>
                  <td>{r.status}</td>
                  <td className={r.book_now ? "bad" : cls}>{r.book_now ? "Book now"
                    : r.transport === "booked" && r.collection ? `Booked · collection ${fmtDayMonth(r.collection)}` : label}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
    </div>
  );
};

// ---------------- dashboard 5: production plan ----------------
const RUN_NOTE: Record<string, string> = { produced: "produced", "awaiting registration": "plan · awaiting registration" };

// One day column. All orders of a run arrive from the job; the column shows as many as fit:
// after each render, if it overflows, the run currently showing the most rows gives one up
// (it then gets "+ n more orders"). Resizing the window or new data starts again from all rows.
function ProdDay({ day: d, runs }: { day: string; runs: any[] }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const full = runs.map((r: any) => r.alloc.length);
  const sig = JSON.stringify(full) + "|" + runs.map((r: any) => r.sku + r.cases).join(",");
  const [shown, setShown] = React.useState<number[]>(full);
  const [size, setSize] = React.useState(0);
  const key = sig + "|" + size;
  const lastKey = React.useRef(key);
  React.useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let last = el.clientHeight;
    const ro = new ResizeObserver(() => { if (el.clientHeight !== last) { last = el.clientHeight; setSize((s) => s + 1); } });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  React.useLayoutEffect(() => {
    if (lastKey.current !== key) {                 // new data or new size: start again from all rows
      lastKey.current = key;
      setShown(full);
      return;
    }
    const el = ref.current;
    if (!el || el.scrollHeight <= el.clientHeight + 1) return;
    let i = -1;
    shown.forEach((n, k) => { if (n > 0 && (i < 0 || n > shown[i])) i = k; });
    if (i >= 0) setShown(shown.map((n, k) => (k === i ? n - 1 : n)));
  });
  return (
    <div className="pcol" ref={ref}>
      <h3>{fmtDayNum(d)}</h3>
      {runs.length === 0 && <div className="empty small">no production</div>}
      {runs.map((r: any, i: number) => {
        const n = Math.min(shown[i] ?? r.alloc.length, r.alloc.length);
        const hidden = r.alloc.length - n + (r.more || 0);
        return (
          <div className={"run" + (r.status === "produced" ? " produced" : "")} key={i}>
            <div className="runhead"><b>{r.flavour}</b>{r.lang && <span className="lang">{r.lang}</span>}
              <span className="sku">{fmtNum(r.cases)} cs</span></div>
            {r.status && r.status !== "planned" && (
              <div className="runstate">{RUN_NOTE[r.status] || r.status}
                {r.status === "produced" && r.planned_cases ? ` · plan ${fmtNum(r.planned_cases)}` : ""}
                {r.unplanned ? " · not in the plan" : ""}</div>)}
            {!r.sku && <div className="al nodata">Unknown article</div>}
            {r.alloc.slice(0, n).map((a: any, j: number) => (
              <div className={"al" + (a.picked ? " picked" : a.ready ? " ready" : "")} key={j}><span className="grow">{a.customer}</span>
                <span className="q">{fmtNum(a.cases)}{a.cases === a.of ? "" : ` of ${fmtNum(a.of)}`}</span>
                <span className="s">{a.picked ? "picked" : a.ready ? "pending picking" : day(a.start)}</span></div>
            ))}
            {hidden > 0 && <div className="al more2">+ {hidden} more orders</div>}
            {r.free > 0 && <div className="al free"><span className="grow">Not yet allocated</span>
              <span className="q">{fmtNum(r.free)}</span><span className="s" /></div>}
          </div>
        );
      })}
    </div>
  );
}

const productionLine = (name: string, p: any) => {
  const runs = (p.lines || {})[name] || [];
  return (
    <Panel title={name}>
      <div className="ptable" style={{ gridTemplateColumns: `repeat(${p.days.length}, minmax(0, 1fr))` }}>
        {p.days.map((d: string) => <ProdDay key={d} day={d} runs={runs.filter((r: any) => r.day === d)} />)}
      </div>
    </Panel>
  );
};

const productionPlan: PanelFn = ({ live }) => {
  const p = live?.production;
  if (!p) return <Panel title="Production plan"><Missing /></Panel>;
  const fri = new Date(Date.UTC(+p.monday.slice(0, 4), +p.monday.slice(5, 7) - 1, +p.monday.slice(8, 10) + 4))
    .toISOString().slice(0, 10);
  return (
    <div className="prodwrap">
      <div className="weekbanner">Week {p.week} · {fmtDayMonthLong(p.monday)} – {fmtDayMonthLong(fri)}</div>
      {productionLine("Laudenberg", p)}
      {productionLine("Bossar", p)}
    </div>
  );
};

// ---------------- rotation config ----------------
export type Dashboard = { id: string; title: string; tab: string; seconds: number; layout: string; columns: string;
                          rows: string; panels: Record<string, PanelFn> };

export const DASHBOARDS: Dashboard[] = [
  { id: "performance", tab: "Performance", title: "How are we doing", seconds: 300,
    layout: `"otif shorts" "reply credit"`, columns: "1fr 1fr", rows: "1fr 1fr",
    panels: { otif: otifPanel, shorts: aiPanel, reply: responsePanel, credit: creditPanel } },
  { id: "action", tab: "Action now", title: "What needs doing now", seconds: 300,
    layout: `"emails due weclapp" "emails credits dachser"`, columns: "1.1fr 1.35fr 0.95fr", rows: "1fr 1fr",
    panels: { emails: emailsPanel, due: duePanel, credits: creditsCheckPanel, weclapp: weclappPanel,
              dachser: dachserPickPanel } },
  { id: "status", tab: "Order status", title: "Open orders by status", seconds: 300,
    layout: `"board"`, columns: "1fr", rows: "1fr", panels: { board: statusBoard } },
  { id: "biggest", tab: "Biggest orders", title: "Biggest open orders", seconds: 300,
    layout: `"big"`, columns: "1fr", rows: "1fr", panels: { big: biggest } },
  { id: "production", tab: "Production", title: "Production plan", seconds: 300,
    layout: `"prod"`, columns: "1fr", rows: "1fr", panels: { prod: productionPlan } },
];
