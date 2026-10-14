"""
dachser_notes.py - dashboard 2 "Picked by Dachser - pick in TraceLink": Hannover orders that
Dachser has picked (a delivery note exists in eLogistics) while the TraceLink mirror still has
reservations. Same matching as the tested dachser_notes_check.py:
  - open True Company GmbH orders (customer 1364) named "<Customer> | <PO>", reservations left
  - delivery notes by reference number 1 = PO (422 = none); a PO with a suffix
    ("01673087 - Sour Cola") is also tried without it
  - PO on one Dachser order: matched, whenever the TraceLink order was created
  - PO on several Dachser orders: the delivery date decides (closest, within MATCH_DAYS)
Keys: GitHub secrets DACHSER_NOTES_KEY (deliverynotes) and DACHSER_STATUS_KEY
(deliveryorderstatus, needed for the status column and for shared POs).
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DACHSER_BASE = "https://api-gateway.dachser.com"
DACHSER_NOTES_KEY = os.environ.get("DACHSER_NOTES_KEY", "").strip()
DACHSER_STATUS_KEY = os.environ.get("DACHSER_STATUS_KEY", "").strip()
GMBH_CUSTOMER = "1364"
MATCH_DAYS = 3

STATUS = {"WN": "sent to warehouse", "WK": "in preparation", "WB": "picking started",
          "WA": "ready for dispatch", "WV": "loading", "WF": "order faulty", "0": "no status"}


def po_of(name):
    """'dm drogerie markt GmbH | 1003725225' -> ['1003725225'];
    'Bela ... | 01673087 - Sour Cola' -> ['01673087 - Sour Cola', '01673087'] (Dachser may hold either)."""
    if "|" not in (name or ""):
        return []
    po = name.split("|", 1)[1].strip()
    out = [po]
    if " - " in po:
        out.append(po.split(" - ", 1)[0].strip())
    return [p for p in out if p]


def dachser(path, ref):
    key = DACHSER_NOTES_KEY if path.endswith("deliverynotes") else DACHSER_STATUS_KEY
    q = urllib.parse.urlencode({"reference-number1": ref})
    req = urllib.request.Request(f"{DACHSER_BASE}{path}?{q}",
                                 headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            time.sleep(0.25)
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        time.sleep(0.25)
        if e.code == 401 and path.endswith("deliverynotes"):
            raise RuntimeError("Dachser answered 401 on deliverynotes: DACHSER_NOTES_KEY is not "
                               "subscribed to the delivery notes API")
        if e.code == 401:
            globals()["DACHSER_STATUS_KEY"] = ""
        return e.code, {}


def check(tl, today):
    orders = tl.list_orders({"customer_id": f"={GMBH_CUSTOMER}", "locked": "=0"})
    rows = []
    for o in sorted(orders, key=lambda o: o.get("start_date") or ""):
        if o.get("delete_dt"):
            continue
        refs = po_of(o.get("name"))
        lines = tl.module("genobj", o["order_id"])
        reserved = sum(float(g.get("unit_order_reserved_f") or 0) for g in lines)
        picked = sum(float(g.get("unit_order_count_f") or 0) for g in lines)
        row = {"order": o["number"], "name": o.get("name", ""), "tracelink_state": o.get("state", ""),
               "start": str(o.get("start_date", ""))[:10], "delivery": str(o.get("deadline_date", ""))[:10],
               "reserved_cases": round(reserved), "picked_cases": round(picked),
               "dachser_ref": "", "dachser_order": "", "dachser_delivery": "", "dachser_status": "",
               "status_since": "", "delivery_note": "",
               "flag": ""}
        if not refs:
            row["flag"] = "no PO in name - not checked"
            rows.append(row)
            continue
        if reserved <= 0:
            row["flag"] = "fully picked in TraceLink"
            rows.append(row)
            continue
        deadline = str(o.get("deadline_date", ""))[:10]
        for ref in refs:
            code, body = dachser("/rest/v2/deliverynotes", ref)
            if code != 200 and ref != refs[-1]:
                continue
            row["dachser_ref"] = ref
            # Dachser orders on this PO with their newest delivery-note time
            notes = {}
            for x in body.get("deliveryOrders") or []:
                ts = [str(d.get("timestamp", "")) for d in (x.get("documents") or [])]
                if ts:
                    notes[x.get("id")] = max(ts)
            status = {}
            if DACHSER_STATUS_KEY:
                scode, sbody = dachser("/rest/v2/deliveryorderstatus", ref)
                for x in sbody.get("deliveryOrders") or []:
                    st = sorted(x.get("status") or [], key=lambda s: s.get("statusSequence") or 99)
                    ev = (st[0].get("event") or {}) if st else {}
                    status[x.get("id")] = {"delivery": str(x.get("deliveryDate", ""))[:10],
                                           "code": ev.get("code", ""), "since": str(st[0].get("statusDate", ""))[:16] if st else ""}
            ids = set(notes) | set(status)
            pick = None
            if len(ids) == 1:
                # one Dachser order on this PO: it is this order, whenever TraceLink created it
                pick = next(iter(ids))
            elif len(ids) > 1:
                # one PO, several Dachser orders (seen: Bela 01673087 x3): the delivery date decides
                cands = [(abs((dt.date.fromisoformat(v["delivery"]) - dt.date.fromisoformat(deadline)).days), i)
                         for i, v in status.items() if v["delivery"] and deadline]
                cands = [c for c in cands if c[0] <= MATCH_DAYS]
                if cands:
                    pick = min(cands)[1]
                elif not status:
                    row["flag"] = f"PO on {len(ids)} Dachser orders - check by hand"
            if pick:
                row["dachser_order"] = pick
                row["delivery_note"] = notes.get(pick, "")
                if pick in status:
                    v = status[pick]
                    row["dachser_status"] = f'{v["code"]} {STATUS.get(v["code"], "")}'.strip()
                    row["status_since"] = v["since"]
                    row["dachser_delivery"] = v["delivery"]
            break
        if row["flag"].startswith("PO on"):
            rows.append(row)
            continue
        row["flag"] = ("DELIVERY NOTE - NOT PICKED IN TRACELINK" if row["delivery_note"]
                       else "not picked by Dachser yet")
        rows.append(row)
    return rows



def build(tl, today, now_utc=None):
    """Payload for the dashboard: the flagged orders, oldest delivery note first."""
    if not DACHSER_NOTES_KEY:
        return {"connected": False}
    rows = check(tl, today)
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    hits = []
    for r in rows:
        if not r["flag"].startswith("DELIVERY NOTE"):
            continue
        try:
            note = dt.datetime.fromisoformat(r["delivery_note"].replace("Z", "+00:00"))
            if note.tzinfo is None:
                note = note.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            note = None
        hits.append({"order": r["order"], "customer": r["name"].split("|")[0].strip(),
                     "po": r["dachser_ref"], "reserved_cases": r["reserved_cases"],
                     "note": r["delivery_note"], "dachser_status": r["dachser_status"],
                     "note_days": (now_utc.date() - note.date()).days if note else None})
    hits.sort(key=lambda h: h["note"] or "9")
    return {"connected": True, "count": len(hits), "orders": hits,
            "checked": sum(1 for r in rows if r["dachser_ref"]),
            "status_key": bool(DACHSER_STATUS_KEY)}
