"""
weclapp_check.py - dashboard 2: weclapp sales orders (True Company GmbH, German market) that
have no TraceLink order yet.

weclapp:   every sales order created on/after TRACK_FROM, except cancelled ones and the
           test customer. The customer's PO is weclapp's "orderNumberAtCustomer".
TraceLink: orders of customer 1364 (True Company GmbH). Since 29 Sep 2026 one order per
           weclapp order named "customer | PO"; older batch orders ("P1774") list the POs in
           the description - both are read.
Match:     the PO exactly (case/spacing ignored), or a TraceLink PO that is a shortened form
           of the weclapp PO ("Order 2" for "Order 2 - Hammer Fitness Shop") for the same
           customer / a 5+ digit number. One TraceLink entry matches one weclapp order only,
           so "01673087 - displays" can't ride on "01673087".
"""
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import common

WECLAPP_BASE = os.environ.get("WECLAPP_BASE_URL", "https://jqbqyaafvfpmbhj.weclapp.com/webapp/api/v2")
WECLAPP_TOKEN = os.environ.get("WECLAPP_TOKEN", "PASTE_WECLAPP_API_TOKEN_HERE")
TRACK_FROM = dt.date(2026, 10, 6)      # only weclapp orders created from this day are tracked
GMBH_CUSTOMER = "1364"                 # True Company GmbH in TraceLink / e-conomic
TRACELINK_LOOKBACK_DAYS = 90           # TraceLink start dates to scan for the matching orders
PAGE_SIZE = 500
TEST_CUSTOMERS = ("test customer",)


def cph_midnight_ms(day):
    """Copenhagen midnight of `day` as epoch millis (weclapp dates are epoch millis)."""
    naive = dt.datetime.combine(day, dt.time()).replace(tzinfo=dt.timezone.utc)
    return int((naive - dt.timedelta(hours=common.cph_offset_hours(naive))).timestamp() * 1000)


def ms_to_local_date(ms):
    return common.to_cph(dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)).date()


class Weclapp:
    def __init__(self, base=None, token=None):
        self.base, self.token = (base or WECLAPP_BASE).rstrip("/"), token or WECLAPP_TOKEN

    def get(self, path, params):
        url = f"{self.base}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"AuthenticationToken": self.token, "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise RuntimeError(f"weclapp HTTP {e.code}: {e.read()[:200]!r}")
        raise RuntimeError("weclapp: no answer after 3 attempts")

    def sales_orders_since(self, day):
        out, page = [], 1
        while True:
            res = self.get("salesOrder", {
                "orderDate-ge": cph_midnight_ms(day - dt.timedelta(days=7)),   # order date may be backdated
                "properties": "id,orderNumber,orderNumberAtCustomer,status,orderDate,createdDate,"
                              "plannedDeliveryDate,invoiceAddress",
                "sort": "orderDate", "pageSize": PAGE_SIZE, "page": page})
            batch = res.get("result") or []
            out += batch
            if len(batch) < PAGE_SIZE:
                return out
            page += 1


def customer_name(o):
    a = o.get("invoiceAddress") or {}
    return (a.get("company") or " ".join(x for x in (a.get("firstName"), a.get("lastName")) if x) or "").strip()


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


_PFX = re.compile(r"^(orders?:?|po|dm\s*-)\s*", re.I)


def tracelink_refs(orders):
    """[(tracelink order number, PO, customer)] from 'customer | PO' names and batch descriptions."""
    out = []
    for o in orders:
        name, desc = o.get("name") or "", o.get("description") or ""
        if "|" in name:
            cust, po = name.split("|", 1)
            out.append((o["number"], norm(po), norm(cust)))
        for part in re.split(r"[\r\n,]+", desc):
            p = _PFX.sub("", _PFX.sub("", part.strip()))
            if p:
                out.append((o["number"], norm(p), ""))
    return out


def match(weclapp_orders, tracelink_orders, today):
    refs = tracelink_refs(tracelink_orders)
    used, hits = set(), {}
    orders = sorted(weclapp_orders, key=lambda o: (o.get("createdDate") or 0, o.get("orderNumber") or ""))
    # pass 1: exact PO for everyone, so a shortened match can never take an exact one's entry
    for o in orders:
        po = norm(o.get("orderNumberAtCustomer"))
        hit = next(((n, r) for n, r, c in refs if po and (n, r) not in used and r == po), None)
        if hit:
            used.add(hit)
            hits[id(o)] = hit
    # pass 2: TraceLink kept a shortened PO ("Order 2" for "Order 2 - Hammer Fitness Shop")
    for o in orders:
        if id(o) in hits:
            continue
        po, cust = norm(o.get("orderNumberAtCustomer")), customer_name(o)
        hit = next(((n, r) for n, r, c in refs if po and r and (n, r) not in used
                    and re.match(re.escape(r) + r"[\s_\-]", po)
                    and (len(re.sub(r"\D", "", r)) >= 5 or (c and norm(cust)[:6] == c[:6]))), None)
        if hit:
            used.add(hit)
            hits[id(o)] = hit
    rows = []
    for o in orders:
        hit = hits.get(id(o))
        created = ms_to_local_date(o.get("createdDate") or o.get("orderDate") or 0)
        rows.append({"weclapp": o.get("orderNumber"), "customer": customer_name(o),
                     "po": o.get("orderNumberAtCustomer") or "", "status": o.get("status"),
                     "created": created.isoformat(), "age_days": max(0, (today - created).days),
                     "tracelink": hit[0] if hit else ""})
    return rows


def build(weclapp, tracelink, today, track_from=TRACK_FROM):
    since = (today - dt.timedelta(days=TRACELINK_LOOKBACK_DAYS)).isoformat()
    tl = tracelink.list_orders({"customer_id": f"={GMBH_CUSTOMER}", "start_date": f">{since}"})
    wc = [o for o in weclapp.sales_orders_since(track_from)
          if o.get("status") != "CANCELLED"
          and ms_to_local_date(o.get("createdDate") or o.get("orderDate") or 0) >= track_from
          and not any(t in customer_name(o).lower() for t in TEST_CUSTOMERS)]
    rows = match(wc, [o for o in tl if not o.get("delete_dt")], today)
    missing = sorted((r for r in rows if not r["tracelink"]), key=lambda r: (-r["age_days"], r["weclapp"] or ""))
    return {"tracking_from": track_from.isoformat(), "orders": len(rows), "in_tracelink": len(rows) - len(missing),
            "missing_count": len(missing), "no_po": sum(1 for r in missing if not r["po"]), "missing": missing}
