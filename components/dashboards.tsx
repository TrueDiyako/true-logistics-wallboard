import React from "react";
import { LineChart, HBar, Legend } from "./charts";

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
const dkk = (v: any) => (v == null ? "–" : `${Math.round(Number(v) / 1000).toLocaleString("da-DK")}k`);
const money = (v: any) => (v == null ? "–" : Math.abs(Number(v)) >= 1e6
  ? `${(Number(v) / 1e6).toLocaleString("en-GB", { maximumFractionDigits: 1 })}M` : dkk(v));
const day = (s: string) => {
  const d = new Date(s + "T12:00:00");
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
};

function Panel({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <section className="panel">
      <h2>{title}{note && <small>{note}</small>}</h2>
      <div className="panel-body">{children}</div>
    </section>
  );
}

function Missing() {
  return <div className="missing">Waiting for first data run</div>;
}

function Headlines({ items }: { items: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="headlines">
      {items.map((i) => (
        <div key={i.label}><b style={{ color: i.color }}>{i.value}</b><span>{i.label}</span></div>
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

const creditPanel: PanelFn = ({ kpi }) => {
  const c = kpi?.credit;
  if (!c) return <Panel title="Credit notes"><Missing /></Panel>;
  const w = c.weekly || [];
  return (
    <Panel title="Credit notes" note="% of invoices credited">
      <Headlines items={[{ label: `${c.headline.credits} of ${c.headline.invoices} invoices, 13 weeks`,
                           value: pct(c.headline.credit_pct), color: C.credit }]} />
      <LineChart labels={w.map((x: any) => x.week)} series={[{ name: "Credited", values: w.map((x: any) => x.pct), color: C.credit, width: 7 }]} />
    </Panel>
  );
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

const duePanel: PanelFn = ({ live }) => {
  const rows = live?.due_not_ready;
  if (!rows) return <Panel title="Due in 3 days, not ready"><Missing /></Panel>;
  const today = new Date().toISOString().slice(0, 10);
  const over = rows.filter((r: any) => r.overdue).length;
  const todayN = rows.filter((r: any) => !r.overdue && r.start === today).length;
  const sorted = [...rows].sort((a: any, b: any) => (b.days_late || 0) - (a.days_late || 0) || (a.start < b.start ? -1 : 1));
  return (
    <Panel title="Due in 3 days, not ready" note="oldest first">
      <div className="chips"><span className="chip bad">Overdue <b>{over}</b></span>
        <span className="chip">Due today <b>{todayN}</b></span>
        <span className="chip">Next 3 days <b>{rows.length - over - todayN}</b></span></div>
      <List rows={sorted} max={9} empty="Everything due is ready"
            render={(r) => (<><span className="grow">{r.customer} · {r.order}{r.po ? ` · ${r.po}` : ""}</span>
                              <span className="muted2">{r.status === "Partially ready for warehouse" ? "Partially ready" : "Not ready"}</span>
                              {r.overdue ? <span className="bad">{r.days_late} d late</span>
                                         : <span className="warn">{r.start === today ? "today" : day(r.start)}</span>}</>)} />
    </Panel>
  );
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
  if (!w) return <Panel title="weclapp orders not in TraceLink"><Missing /></Panel>;
  const since = new Date(w.tracking_from + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return (
    <Panel title="weclapp orders not in TraceLink" note={`created from ${since}`}>
      <Headlines items={[{ label: `of ${w.orders} weclapp orders${w.no_po ? ` · ${w.no_po} without PO` : ""}`,
                           value: String(w.missing_count), color: w.missing_count ? C.noReply : C.reply }]} />
      <List rows={w.missing} max={11} empty="Every weclapp order is in TraceLink"
            render={(r) => (<><span className="grow"><b>{r.customer}</b>
                                <span className="sub">PO {r.po || "missing"} · weclapp #{r.weclapp}</span></span>
                              <span className={r.age_days >= 2 ? "bad" : "warn"}>{r.age_days === 0 ? "today" : `${r.age_days} d`}</span></>)} />
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
                  <td className={r.book_now ? "bad" : cls}>{r.book_now ? "Book now" : label}</td>
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
const productionLine = (name: string, p: any) => {
  const runs = (p.lines || {})[name] || [];
  return (
    <Panel title={name}>
      <div className="ptable" style={{ gridTemplateColumns: `repeat(${p.days.length}, minmax(0, 1fr))` }}>
        {p.days.map((d: string) => {
          const today = runs.filter((r: any) => r.day === d);
          return (
            <div className="pcol" key={d}>
              <h3>{new Date(d + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "numeric" })}</h3>
              {today.length === 0 && <div className="empty small">no production</div>}
              {today.map((r: any, i: number) => (
                <div className="run" key={i}>
                  <div className="runhead"><b>{r.flavour}</b><span className="lang">{r.lang}</span>
                    <span className="sku">{r.cases.toLocaleString("da-DK")} cs</span></div>
                  {!r.sku && <div className="al nodata">Unknown article</div>}
                  {r.alloc.map((a: any, j: number) => (
                    <div className="al" key={j}><span className="grow">{a.customer}</span>
                      <span className="q">{a.cases.toLocaleString("da-DK")}{a.cases === a.of ? "" : ` of ${a.of.toLocaleString("da-DK")}`}</span>
                      <span className="s">{day(a.start)}</span></div>
                  ))}
                  {r.more > 0 && <div className="al more2">+ {r.more} more orders</div>}
                  {r.free > 0 && <div className="al free"><span className="grow">Not yet allocated</span>
                    <span className="q">{r.free.toLocaleString("da-DK")}</span><span className="s" /></div>}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
};

const productionPlan: PanelFn = ({ live }) => {
  const p = live?.production;
  if (!p) return <Panel title="Production plan"><Missing /></Panel>;
  const mon = new Date(p.monday + "T12:00:00"), fri = new Date(mon.getTime() + 4 * 864e5);
  const f = (x: Date) => x.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
  return (
    <div className="prodwrap">
      <div className="weekbanner">Week {p.week} · {f(mon)} – {f(fri)} · new production only, cases (bags ÷ 12) allocated to open reservations by start date</div>
      {productionLine("Laudenberg", p)}
      {productionLine("Bossar", p)}
    </div>
  );
};

// ---------------- rotation config ----------------
export type Dashboard = { id: string; title: string; seconds: number; layout: string; columns: string;
                          rows: string; panels: Record<string, PanelFn> };

export const DASHBOARDS: Dashboard[] = [
  { id: "performance", title: "How are we doing", seconds: 300,
    layout: `"otif shorts" "reply credit"`, columns: "1fr 1fr", rows: "1fr 1fr",
    panels: { otif: otifPanel, shorts: shortsPanel, reply: responsePanel, credit: creditPanel } },
  { id: "action", title: "What needs doing now", seconds: 300,
    layout: `"emails due customers" "emails credits customers"`, columns: "1.1fr 1.35fr 0.95fr", rows: "1fr 1fr",
    panels: { emails: emailsPanel, due: duePanel, credits: creditsCheckPanel, customers: weclappPanel } },
  { id: "status", title: "Open orders by status", seconds: 300,
    layout: `"board"`, columns: "1fr", rows: "1fr", panels: { board: statusBoard } },
  { id: "biggest", title: "Biggest open orders", seconds: 300,
    layout: `"big"`, columns: "1fr", rows: "1fr", panels: { big: biggest } },
  { id: "production", title: "Production plan", seconds: 300,
    layout: `"prod"`, columns: "1fr", rows: "1fr", panels: { prod: productionPlan } },
];
