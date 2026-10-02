"""
live.py - near-real-time data for dashboards 2, 3 and 4 (hourly).

Writes Redis key lk:live:
  status_board   dashboard 3: open orders by TraceLink status
  biggest        dashboard 4: biggest open orders by revenue
  due_not_ready  dashboard 2: due within ACTION_DAYS, not ready for warehouse
  emails_waiting dashboard 2: customer mails waiting > 1 business day
"""
import datetime as dt
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import common
import credit_notes_extract as cx
import otif_extract as ox
import response_time_extract as rx

DACHSER_API_KEY = os.environ.get("DACHSER_API_KEY", "PASTE_DACHSER_API_KEY_HERE")
DACHSER_BASE = "https://api-gateway.dachser.com"
DACHSER_CUSTOMER_ID = os.environ.get("DACHSER_CUSTOMER_ID", "47379200")

BOARD_AHEAD_DAYS = 14      # dashboard 3: start date within the next 14 days
BOARD_OVERDUE_DAYS = 60    # ...and not older than this (stale orders ignored)
SHIPPED_BACK_DAYS = 61     # "shipped, not closed": start date within the past two months
ACTION_DAYS = 3            # dashboard 2: due within 3 days
BIGGEST_ROWS = 20
EMAIL_LOOKBACK_DAYS = 14   # older unanswered mails are history, not today's to-do
EMAIL_WAIT_BH = 8          # more than 1 business day

NOT_READY = "Not ready for warehouse"
COLUMNS = [NOT_READY, "Partially ready for warehouse", "Ready for warehouse",
           "Ready to be shipped", "Shipped - not closed"]
# statuses that are folded into "Not ready" (sales orders only)
NOT_READY_ALIASES = {"not ready for warehouse", "pakning", "-- none --", "-- ingen --", ""}


def column_of(state):
    s = (state or "").strip()
    if s.lower() in NOT_READY_ALIASES:
        return NOT_READY
    if s == "Shipped":
        return "Shipped - not closed"
    return s if s in COLUMNS else None


def d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ----------------------------- sources -------------------------------------
class Dachser:
    """Track & trace lookup by order number. Booked = sent to Dachser via eLogistics."""

    def __init__(self):
        self.status = "ok"

    def booked(self, order_number):
        if self.status == "not subscribed":
            return "unknown"
        q = urllib.parse.urlencode({"tracking-number": order_number, "customer-id": DACHSER_CUSTOMER_ID})
        req = urllib.request.Request(f"{DACHSER_BASE}/rest/v2/shipmentstatus?{q}",
                                     headers={"X-API-Key": DACHSER_API_KEY, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read() or b"{}")
            time.sleep(0.1)
            if isinstance(body, dict):
                hits = body.get("shipments") if "shipments" in body else body
            else:
                hits = body
            return "booked" if hits else "not booked"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self.status = "not subscribed"
                return "unknown"
            if e.code in (404, 422):
                return "not booked"
            raise


def econ_collection(econ, path):
    url, out = f"{cx.BASE_URL}{path}{'&' if '?' in path else '?'}pagesize=1000", []
    while url:
        res = econ._get(url)
        out += res.get("collection", [])
        url = (res.get("pagination") or {}).get("nextPage")
    return out


def pick_rate(genobj):
    picked = sum(ox.fnum(g.get("unit_order_count_f")) for g in genobj)
    left = sum(ox.fnum(g.get("unit_order_reserved_f")) for g in genobj)
    total = picked + left
    return (round(100 * picked / total, 1) if total else None), left


# ----------------------------- builders ------------------------------------
def build_status_board(open_orders, names, today):
    board = {c: [] for c in COLUMNS}
    for o in open_orders:
        col = column_of(o["state"])
        start = d(o.get("start_date"))
        if col is None or start is None:
            continue
        if col == "Shipped - not closed":
            if start < today - dt.timedelta(days=SHIPPED_BACK_DAYS):
                continue
        elif not (today - dt.timedelta(days=BOARD_OVERDUE_DAYS) <= start
                  <= today + dt.timedelta(days=BOARD_AHEAD_DAYS)):
            continue
        board[col].append({"customer": names.get(str(o["customer_id"]), ""),
                           "order": o["number"], "po": (o.get("name") or "").strip(),
                           "start": start.isoformat(),
                           "overdue": col != "Shipped - not closed" and start < today})
    for c in board:
        board[c].sort(key=lambda x: (not x["overdue"], x["start"], x["customer"]))
    return [{"status": c, "orders": board[c]} for c in COLUMNS]


def build_biggest(open_orders, names, values, genobj_of, dachser, today):
    rows = []
    for o in open_orders:
        start = d(o.get("start_date"))
        if start is None or start < today:
            continue
        v = values.get(str(o["number"]))
        if v is None:
            continue
        rows.append((v, o, start))
    rows.sort(key=lambda x: -x[0])
    out = []
    for v, o, start in rows[:BIGGEST_ROWS]:
        rate, _ = pick_rate(genobj_of(o["order_id"]))
        out.append({"customer": names.get(str(o["customer_id"]), ""), "order": o["number"],
                    "po": (o.get("name") or "").strip(),
                    "start": start.isoformat(),
                    "delivery": d(o.get("deadline_date")).isoformat() if d(o.get("deadline_date")) else None,
                    "revenue_dkk": round(v),
                    "pick_rate_pct": rate, "transport": dachser.booked(o["number"]),
                    "status": column_of(o["state"]) or o["state"]})
    return out


def build_due_not_ready(open_orders, names, today):
    due = []
    horizon = today + dt.timedelta(days=ACTION_DAYS)
    for o in open_orders:
        start, col = d(o.get("start_date")), column_of(o["state"])
        if start is None or col not in (NOT_READY, "Partially ready for warehouse"):
            continue
        if start < today - dt.timedelta(days=BOARD_OVERDUE_DAYS) or start > horizon:
            continue
        due.append({"customer": names.get(str(o["customer_id"]), ""), "order": o["number"],
                    "po": (o.get("name") or "").strip(), "start": start.isoformat(),
                    "status": col, "overdue": start < today})
    due.sort(key=lambda x: x["start"])
    return due


def build_emails(rows):
    w = [r for r in rows if r["in_kpi"] and not r["answered"] and r["business_hours"] > EMAIL_WAIT_BH]
    w.sort(key=lambda r: -r["business_hours"])
    day = rx.BUSINESS_END - rx.BUSINESS_START
    return [{"customer": r["customer"], "domain": r["customer_domain"], "subject": r["subject"][:80],
             "company": r.get("owner_customer") or r["customer_domain"],
             "owner": r.get("owner") or "Unassigned",
             "received": r["received_local"], "waiting_days": round(r["business_hours"] / day, 1)}
            for r in w]


# ----------------------------- main ----------------------------------------
def main(tl=None, econ=None, graph=None, dachser=None, today=None):
    today = today or common.cph_now().date()
    tl, econ, dachser = tl or ox.TraceLink(), econ or cx.Economic(), dachser or Dachser()
    h = common.Health("live")
    payload = {"generated_at": dt.datetime.now(common.UTC).isoformat()}

    orders = h.section("tracelink orders", tl.list_orders,
                       {"locked": "=0", "order_group_id": f"={ox.SALES_GROUP}"}) or []
    open_orders = [o for o in orders if not o.get("delete_dt")
                   and str(o.get("customer_id")) not in common.INTERNAL_CUSTOMERS]

    names = h.section("e-conomic customers", lambda: {
        str(c["customerNumber"]): c.get("name", "") for c in econ_collection(econ, "/customers")}) or {}

    def values():
        v = {}
        for state in ("drafts", "sent"):
            for o in econ_collection(econ, f"/orders/{state}"):
                v[str(o.get("orderNumber"))] = float(o.get("netAmountInBaseCurrency") or 0)
        return v
    order_values = h.section("e-conomic order values", values) or {}

    cache = {}

    def genobj_of(order_id):
        if order_id not in cache:
            cache[order_id] = tl.module("genobj", order_id)
        return cache[order_id]

    if orders:
        payload["status_board"] = h.section("status board", build_status_board, open_orders, names, today)
        payload["biggest"] = h.section("biggest orders", build_biggest, open_orders, names,
                                       order_values, genobj_of, dachser, today)
        payload["due_not_ready"] = h.section("due not ready", build_due_not_ready, open_orders, names, today)

    def emails():
        rx.DATE_FROM = (today - dt.timedelta(days=EMAIL_LOOKBACK_DAYS)).isoformat()
        rx.DATE_TO = None
        rx.OUTPUT_DIR = tempfile.mkdtemp()
        rows, _ = rx.run(graph or rx.Graph())
        return build_emails(rows)
    payload["emails_waiting"] = h.section("emails", emails)

    payload = {k: v for k, v in payload.items() if v is not None}
    common.keep_previous(payload, "lk:live",
                         ["status_board", "biggest", "due_not_ready", "emails_waiting"])
    payload["dachser_tracking"] = dachser.status
    common.redis_set("lk:live", payload)
    return payload, h.publish({"dachser_tracking": dachser.status})


if __name__ == "__main__":
    _, health = main()
    print(health)
    raise SystemExit(0 if health["ok"] else 1)
