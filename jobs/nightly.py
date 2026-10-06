"""
nightly.py - KPI history for dashboards 1 and 2 (runs once a night).

Writes Redis key lk:kpi:
  otif      weekly OTIF / on-time / in-full %, 13-week headlines
  shorts    weekly average % of an order's volume short-picked, % orders with a short line
  response  within-1-day trend, last-4-weeks answer bands, no-reply root cause
  credit    weekly % of invoices credited, headline, credits to check
  customers order fill rate + best/worst 10 OTIF among the busiest customers
"""
import datetime as dt
import os
import re
import tempfile

import common
import credit_notes_extract as cx
import otif_extract as ox
import response_time_extract as rx

WINDOW_DAYS = 91           # 13 weeks
RANK_POOL = 30             # rank within the 30 customers with most orders
RANK_TOP = 10


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def complete_weeks(rows, today, key="week", n=13):
    """Graphs end at the last COMPLETE week: the running week would show
    orders not yet due and mails not yet answered as failures."""
    current = iso_week(today)
    return sorted((r for r in rows if r[key] < current), key=lambda r: r[key])[-n:]


def pct(n, d):
    return round(100 * n / d, 1) if d else None


# ----------------------------- OTIF / short picks --------------------------
def otif_payload(orders, today=None):
    timed = [o for o in orders if o["on_time"] is not None]
    filled = [o for o in orders if ox._filled_base(o)]
    weekly = ox.weekly_series(orders)
    if today:
        weekly = complete_weeks(weekly, today)
    head = {
        "otif_pct": pct(sum(o["otif"] is True for o in timed), len(timed)),
        "on_time_pct": pct(sum(o["on_time"] is True for o in timed), len(timed)),
        "in_full_pct": pct(sum(o["in_full"] is True for o in filled), len(filled)),
        "orders": len(orders),
    }
    return {"headline": head,
            "weekly": [{k: w[k] for k in ("week", "otif_pct", "on_time_pct", "in_full_pct", "orders")}
                       for w in weekly]}


def shorts_payload(orders, today=None):
    """Short picks by volume: for each shipped order, the share of its ordered cases not
    picked (judged per flavour, swaps and bag/case differences already handled); the
    weekly line is the average over that week's orders."""
    shipped = [o for o in orders if ox._filled_base(o) and o["ordered_units"] > 0]

    def short_pct(o):
        return 100 * (1 - min(o["filled_units"], o["ordered_units"]) / o["ordered_units"])
    wk = {}
    for o in shipped:
        w = iso_week(o.get("last_dispatch") or o["requested_date"])
        wk.setdefault(w, []).append(short_pct(o))
    weekly = [{"week": k, "pct": round(sum(v) / len(v), 1), "orders": len(v)} for k, v in sorted(wk.items())]
    if today:
        weekly = complete_weeks(weekly, today)
    return {"avg_short_pct": round(sum(map(short_pct, shipped)) / len(shipped), 1) if shipped else None,
            "orders_with_short_pct": pct(sum(1 for o in shipped if short_pct(o) > 0), len(shipped)),
            "orders": len(shipped), "weekly": weekly}


def customers_payload(orders):
    rank = ox.customer_ranking(orders)
    pool = sorted((r for r in rank if r["otif_pct"] is not None),
                  key=lambda r: -r["orders"])[:RANK_POOL]
    best = sorted(pool, key=lambda r: (-r["otif_pct"], -r["orders"]))[:RANK_TOP]
    worst = sorted(pool, key=lambda r: (r["otif_pct"], -r["orders"]))[:RANK_TOP]
    filled = [o for o in orders if ox._filled_base(o)]
    ou = sum(o["ordered_units"] for o in filled)
    fu = sum(o["filled_units"] for o in filled)
    slim = lambda r: {"customer": r["customer"], "orders": r["orders"], "otif_pct": r["otif_pct"],
                      "fill_pct": r["order_fill_rate_pct"]}
    return {"order_fill_rate_pct": pct(fu, ou), "pool": len(pool),
            "best": [slim(r) for r in best], "worst": [slim(r) for r in worst]}


# ----------------------------- response time -------------------------------
def response_payload(rows, today=None, tracelink_refs=None):
    """Headline + weekly 'answered within 1 day' trend, the last 4 complete weeks
    split into answer-time bands, and why mails got no reply."""
    kpi = [r for r in rows if r["in_kpi"]]
    weekly = rx.distribution(kpi, lambda r: rx.iso_week_of(r["received_local"]))
    if today:
        weekly = complete_weeks(weekly, today, key="period")
    last4_weeks = {w["period"] for w in weekly[-4:]}
    last4 = rx.distribution([r for r in kpi if rx.iso_week_of(r["received_local"]) in last4_weeks],
                            lambda r: "last4")
    total = rx.distribution(kpi, lambda r: "all")
    head = total[0] if total else {}
    refs = tracelink_refs or set()
    unanswered = [r for r in kpi if not r["answered"]]
    processed = [r for r in unanswered if set(re.findall(r"\d{4,}", r["subject"] or "")) & refs]
    answered = [r for r in kpi if r["answered"]]
    outside = [r for r in answered if r.get("without_order_copy")]
    by_person = {}
    for r in outside:
        p = r["replied_by"].split("@")[0].capitalize()
        by_person[p] = by_person.get(p, 0) + 1
    return {"bands": rx.BUCKET_ORDER,
            "outside_copy": {"count": len(outside), "answered": len(answered),
                             "pct": pct(len(outside), len(answered)),
                             "by_person": sorted(by_person.items(), key=lambda x: -x[1])},
            "headline": {"within_1_day_pct": head.get("within 1 day %"),
                         "no_reply_pct": head.get("no reply %"), "waits": head.get("waits", 0)},
            "trend": [{"week": w["period"], "pct": w["within 1 day %"], "waits": w["waits"]} for w in weekly],
            "last4": ({b: last4[0][b + " %"] for b in rx.BUCKET_ORDER} | {"waits": last4[0]["waits"]}) if last4 else None,
            "no_reply": {"count": len(unanswered),
                         "order_in_tracelink": len(processed),
                         "order_in_tracelink_pct": pct(len(processed), len(unanswered))}}


# ----------------------------- credit notes --------------------------------
def credit_payload(weekly, rows, today=None):
    scope = [r for r in rows if not r["internal"]]
    inv = sum(w["invoices"] for w in weekly)          # headline: the whole window
    cr = sum(w["credits"] for w in weekly)
    if today:
        weekly = complete_weeks(weekly, today, key="period")
    by_chain = {}
    for r in scope:
        if r["over_credited"]:
            g = by_chain.setdefault(r.get("chain") or r["reverses_invoice"], {
                "customer": r["customer"], "date": r["date"], "chain": r.get("chain", ""),
                "reverses_invoice": r["reverses_invoice"], "net": r.get("chain_net"),
                "amount_dkk": 0.0, "credit_notes": []})
            g["amount_dkk"] += r["amount_dkk"]
            g["credit_notes"].append(r["credit_note"])
            g["date"] = max(g["date"], r["date"])
    to_check = [{**g, "reason": "credited more than invoiced"} for g in by_chain.values()]
    return {"headline": {"credit_pct": pct(cr, inv), "credits": cr, "invoices": inv},
            "weekly": [{"week": w["period"], "pct": w["credit_pct_count"], "credits": w["credits"],
                        "invoices": w["invoices"]} for w in weekly],
            "to_check": sorted(to_check, key=lambda x: x["amount_dkk"])}


# ----------------------------- main ----------------------------------------
def main(tl=None, graph=None, econ=None, today=None):
    today = today or common.cph_now().date()
    since = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    work = tempfile.mkdtemp()
    h = common.Health("nightly")
    payload = {"generated_at": dt.datetime.now(common.UTC).isoformat(), "window_from": since}
    refs = set()

    def otif():
        ox.DATE_FROM, ox.DATE_TO = since, None
        ox.OUTPUT_DIR = os.path.join(work, "otif")
        ox.CACHE_DIR = os.environ.get("OTIF_CACHE_DIR", os.path.join(work, "otif_cache"))
        orders, _, _ = ox.run(tl or ox.TraceLink(), today=today)
        payload["otif"] = otif_payload(orders, today)
        payload["shorts"] = shorts_payload(orders, today)
        payload["customers"] = customers_payload(orders)
        for o in orders:                            # order refs to explain "no reply"
            refs.update(re.findall(r"\d{4,}", str(o.get("name", ""))))
            refs.add(str(o["order_number"]))

    def response():
        rx.DATE_FROM, rx.DATE_TO = since, None
        rx.OUTPUT_DIR = os.path.join(work, "resp")
        rows, _ = rx.run(graph or rx.Graph())
        payload["response"] = response_payload(rows, today, refs)

    def credit():
        cx.DATE_FROM, cx.DATE_TO = since, None
        cx.INCLUDE_INTERNAL_IN_HEADLINE = False
        cx.OUTPUT_DIR = os.path.join(work, "credit")
        _, weekly, rows = cx.run(econ or cx.Economic(), today=today)
        payload["credit"] = credit_payload(weekly, rows, today)

    h.section("otif", otif)
    h.section("response", response)
    h.section("credit", credit)
    common.keep_previous(payload, "lk:kpi", ["otif", "shorts", "customers", "response", "credit"])
    common.redis_set("lk:kpi", payload)
    return payload, h.publish({"graph_secret_expires": os.environ.get("GRAPH_SECRET_EXPIRES", "")})


if __name__ == "__main__":
    _, health = main()
    print(health)
    raise SystemExit(0 if health["ok"] else 1)
