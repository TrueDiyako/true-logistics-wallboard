"""
credit_notes_extract.py - credit notes as a share of invoices (e-conomic).

A credit note in e-conomic is a booked invoice with a negative amount.
For every month (and ISO week) in the window the script counts invoices and
credit notes, by number and by value, and classifies every credit note so the
admin work behind it can be traced to a cause:

  correction       credit followed by a new invoice to the same customer for
                   the same order/heading within REINVOICE_DAYS (wrong price,
                   terms, quantity, address ... on the first invoice)
  full reversal    credit equals an earlier invoice to that customer in full,
                   with no re-invoice (cancelled / invoiced twice / returned)
  goods credit     partial credit of products (missing, damaged, short-dated,
                   price difference on some lines)
  non-goods        no product lines (fees, bonuses, marketing, free text)

Outputs (OUTPUT_DIR):
  credit_monthly.csv   month: invoices, credits, % by count, % by value, per category
  credit_weekly.csv    the same per ISO week (for the dashboard trend)
  credit_notes.csv     one row per credit note with category, linked invoices
  credit_lines.csv     every line on every credit note (products credited)
  credit_summary.txt   headline numbers

Pure standard library. Python 3.9+.   Run:  python credit_notes_extract.py
"""

# ============================ CONFIG ======================================
APP_SECRET_TOKEN      = "PASTE_APP_SECRET_TOKEN_HERE"
AGREEMENT_GRANT_TOKEN = "PASTE_AGREEMENT_GRANT_TOKEN_HERE"
BASE_URL = "https://restapi.e-conomic.com"

DATE_FROM = "2026-07-01"
DATE_TO   = None             # None = today
LOOKBACK_DAYS = 120          # earlier invoices scanned to find what a credit reverses

REINVOICE_DAYS = 10          # new invoice within this many days = correction
# Internal / intercompany customers: counted, but reported separately
# (DECISION: set INCLUDE_INTERNAL_IN_HEADLINE = False to leave them out)
INTERNAL_CUSTOMERS = {2, 1128, 1129, 3019, 1364}
INCLUDE_INTERNAL_IN_HEADLINE = True

OUTPUT_DIR = "credit_output"
SLEEP_S = 0.05
# ==========================================================================

# Hosted runs (GitHub Actions) take the keys from environment secrets;
# locally the constants above are used.
import os as _os
for _k in ('APP_SECRET_TOKEN', 'AGREEMENT_GRANT_TOKEN'):
    if _os.environ.get(_k):
        globals()[_k] = _os.environ[_k]


import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


# ----------------------------- API layer ----------------------------------
class Economic:
    def _get(self, url, retries=5):
        for attempt in range(retries):
            req = urllib.request.Request(url, headers={
                "X-AppSecretToken": APP_SECRET_TOKEN,
                "X-AgreementGrantToken": AGREEMENT_GRANT_TOKEN,
                "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    time.sleep(SLEEP_S)
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"e-conomic HTTP {e.code}: "
                                   f"{e.read().decode(errors='replace')[:300]}") from None

    def booked_invoices(self, d_from, d_to):
        q = urllib.parse.urlencode({"filter": f"date$gte:{d_from}$and:date$lte:{d_to}",
                                    "pagesize": 1000}, safe="$:")
        url, out = f"{BASE_URL}/invoices/booked?{q}", []
        while url:
            res = self._get(url)
            out += res.get("collection", [])
            url = (res.get("pagination") or {}).get("nextPage")
            print(f"  fetched {len(out)} invoices", flush=True)
        return out

    def invoice_detail(self, number):
        return self._get(f"{BASE_URL}/invoices/booked/{number}")


# ----------------------------- helpers ------------------------------------
def d(s):
    return dt.date.fromisoformat(str(s)[:10])


def cust(inv):
    return (inv.get("customer") or {}).get("customerNumber")


def dkk(inv):
    """Net amount in DKK (ex VAT), the basis for value ratios."""
    v = inv.get("netAmountInBaseCurrency")
    if v is None:
        v = inv.get("netAmount", 0) * (inv.get("exchangeRate", 100) or 100) / 100
    return float(v)


def heading(inv):
    return ((inv.get("notes") or {}).get("heading") or "").strip()


def ref_order(inv):
    """Order number a credit refers to ('Order no. #1022698' in the text lines)."""
    n = inv.get("notes") or {}
    m = re.search(r"#\s*(\d{6,})", f"{n.get('textLine1', '')} {n.get('textLine2', '')}")
    return int(m.group(1)) if m else None


def iso_week(day):
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


# ----------------------------- classification -----------------------------
def classify(credits, invoices, details):
    """Return one dict per credit note with category and linked invoices."""
    by_cust = {}
    for inv in invoices:
        by_cust.setdefault(cust(inv), []).append(inv)
    used_reinvoice = set()
    rows = []
    for c in sorted(credits, key=lambda x: (x["date"], x["bookedInvoiceNumber"])):
        cn, cd, cc = c["bookedInvoiceNumber"], d(c["date"]), cust(c)
        amount = round(float(c.get("netAmount", 0)), 2)
        pos = [i for i in by_cust.get(cc, []) if float(i.get("netAmount", 0)) > 0]
        ro, hd = ref_order(c), heading(c)

        # what does it reverse? the most recent earlier invoice of this customer
        # in the same chain (referenced order, same heading, or exact amount);
        # an exact-amount match wins, then the latest invoice (so a credit of a
        # re-invoice links to the re-invoice, not the first invoice)
        cands = [i for i in pos if d(i["date"]) <= cd and i["bookedInvoiceNumber"] < cn]
        chain = [i for i in cands
                 if (ro and (i.get("orderNumber") == ro or ref_order(i) == ro))
                 or (hd and heading(i) == hd)
                 or round(float(i.get("netAmount", 0)), 2) == -amount]
        original = max(chain, key=lambda i: (round(float(i.get("netAmount", 0)), 2) == -amount,
                                             i["bookedInvoiceNumber"]), default=None)
        full = original is not None and round(float(original.get("netAmount", 0)), 2) == -amount

        # re-invoiced? a later invoice, same customer, same heading / order ref
        reinv = None
        for i in sorted(pos, key=lambda i: (i["date"], i["bookedInvoiceNumber"])):
            if i["bookedInvoiceNumber"] in used_reinvoice or (original and i is original):
                continue
            gap = (d(i["date"]) - cd).days
            if gap < 0 or gap > REINVOICE_DAYS or i["bookedInvoiceNumber"] < cn:
                continue
            same = ((hd and heading(i) == hd)
                    or (ro and ref_order(i) == ro)
                    or (original and heading(original) and heading(i) == heading(original)))
            if same:
                reinv = i
                used_reinvoice.add(i["bookedInvoiceNumber"])
                break

        lines = (details.get(cn) or {}).get("lines") or []
        goods = [l for l in lines if (l.get("product") or {}).get("productNumber")]
        if reinv is not None:
            cat = "correction"
        elif full:
            cat = "full reversal"
        elif goods:
            cat = "goods credit"
        else:
            cat = "non-goods"

        rows.append({
            "credit_note": cn, "date": c["date"], "customer_number": cc,
            "customer": (c.get("recipient") or {}).get("name", ""),
            "internal": cc in INTERNAL_CUSTOMERS,
            "heading": hd, "currency": c.get("currency"),
            "amount": amount, "amount_dkk": round(dkk(c), 2),
            "category": cat,
            "reverses_invoice": original["bookedInvoiceNumber"] if original else "",
            "reverses_invoice_date": original["date"] if original else "",
            "days_after_invoice": (cd - d(original["date"])).days if original else "",
            "full_amount": full,
            "reinvoice": reinv["bookedInvoiceNumber"] if reinv else "",
            "reinvoice_amount": round(float(reinv.get("netAmount", 0)), 2) if reinv else "",
            "correction_type": (("same amount" if abs(float(reinv.get("netAmount", 0)) + amount) < 0.01
                                 else "re-invoiced lower" if float(reinv.get("netAmount", 0)) < -amount
                                 else "re-invoiced higher") if reinv else ""),
            "reinvoice_delta_dkk": (round(dkk(reinv) + dkk(c), 2) if reinv else ""),
            "same_day": bool(original) and (cd - d(original["date"])).days == 0,
            "goods_lines": len(goods), "lines": len(lines),
            "credited_units": -sum(float(l.get("quantity", 0)) for l in goods),
        })
    # Over-credited = the whole chain (all invoices and credit notes of one customer that
    # share a heading or an order reference) nets below zero. A heading that was invoiced,
    # credited, re-invoiced, credited and re-invoiced again (EUROBRANDS EB51: 5 documents,
    # net +36,012 EUR) is fine; only a negative balance means more was credited than billed.
    chains = chain_balances(list({x["bookedInvoiceNumber"]: x for x in invoices + credits}.values()))
    for r in rows:
        ch = chains.get(r["credit_note"], {})
        r["chain"] = ch.get("key", "")
        r["chain_documents"] = ch.get("documents", 0)
        r["chain_net"] = ch.get("net", "")
        r["over_credited"] = bool(ch) and ch["has_invoice"] and ch["net"] < -0.01
    return rows


_GENERIC_HEADINGS = {"", "order", "ordre", "invoice", "faktura", "credit note", "kreditnota"}


def chain_balances(docs):
    """Group one customer's invoices and credit notes into chains: documents are linked when
    they share a heading or an order reference ('Order no. #1021530', or the invoice's own
    order number). Returns {document number: {key, documents, net, has_invoice}}; net is in
    the documents' currency."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for doc in docs:
        n, c = doc["bookedInvoiceNumber"], cust(doc)
        keys = []
        hd = heading(doc).strip().lower()
        if hd not in _GENERIC_HEADINGS and len(hd) >= 3:
            keys.append(("h", c, hd))
        ro = ref_order(doc)
        if ro:
            keys.append(("o", c, ro))
        if float(doc.get("netAmount", 0)) > 0 and doc.get("orderNumber"):
            keys.append(("o", c, int(doc["orderNumber"])))
        for k in keys:
            union(("d", n), k)
    groups = {}
    for doc in docs:
        groups.setdefault(find(("d", doc["bookedInvoiceNumber"])), []).append(doc)
    out = {}
    for root, members in groups.items():
        net = round(sum(float(m.get("netAmount", 0)) for m in members), 2)
        info = {"key": " / ".join(sorted({heading(m) for m in members if heading(m)})) or str(root[1]),
                "documents": len(members), "net": net,
                "has_invoice": any(float(m.get("netAmount", 0)) > 0 for m in members)}
        for m in members:
            out[m["bookedInvoiceNumber"]] = info
    return out


def line_rows(credit_rows, details):
    out = []
    meta = {r["credit_note"]: r for r in credit_rows}
    for cn, det in details.items():
        r = meta.get(cn)
        if not r:
            continue
        rate = (det.get("exchangeRate") or 100) / 100
        for l in det.get("lines") or []:
            out.append({
                "credit_note": cn, "date": r["date"], "customer": r["customer"],
                "category": r["category"],
                "product": (l.get("product") or {}).get("productNumber", ""),
                "description": l.get("description", ""),
                "quantity": l.get("quantity", 0), "unit_price": l.get("unitNetPrice", 0),
                "discount_pct": l.get("discountPercentage", 0),
                "line_amount": l.get("totalNetAmount", 0),
                "line_amount_dkk": round(float(l.get("totalNetAmount", 0)) * rate, 2),
            })
    return out


def aggregate(invoices, credit_rows, keyfn, include_internal):
    cats = ("correction", "full reversal", "goods credit", "non-goods")
    agg = {}
    for i in invoices:
        if float(i.get("netAmount", 0)) <= 0:
            continue
        if not include_internal and cust(i) in INTERNAL_CUSTOMERS:
            continue
        a = agg.setdefault(keyfn(d(i["date"])), {"invoices": 0, "invoice_value_dkk": 0.0})
        a["invoices"] += 1
        a["invoice_value_dkk"] += dkk(i)
    for r in credit_rows:
        if not include_internal and r["internal"]:
            continue
        a = agg.setdefault(keyfn(d(r["date"])), {"invoices": 0, "invoice_value_dkk": 0.0})
        a["credits"] = a.get("credits", 0) + 1
        a["credit_value_dkk"] = a.get("credit_value_dkk", 0.0) - r["amount_dkk"]
        a[r["category"]] = a.get(r["category"], 0) + 1
        if r["category"] == "goods credit":
            a["goods_dkk"] = a.get("goods_dkk", 0.0) - r["amount_dkk"]
    out = []
    for k in sorted(agg):
        a = agg[k]
        cr, inv = a.get("credits", 0), a["invoices"]
        row = {"period": k, "invoices": inv, "credits": cr,
               "credit_pct_count": round(100 * cr / inv, 1) if inv else None,
               "invoice_value_dkk": round(a["invoice_value_dkk"], 0),
               "gross_credit_value_dkk": round(a.get("credit_value_dkk", 0.0), 0),
               "net_sales_dkk": round(a["invoice_value_dkk"] - a.get("credit_value_dkk", 0.0), 0),
               "goods_credit_dkk": round(a.get("goods_dkk", 0.0), 0),
               "goods_credit_pct_net_sales": (round(100 * a.get("goods_dkk", 0.0)
                                                    / (a["invoice_value_dkk"] - a.get("credit_value_dkk", 0.0)), 2)
                                              if a["invoice_value_dkk"] - a.get("credit_value_dkk", 0.0) > 0 else None)}
        for c in cats:
            row[c] = a.get(c, 0)
        out.append(row)
    return out


def write_csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)


def summarise(monthly, credit_rows, d_from, d_to):
    inv = sum(m["invoices"] for m in monthly)
    cr = sum(m["credits"] for m in monthly)
    iv = sum(m["invoice_value_dkk"] for m in monthly)
    cv = sum(m["gross_credit_value_dkk"] for m in monthly)
    net_sales = iv - cv
    scope = [r for r in credit_rows if INCLUDE_INTERNAL_IN_HEADLINE or not r["internal"]]
    goods_v = -sum(r["amount_dkk"] for r in scope if r["category"] == "goods credit")
    cats, ctype = {}, {}
    for r in scope:
        c = cats.setdefault(r["category"], [0, 0.0])
        c[0] += 1
        c[1] -= r["amount_dkk"]
        if r["correction_type"]:
            ctype[r["correction_type"]] = ctype.get(r["correction_type"], 0) + 1
    over = [r for r in scope if r["over_credited"]]
    same = sum(1 for r in scope if r["same_day"])
    lines = [f"Credit notes  {d_from} .. {d_to}   "
             f"({'incl.' if INCLUDE_INTERNAL_IN_HEADLINE else 'excl.'} internal/intercompany)",
             "",
             (f"HEADLINE  credit notes per invoice: {cr} of {inv} = {100 * cr / inv:.1f}%" if inv else "no invoices"),
             (f"Goods credited (missing/damaged/short-dated/price): {goods_v:,.0f} DKK = "
              f"{100 * goods_v / net_sales:.2f}% of net sales ({net_sales:,.0f} DKK)") if net_sales > 0 else "",
             "  (gross credit value is not a KPI: a credited-and-reissued invoice counts twice,",
             "   and one wrong invoice can be millions - see credit_notes.csv)",
             "", "Per month (credit notes / invoices):"]
    lines += [f"  {m['period']}: {m['credits']:>3} of {m['invoices']:>4} = {m['credit_pct_count'] or 0:>5.1f}%"
              for m in monthly]
    lines += ["", "Why (category: count, gross DKK):"]
    lines += [f"  {k:<14} {v[0]:>4}   {v[1]:>14,.0f}"
              for k, v in sorted(cats.items(), key=lambda x: -x[1][0])]
    lines += ["  corrections by type: " + ", ".join(f"{k} {v}" for k, v in sorted(ctype.items(), key=lambda x: -x[1]))]
    lines += ["", f"Same-day credits (invoice reversed the day it was booked): {same}",
              f"Invoices credited for more than their own amount: "
              f"{len({r['reverses_invoice'] for r in over})} invoices, {len(over)} credit notes -> column over_credited"]
    internal = [r for r in credit_rows if r["internal"]]
    lines += [f"Internal/intercompany credit notes: {len(internal)} "
              f"({-sum(r['amount_dkk'] for r in internal):,.0f} DKK)"]
    return "\n".join(lines)


def run(api, today=None):
    today = today or dt.date.today()
    d_from = dt.date.fromisoformat(DATE_FROM)
    d_to = dt.date.fromisoformat(DATE_TO) if DATE_TO else today
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Reading booked invoices {d_from - dt.timedelta(days=LOOKBACK_DAYS)} .. {d_to}", flush=True)
    allinv = api.booked_invoices(d_from - dt.timedelta(days=LOOKBACK_DAYS), d_to)
    window = [i for i in allinv if d_from <= d(i["date"]) <= d_to]
    credits = [i for i in window if float(i.get("netAmount", 0)) < 0]
    print(f"{len(window)} invoices in window, {len(credits)} credit notes; reading lines ...", flush=True)
    details = {}
    for c in credits:
        details[c["bookedInvoiceNumber"]] = api.invoice_detail(c["bookedInvoiceNumber"])

    rows = classify(credits, allinv, details)
    monthly = aggregate(window, rows, lambda x: x.strftime("%Y-%m"), INCLUDE_INTERNAL_IN_HEADLINE)
    weekly = aggregate(window, rows, iso_week, INCLUDE_INTERNAL_IN_HEADLINE)

    write_csv(os.path.join(OUTPUT_DIR, "credit_monthly.csv"), monthly)
    write_csv(os.path.join(OUTPUT_DIR, "credit_weekly.csv"), weekly)
    write_csv(os.path.join(OUTPUT_DIR, "credit_notes.csv"), rows)
    write_csv(os.path.join(OUTPUT_DIR, "credit_lines.csv"), line_rows(rows, details))
    summary = summarise(monthly, rows, d_from, d_to)
    with open(os.path.join(OUTPUT_DIR, "credit_summary.txt"), "w", encoding="utf-8") as f:
        f.write(summary)
    print("\n" + summary)
    return monthly, weekly, rows


if __name__ == "__main__":
    if "PASTE_" in APP_SECRET_TOKEN + AGREEMENT_GRANT_TOKEN:
        sys.exit("Paste APP_SECRET_TOKEN and AGREEMENT_GRANT_TOKEN at the top of the script.")
    run(Economic())
