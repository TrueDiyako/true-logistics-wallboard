"""
live.py - near-real-time data for dashboards 2, 3 and 4 (hourly).

Writes Redis key lk:live:
  status_board   dashboard 3: open orders by TraceLink status
  biggest        dashboard 4: biggest open orders by revenue
  due_not_ready  dashboard 2: due within ACTION_DAYS, not ready for warehouse
  emails_waiting dashboard 2: customer mails waiting > 1 business day
  production     dashboard 5: this week's production plan and what it covers
  weclapp        dashboard 2: weclapp orders (from 6 Oct) with no TraceLink order
  dachser_pick   dashboard 2: picked by Dachser (delivery note) but not picked in TraceLink
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
import dachser_notes
import production
import skill_usage
import weclapp_check
import response_time_extract as rx

# Track & Trace: one key per Dachser customer number (login). Secrets DACHSER_TT_<customer no.>;
# the older DACHSER_API_KEY / _DK / _DK2 / _DK3 secrets still work (customer number unknown).
DACHSER_ACCOUNTS = {"47379200": "Hannover (Dachser-run warehouse)", "72695099": "Denmark - TRUE. APS, Glostrup",
                    "47133021": "Denmark", "47222882": "Denmark"}


def _dachser_keys():
    out, seen = [], set()
    for cust in DACHSER_ACCOUNTS:
        k = os.environ.get(f"DACHSER_TT_{cust}", "").strip()
        if k and k.upper() != "PENDING" and k not in seen:
            out.append((cust, k)); seen.add(k)
    for var in ("DACHSER_API_KEY", "DACHSER_API_KEY_DK", "DACHSER_API_KEY_DK2", "DACHSER_API_KEY_DK3"):
        k = os.environ.get(var, "").strip()
        if k and k.upper() != "PENDING" and k not in seen:
            out.append(("", k)); seen.add(k)
    return out


DACHSER_KEYS = _dachser_keys()
DACHSER_BASE = "https://api-gateway.dachser.com"
# Track & Trace comes as two subscriptions with the same request and answer (history first)
DACHSER_ENDPOINTS = ["shipmenthistory", "shipmentstatus"]
DACHSER_MATCH_WINDOW = (-21, 14)   # shipment date vs order start date, so a generic PO can't match
# Red "book it" warning for Glostrup orders not booked 2 working days before start.
# Off until every Danish Dachser account is visible to the keys (otherwise false alarms).
BOOKING_WARNING = os.environ.get("BOOKING_WARNING", "1") != "0"     # on; "0" switches it off
BOOK_BY_WORKDAYS = 2          # general rule: booked at least this many working days before start
# Customers that collect themselves: no booking by us, no warning ({customer number: name})
EX_WORKS = {"1308": "Real Food Distributors Pty Ltd.", "1332": "A1 d.o.o."}
# Customers that need booking earlier than the general rule ({customer number: working days})
BOOK_AHEAD_WORKDAYS = {}

BOARD_AHEAD_DAYS = 14      # dashboard 3: start date within the next 14 days
BOARD_OVERDUE_DAYS = 60    # ...and not older than this (stale orders ignored)
SHIPPED_BACK_DAYS = 61     # "shipped, not closed": start date within the past two months
ACTION_DAYS = 3            # dashboard 2: due within 3 days
BIGGEST_ROWS = 40              # sent to the page, which shows as many as fit on the screen
BIGGEST_WINDOW_DAYS = 14       # biggest orders starting within the next 2 weeks
EMAIL_LOOKBACK_DAYS = 14   # older unanswered mails are history, not today's to-do
EMAIL_WAIT_BH = 8          # more than 1 business day
DACHSER_EVERY_MIN = int(os.environ.get("DACHSER_EVERY_MIN", "30") or 30)   # Dachser delivery-note check
EMAILS_FROM = "2026-10-12"  # clean slate: logistics copy order@ from week 42 on; older waits are dropped

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
def workdays_until(today, day):
    n, cur = 0, today
    while cur < day:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


class TransportNoAnswer(Exception):
    """Dachser Track & Trace didn't answer after the retries."""


class Dachser:
    """Booking state of an order in Dachser Track & Trace. Glostrup bookings carry the
    TraceLink order number, Hannover bookings the customer order (PO) - both are tried,
    with every key. States: not booked / booked / in transit / delivered / unknown."""

    def __init__(self, keys=None):
        # keys: [(customer number, key)]; a bare key string = customer number unknown
        keys = DACHSER_KEYS if keys is None else keys
        self.keys = [{"customer": k[0], "key": k[1]} if isinstance(k, tuple) else {"customer": "", "key": k}
                     for k in keys]
        for k in self.keys:
            k.update(endpoints=list(DACHSER_ENDPOINTS), ok=True)
        self.status = "ok" if self.keys else "no key"
        self.found_by = {}          # order number -> customer number of the account that found it
        self.collection = {}        # order number -> planned collection date (Dachser shipmentDate)
        self.no_answer = 0

    def _get(self, k, ref):
        params = {"tracking-number": ref}
        if k["customer"]:
            params["customer-id"] = k["customer"]
        tries = 0
        while k["endpoints"]:
            req = urllib.request.Request(f"{DACHSER_BASE}/rest/v2/{k['endpoints'][0]}?{urllib.parse.urlencode(params)}",
                                         headers={"X-API-Key": k["key"], "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    time.sleep(0.25)             # Dachser: max 5 calls/second
                    return json.loads(r.read() or b"{}").get("shipments") or []
            except urllib.error.HTTPError as e:
                time.sleep(0.25)
                if e.code in (401, 403):
                    k["endpoints"].pop(0)        # try the other subscription, then give up on this key
                    continue
                if e.code in (400, 404, 422):
                    return []
                if e.code < 500 or tries >= 2:
                    raise TransportNoAnswer(f"HTTP {e.code}")
            except (TimeoutError, OSError, ValueError) as e:     # timeout / connection reset / cut-off
                if tries >= 2:
                    raise TransportNoAnswer(f"{type(e).__name__}: {e}")
            tries += 1
            time.sleep(2 * tries)
        k["ok"] = False
        return []

    @staticmethod
    def cancelled(ship):
        return any((e.get("event") or {}).get("code") == "K"
                   and "cancel" in str((e.get("event") or {}).get("description", "")).lower()
                   for e in ship.get("status") or [])

    @staticmethod
    def state_of(ship):
        """From all events, the furthest one: delivered > in transit > booked. 'K' events are
        information only (customs, cancellation) and '0' means no scan yet - both = booked."""
        codes = {str((e.get("event") or {}).get("code") or "0") for e in ship.get("status") or []}
        if "Z" in codes:
            return "delivered"
        if codes - {"0", "K", ""}:
            return "in transit"
        return "booked"

    def booked(self, order_number, po="", start=None):
        try:
            return self._booked(order_number, po, start)
        except TransportNoAnswer:
            self.no_answer += 1                  # this order shows "unknown"; the rest carries on
            return "unknown"

    def _booked(self, order_number, po="", start=None):
        live = [k for k in self.keys if k["ok"]]
        if not live:
            self.status = "not subscribed" if self.keys else "no key"
            return "unknown"
        for ref in [str(order_number), (po or "").strip()]:
            if not ref:
                continue
            for k in live:
                ships = self._get(k, ref)
                if start:
                    ships = [s for s in ships if d(s.get("shipmentDate")) and DACHSER_MATCH_WINDOW[0]
                             <= (d(s["shipmentDate"]) - start).days <= DACHSER_MATCH_WINDOW[1]]
                ships = [s for s in ships if not self.cancelled(s)]   # rebooked orders: the cancelled one doesn't count
                if ships:
                    order = ["delivered", "in transit", "booked"]
                    best = min(ships, key=lambda s: order.index(self.state_of(s)))
                    self.found_by[str(order_number)] = k["customer"]
                    self.collection[str(order_number)] = str(best.get("shipmentDate") or "")[:10]
                    return self.state_of(best)
        if not any(k["ok"] for k in self.keys):
            self.status = "not subscribed"
            return "unknown"
        return "not booked"


def hannover_order(po):
    """True when Dachser's Hannover warehouse has a delivery order for this PO - Dachser books
    those shipments itself, so no 'Book now'. Uses the warehouse status key (DACHSER_STATUS_KEY)."""
    import dachser_notes
    if not po or not dachser_notes.DACHSER_STATUS_KEY:
        return False
    try:
        code, body = dachser_notes.dachser("/rest/v2/deliveryorderstatus", po)
    except dachser_notes.NoAnswer:
        return False
    return code == 200 and bool(body.get("deliveryOrders"))


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
        if start is None or start < today or start > today + dt.timedelta(days=BIGGEST_WINDOW_DAYS):
            continue
        v = values.get(str(o["number"]))
        if v is None:
            continue
        rows.append((v, o, start))
    rows.sort(key=lambda x: -x[0])
    out = []
    for v, o, start in rows[:BIGGEST_ROWS]:
        rate, _ = pick_rate(genobj_of(o["order_id"]))
        cust = str(o["customer_id"])
        transport = "ex works" if cust in EX_WORKS else dachser.booked(o["number"], o.get("name"), start)
        due = workdays_until(today, start) <= BOOK_AHEAD_WORKDAYS.get(cust, BOOK_BY_WORKDAYS)
        if transport == "not booked" and BOOKING_WARNING and due and hannover_order((o.get("name") or "").strip()):
            transport = "hannover"                                   # Dachser books it on shipping day
        out.append({"customer": names.get(str(o["customer_id"]), ""), "order": o["number"],
                    "po": (o.get("name") or "").strip(),
                    "start": start.isoformat(),
                    "delivery": d(o.get("deadline_date")).isoformat() if d(o.get("deadline_date")) else None,
                    "revenue_dkk": round(v),
                    "pick_rate_pct": rate, "transport": transport,
                    "transport_account": getattr(dachser, "found_by", {}).get(str(o["number"]), ""),
                    "collection": getattr(dachser, "collection", {}).get(str(o["number"]), ""),
                    "book_now": BOOKING_WARNING and transport == "not booked" and due,
                    "status": column_of(o["state"]) or o["state"]})
    return out


def biggest_summary(open_orders, values, biggest, today):
    total = [values[str(o["number"])] for o in open_orders
             if d(o.get("start_date")) and d(o.get("start_date")) >= today and str(o["number"]) in values]
    top = sum(r["revenue_dkk"] for r in biggest)
    return {"top_value_dkk": round(top), "top_orders": len(biggest), "open_value_dkk": round(sum(total)),
            "open_orders": len(total), "top_share_pct": round(100 * top / sum(total), 1) if total else None,
            "booked": sum(1 for r in biggest if r["transport"] in ("booked", "in transit", "delivered"))}


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
                    "status": col, "overdue": start < today, "days_late": max(0, (today - start).days)})
    due.sort(key=lambda x: x["start"])
    return due


def build_emails(rows):
    w = [r for r in rows if r["in_kpi"] and not r["answered"] and r["business_hours"] > EMAIL_WAIT_BH
         and str(r["received_local"])[:10] >= EMAILS_FROM]
    w.sort(key=lambda r: -r["business_hours"])
    day = rx.BUSINESS_END - rx.BUSINESS_START
    return [{"customer": r["customer"], "domain": r["customer_domain"], "subject": r["subject"][:80],
             "company": r.get("owner_customer") or r["customer_domain"],
             "owner": r.get("owner") or "Unassigned",
             "received": r["received_local"], "waiting_days": round(r["business_hours"] / day, 1)}
            for r in w]


# ----------------------------- main ----------------------------------------
def main(tl=None, econ=None, graph=None, dachser=None, today=None, plan_source=None, weclapp=None, drive=None,
         force_dachser=False):
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
        if payload.get("biggest") is not None:
            payload["biggest_summary"] = h.section("biggest summary", biggest_summary, open_orders,
                                                   order_values, payload["biggest"], today)
        payload["due_not_ready"] = h.section("due not ready", build_due_not_ready, open_orders, names, today)

    def emails():
        rx.DATE_FROM = (today - dt.timedelta(days=EMAIL_LOOKBACK_DAYS)).isoformat()
        rx.DATE_TO = None
        rx.OUTPUT_DIR = tempfile.mkdtemp()
        rx.MAX_BODY_READS = 80      # hourly run: keep it short (the nightly run reads up to 400)
        rows, _ = rx.run(graph or rx.Graph())
        return build_emails(rows)
    payload["emails_waiting"] = h.section("emails", emails)
    payload["ai_adoption"] = h.section("AI adoption (skill logs)", skill_usage.build,
                                       drive or skill_usage.Drive(), today)
    # Dachser check: one call per open order with reservations (~100), so it runs every
    # DACHSER_EVERY_MIN minutes (default 60: the :00 run); the other runs reuse the last result
    def dachser_pick():
        prev = (common.redis_get("lk:live") or {}).get("dachser_pick")
        if prev and prev.get("checked_at") and not force_dachser:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(prev["checked_at"])).total_seconds()
            if age < (DACHSER_EVERY_MIN - 10) * 60:
                return prev
        return dachser_notes.build(tl, orders, names, today=today)
    payload["dachser_pick"] = h.section("Dachser delivery notes", dachser_pick)
    payload["weclapp"] = h.section("weclapp vs TraceLink", weclapp_check.build,
                                   weclapp or weclapp_check.Weclapp(), tl, today)

    def plan():
        res = []
        for o in open_orders:
            if column_of(o["state"]) in (None, "Shipped - not closed"):
                continue
            for g in genobj_of(o["order_id"]):
                q = ox.fnum(g.get("unit_order_reserved_f"))
                if q > 0:
                    res.append({"sku": str(g.get("name") or "").strip(), "order": o["number"],
                                "customer": names.get(str(o["customer_id"]), ""),
                                "start": str(o.get("start_date") or "")[:10], "cases": round(q)})
        monday = today - dt.timedelta(days=today.weekday())
        past = [monday + dt.timedelta(days=i) for i in range(7) if monday + dt.timedelta(days=i) < today]
        lines = [(o["number"], names.get(str(o["customer_id"]), ""), str(o.get("start_date") or "")[:10], g)
                 for o in open_orders for g in genobj_of(o["order_id"])]
        return production.build((plan_source or production.download_plan)(), res, today,
                                production.fetch_actuals(tl, past), production.picks_from_lines(lines))
    if orders:
        payload["production"] = h.section("production plan", plan)

    payload = {k: v for k, v in payload.items() if v is not None}
    common.keep_previous(payload, "lk:live",
                         ["status_board", "biggest", "biggest_summary", "due_not_ready", "emails_waiting", "production",
                          "weclapp", "ai_adoption", "dachser_pick"])
    payload["dachser_tracking"] = dachser.status
    payload["dachser_accounts"] = [k["customer"] or "unnamed key" for k in getattr(dachser, "keys", []) if k.get("ok")]
    payload["dachser_no_answer"] = getattr(dachser, "no_answer", 0)
    payload["booking_warning"] = BOOKING_WARNING
    common.redis_set("lk:live", payload)
    return payload, h.publish({"dachser_tracking": dachser.status})


if __name__ == "__main__":
    _, health = main()
    print(health)
    raise SystemExit(0 if health["ok"] else 1)
