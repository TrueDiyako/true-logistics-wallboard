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
OUTSIDE_COPY_FROM = "2026-10-12"   # week 42: from here on order@ must be in copy


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
    # "answered without order@ in copy": clean slate from week 42 (team told to copy order@)
    outside_scope = [r for r in answered if str(r.get("received_local", ""))[:10] >= OUTSIDE_COPY_FROM]
    outside = [r for r in outside_scope if r.get("without_order_copy")]
    by_person = {}
    for r in outside:
        p = r["replied_by"].split("@")[0].capitalize()
        by_person[p] = by_person.get(p, 0) + 1
    return {"bands": rx.BUCKET_ORDER,
            "outside_copy": {"count": len(outside), "answered": len(outside_scope), "from": OUTSIDE_COPY_FROM,
                             "pct": pct(len(outside), len(outside_scope)),
                             "by_person": sorted(by_person.items(), key=lambda x: -x[1])},
            "headline": {"within_1_day_pct": head.get("within 1 day %"),
                         "no_reply_pct": head.get("no reply %"), "waits": head.get("waits", 0)},
            "trend": [{"week": w["period"], "pct": w["within 1 day %"], "waits": w["waits"]} for w in weekly],
            "last4": ({b: last4[0][b + " %"] for b in rx.BUCKET_ORDER} | {"waits": last4[0]["waits"]}) if last4 else None,
            "no_reply": {"count": len(unanswered),
                         "order_in_tracelink": len(processed),
                         "order_in_tracelink_pct": pct(len(processed), len(unanswered))}}


# ----------------------------- credit notes --------------------------------
LOGISTICS_OWNERS = ["Valentina", "Frederikke", "Andreas"]
CLOSEOUT_FROM = "2026-10-05"     # week 41: order-closeout skill share counts credit notes from here


def customer_owners(econ):
    """{e-conomic customer number: logistics owner}, via the customer's invoice e-mail domain and
    jobs/customer_owners.csv (the same list as the e-mail panel)."""
    url, out = "https://restapi.e-conomic.com/customers?pagesize=1000", {}
    while url:
        res = econ._get(url)
        for c in res.get("collection", []):
            owner = rx.owner_of(str(c.get("email") or "").strip().lower()).get("responsible", "")
            if owner:
                out[str(c["customerNumber"])] = owner
        url = (res.get("pagination") or {}).get("nextPage")
    return out


OWNER_ROLLING_WEEKS = 4         # each point = credits / invoices over the last 4 weeks (smooths small counts)


def owner_weekly(window, rows, owners, weeks):
    """Per logistics owner and week: credit notes on their customers / invoices to their customers,
    summed over the last OWNER_ROLLING_WEEKS weeks (a week alone often has only 3-8 invoices)."""
    inv, cr = {}, {}
    for i in window:
        if float(i.get("netAmount", 0)) <= 0:
            continue
        c = str((i.get("customer") or {}).get("customerNumber", ""))
        if c in cx.INTERNAL_CUSTOMERS or c not in owners:
            continue
        k = (owners[c], iso_week(dt.date.fromisoformat(i["date"][:10])))
        inv[k] = inv.get(k, 0) + 1
    for r in rows:
        c = str(r.get("customer_number", ""))
        if r.get("internal") or c not in owners:
            continue
        k = (owners[c], iso_week(dt.date.fromisoformat(str(r["date"])[:10])))
        cr[k] = cr.get(k, 0) + 1
    def back(w, n):
        y, k = int(w[:4]), int(w[-2:])
        monday = dt.date.fromisocalendar(y, k, 1) - dt.timedelta(weeks=n)
        return iso_week(monday)
    out = {}
    for o in LOGISTICS_OWNERS:
        pts = []
        for w in weeks:
            span = [back(w, n) for n in range(OWNER_ROLLING_WEEKS)]
            c = sum(cr.get((o, x), 0) for x in span)
            i = sum(inv.get((o, x), 0) for x in span)
            pts.append({"week": w, "credits": c, "invoices": i, "pct": pct(c, i)})
        out[o] = pts
    return out


def closeout_share(rows, closeout_orders, po_to_order, since=CLOSEOUT_FROM):
    """Of the credit notes since `since`: how many concern an order that was NOT closed out
    with the order-closeout skill (100% = none of them were)."""
    scope = [r for r in rows if not r.get("internal") and str(r["date"])[:10] >= since]
    out, unknown = [], 0
    for r in scope:
        orders = {int(o) for o in r.get("orders") or []}
        po = str(r.get("heading") or "").strip().lower()
        if po in po_to_order:
            orders.add(po_to_order[po])
        if not orders:
            unknown += 1
        out.append({"credit_note": r["credit_note"], "orders": sorted(orders),
                    "closeout": bool(orders & closeout_orders)})
    n = len(out)
    not_used = sum(1 for x in out if not x["closeout"])
    return {"since": since, "credit_notes": n, "without_closeout": not_used, "no_order_found": unknown,
            "pct_without_closeout": pct(not_used, n), "detail": out}


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


def closeout_orders():
    """TraceLink order numbers the order-closeout skill was used on (Claude logs on Drive)."""
    import skill_usage
    drive = skill_usage.Drive()
    if not drive.connected:
        return set()
    out = set()
    for f in drive.list_folder(skill_usage.FOLDER_ID, dt.date(2026, 10, 1)):
        m = skill_usage._NAME.match(f.get("name") or "")
        if m and m["skill"] == "tracelink-order-closeout" and (m["ref"] or "").strip().isdigit():
            out.add(int(m["ref"].strip()))
    return out


def po_orders(tl):
    """TraceLink order name (customer PO) -> order number, for credit notes without an order reference."""
    tl = tl or ox.TraceLink()
    since = (dt.date.today() - dt.timedelta(days=240)).isoformat()
    out = {}
    for o in tl.list_orders({"order_group_id": f"={ox.SALES_GROUP}", "start_date": f">{since}"}):
        name = str(o.get("name") or "").strip().lower()
        if name and not o.get("delete_dt"):
            out.setdefault(name, int(o["number"]))
    return out


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
        api = econ or cx.Economic()
        _, weekly, rows = cx.run(api, today=today)
        c = credit_payload(weekly, rows, today)
        weeks = [w["week"] for w in c["weekly"]]
        try:
            c["by_owner"] = owner_weekly(cx.LAST_WINDOW, rows, customer_owners(api), weeks)
        except Exception as e:                               # the total line still shows
            print(f"credit by owner failed: {e}", flush=True)
        try:
            c["closeout"] = closeout_share(rows, closeout_orders(), po_orders(tl))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"closeout share failed: {type(e).__name__}: {e}", flush=True)
            c["closeout_error"] = f"{type(e).__name__}: {e}"[:200]
        payload["credit"] = c

    h.section("otif", otif)
    h.section("response", response)
    h.section("credit", credit)
    common.keep_previous(payload, "lk:kpi", ["otif", "shorts", "customers", "response", "credit"])
    common.redis_set("lk:kpi", payload)
    return payload, h.publish({"graph_secret_expires": os.environ.get("GRAPH_SECRET_EXPIRES", "")})


def already_done_today():
    """True when tonight's run already published successfully (backup schedule skips)."""
    rec = common.redis_get("lk:health:nightly") or {}
    if not rec.get("ok") or not rec.get("at"):
        return False
    at = dt.datetime.fromisoformat(rec["at"])
    return common.to_cph(at.astimezone(common.UTC)).date() == common.cph_now().date()


if __name__ == "__main__":
    import sys
    if "--skip-if-done" in sys.argv and already_done_today():
        print("Nightly KPIs already published today - backup run skipped.")
        raise SystemExit(0)
    _, health = main()
    print(health)
    raise SystemExit(0 if health["ok"] else 1)
