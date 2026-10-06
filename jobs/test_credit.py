import datetime as dt, json, os, csv, shutil, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import credit_notes_extract as cx

def INV(n, date, cust, net, dkk=None, heading="", text="", order=None, cur="EUR", rate=747.5):
    return {"bookedInvoiceNumber": n, "date": date, "customer": {"customerNumber": cust},
            "netAmount": net, "netAmountInBaseCurrency": dkk if dkk is not None else round(net * rate / 100, 2),
            "currency": cur, "exchangeRate": rate, "orderNumber": order,
            "recipient": {"name": f"cust{cust}"}, "notes": {"heading": heading, "textLine1": text}}

def LINES(*ls):
    return {"exchangeRate": 747.5, "lines": [{"product": {"productNumber": p} if p else None,
                                              "description": desc, "quantity": q, "unitNetPrice": u,
                                              "totalNetAmount": q * u} for p, desc, q, u in ls]}

# --- real cases (Sep 2026) ---
ITU_ORIG = INV(20500, "2026-09-10", 1630, 9839.88, heading="11042", order=1022698)
ITU_CR   = INV(20690, "2026-09-29", 1630, -9839.88, -73549.43, "11042", "Order no. #1022698\r\n", 1022879)
ITU_RE   = INV(20691, "2026-09-29", 1630, 8270.88, 61828.69, "11042", "Order no. #1022698\r\n", 1022880)
LVK_INV  = INV(20706, "2026-09-30", 1491, 113495.2, 988938.03, "Stiller truck 5", order=1022685, cur="GBP")
LVK_CR   = INV(20707, "2026-09-30", 1491, -113495.2, -988938.03, "Stiller truck 5", order=1022891, cur="GBP")
VF_INV   = INV(20480, "2026-09-02", 1419, 2400.0, heading="Order 20.2026", order=1022487)
VF_CR    = INV(20713, "2026-09-30", 1419, -10.8, -80.73, "Order 20.2026", "Order no. #1022487\r\n", 1022900)
BONUS    = INV(20650, "2026-08-15", 1175, -5000.0, -5000.0, "Bonus Q2", cur="DKK", rate=100)
GMBH_CR  = INV(20600, "2026-08-20", 1364, -500.0, heading="REWE | 123")
NORMAL   = [INV(20000 + i, f"2026-0{7 + i % 3}-1{i % 9}", 1000 + i, 1000.0) for i in range(20)]

DETAILS = {
    20690: LINES(("1.15", "Raspberry & Vanilla", -18, 17.28), ("6301", "Sour Cola", -252, 9.48)),
    20707: LINES(("6301", "Sour Cola", -100, 10)),
    20713: LINES(("6006", "True Dates - Sour Watermelon", -1, 10.8)),
    20650: LINES((None, "Bonus Q2 2026", -1, 5000)),
    20600: LINES(("6001", "Sour Cola", -50, 10)),
}
ALL = [ITU_ORIG, ITU_CR, ITU_RE, LVK_INV, LVK_CR, VF_INV, VF_CR, BONUS, GMBH_CR] + NORMAL
CREDITS = [i for i in ALL if i["netAmount"] < 0]

def rows():
    return {r["credit_note"]: r for r in cx.classify(CREDITS, ALL, DETAILS)}

def test_itu_credit_and_reinvoice_is_correction():
    r = rows()[20690]
    assert r["category"] == "correction" and r["reinvoice"] == 20691
    assert r["reverses_invoice"] == 20500 and r["full_amount"] and r["days_after_invoice"] == 19

def test_lvk_same_day_full_reversal():
    r = rows()[20707]
    assert r["category"] == "full reversal" and r["reverses_invoice"] == 20706 and r["reinvoice"] == ""
    assert r["amount_dkk"] == -988938.03

def test_vitafood_one_case_is_goods_credit():
    r = rows()[20713]
    assert r["category"] == "goods credit" and r["reverses_invoice"] == 20480
    assert not r["full_amount"] and r["credited_units"] == 1

def test_bonus_without_products_is_non_goods():
    assert rows()[20650]["category"] == "non-goods"

def test_internal_flag():
    assert rows()[20600]["internal"] and not rows()[20690]["internal"]

def test_reinvoice_not_reused_and_window_respected():
    late = INV(20800, "2026-10-20", 1630, 8270.88, heading="11042")
    r = {x["credit_note"]: x for x in cx.classify([ITU_CR], [ITU_ORIG, ITU_CR, late], DETAILS)}
    assert r[20690]["category"] == "full reversal"          # 21 days later: not a re-invoice

def test_monthly_aggregation_and_internal_switch():
    rs = cx.classify(CREDITS, ALL, DETAILS)
    window = [i for i in ALL if i["date"] >= "2026-07-01"]
    m = {x["period"]: x for x in cx.aggregate(window, rs, lambda d: d.strftime("%Y-%m"), True)}
    sep = m["2026-09"]
    assert sep["credits"] == 3 and sep["correction"] == 1 and sep["full reversal"] == 1 and sep["goods credit"] == 1
    pos_sep = [i for i in window if i["date"].startswith("2026-09") and i["netAmount"] > 0]
    assert sep["invoices"] == len(pos_sep)
    assert sep["credit_pct_count"] == round(100 * 3 / len(pos_sep), 1)
    assert sep["goods_credit_dkk"] == 81 and sep["gross_credit_value_dkk"] == round(73549.43 + 988938.03 + 80.73)
    aug_in = m["2026-08"]["credits"]
    aug_ex = {x["period"]: x for x in cx.aggregate(window, rs, lambda d: d.strftime("%Y-%m"), False)}["2026-08"]["credits"]
    assert aug_in == 2 and aug_ex == 1

def test_chain_credit_of_reinvoice_links_to_reinvoice():
    # real EUROBRANDS EB34: 19887 -> credit 19902 + re-invoice 19903 (same) -> credit 19904 + re-invoice 19905 (lower)
    t = "Order no. #1021529"
    o  = INV(19887, "2026-07-14", 1692, 63132.0, heading="EB34 - Truck 25", order=1021529)
    c1 = INV(19902, "2026-07-16", 1692, -63132.0, heading="EB34 - Truck 25", text=t)
    r1 = INV(19903, "2026-07-16", 1692, 63132.0, heading="EB34 - Truck 25", text=t)
    c2 = INV(19904, "2026-07-16", 1692, -63132.0, heading="EB34 - Truck 25", text=t)
    r2 = INV(19905, "2026-07-16", 1692, 60890.66, heading="EB34 - Truck 25", text=t)
    det = {19902: LINES(("6201", "x", -10, 10)), 19904: LINES(("6201", "x", -10, 10))}
    r = {x["credit_note"]: x for x in cx.classify([c1, c2], [o, c1, r1, c2, r2], det)}
    assert r[19902]["reverses_invoice"] == 19887 and r[19902]["reinvoice"] == 19903
    assert r[19902]["correction_type"] == "same amount"
    assert r[19904]["reverses_invoice"] == 19903 and r[19904]["reinvoice"] == 19905
    assert r[19904]["correction_type"] == "re-invoiced lower"
    assert not r[19902]["over_credited"] and not r[19904]["over_credited"]

def test_double_credit_flagged():
    # real: 19964 (16,273 GBP) credited twice, 20565 and 20566
    o = INV(19964, "2026-07-20", 1491, 16273.0, heading="Delivery to Stiller 07.07.26 (2)", cur="GBP")
    a = INV(20565, "2026-09-01", 1491, -16273.0, heading="Delivery to Stiller 07.07.26 (2)", cur="GBP")
    b = INV(20566, "2026-09-01", 1491, -16273.0, heading="Delivery to Stiller 07.07.26 (2)", cur="GBP")
    det = {20565: LINES(("6301", "x", -1, 1)), 20566: LINES(("6301", "x", -1, 1))}
    r = cx.classify([a, b], [o, a, b], det)
    assert all(x["over_credited"] for x in r) and all(x["reverses_invoice"] == 19964 for x in r)

def test_eb51_chain_nets_positive_not_flagged():
    # real EUROBRANDS EB51 (e-conomic, 6 Oct 2026): invoice, credit, lower re-invoice, credit,
    # re-invoice - 5 documents, net +36,012.19 EUR -> counted as credits, NOT "to check"
    h = "EB51"
    i1 = INV(20131, "2026-08-10", 1692, 37046.4, heading=h, order=1022096)
    c1 = INV(20517, "2026-08-10", 1692, -37046.4, heading=h)
    i2 = INV(20518, "2026-09-10", 1692, 36012.19, heading=h, order=1022711)
    c2 = INV(20550, "2026-09-10", 1692, -37046.4, heading=h)
    i3 = INV(20551, "2026-09-14", 1692, 37046.4, heading=h, order=1022723)
    det = {20517: LINES(("6201", "x", -10, 10)), 20550: LINES(("6201", "x", -10, 10))}
    r = {x["credit_note"]: x for x in cx.classify([c1, c2], [i1, c1, i2, c2, i3], det)}
    assert not r[20517]["over_credited"] and not r[20550]["over_credited"]
    assert r[20550]["chain_net"] == 36012.19 and r[20550]["chain_documents"] == 5 and r[20550]["chain"] == "EB51"

def test_chain_via_order_reference_when_headings_differ():
    # credit carries 'Order no. #1021530' but a different heading than the invoice
    i = INV(20002, "2026-07-24", 1692, 50313.6, heading="EB35 - Truck 26 - LIDL CW31", order=1021530)
    c = INV(20513, "2026-09-10", 1692, -50313.6, heading="EB35 correction", text="Order no. #1021530")
    c2 = INV(20599, "2026-09-12", 1692, -1000.0, heading="EB35 correction", text="Order no. #1021530")
    r = {x["credit_note"]: x for x in cx.classify([c, c2], [i, c, c2], {})}
    assert r[20599]["over_credited"] and r[20599]["chain_net"] == -1000.0

def test_price_typo_correction():
    # real Dollarstore: 5760 x 2940 DKK invoiced instead of 2.94, credited and re-issued same day
    o = INV(19894, "2026-07-15", 2200, 16934400.0, heading="6498641", cur="DKK", rate=100)
    c = INV(19944, "2026-07-15", 2200, -16934400.0, heading="6498641", cur="DKK", rate=100)
    r = INV(19945, "2026-07-15", 2200, 16934.40, heading="6498641", cur="DKK", rate=100)
    [x] = cx.classify([c], [o, c, r], {19944: LINES(("616070", "Boom Energy", -5760, 2940))})
    assert x["category"] == "correction" and x["correction_type"] == "re-invoiced lower"
    assert x["same_day"] and x["reinvoice_delta_dkk"] == round(16934.40 - 16934400.0, 2)

def test_iso_week():
    assert cx.iso_week(dt.date(2026, 9, 30)) == "2026-W40"
    assert cx.iso_week(dt.date(2026, 1, 1)) == "2026-W01"

def test_end_to_end_with_mock_api_and_paging():
    seen = {"pages": 0, "details": 0}
    page1, page2 = ALL[:10], ALL[10:]
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["X-AppSecretToken"] == "a" and self.headers["X-AgreementGrantToken"] == "g"
            if self.path.startswith("/invoices/booked?"):
                seen["pages"] += 1
                assert "filter=date$gte:" in self.path, self.path
                body = {"collection": page1, "pagination": {"nextPage": f"{cx.BASE_URL}/invoices/booked/page2"}}
            elif self.path == "/invoices/booked/page2":
                seen["pages"] += 1
                body = {"collection": page2, "pagination": {}}
            else:
                seen["details"] += 1
                body = DETAILS[int(self.path.rsplit("/", 1)[1])]
            b = json.dumps(body).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cx.BASE_URL = f"http://127.0.0.1:{srv.server_port}"
    cx.APP_SECRET_TOKEN, cx.AGREEMENT_GRANT_TOKEN, cx.SLEEP_S = "a", "g", 0
    tmp = tempfile.mkdtemp(); cx.OUTPUT_DIR = tmp
    cx.DATE_FROM, cx.DATE_TO = "2026-07-01", "2026-10-02"
    try:
        monthly, weekly, rs = cx.run(cx.Economic(), today=dt.date(2026, 10, 2))
        assert seen["pages"] == 2 and seen["details"] == len(CREDITS)
        for f in ("credit_monthly.csv", "credit_weekly.csv", "credit_notes.csv", "credit_lines.csv", "credit_summary.txt"):
            assert os.path.getsize(os.path.join(tmp, f)) > 0, f
        with open(os.path.join(tmp, "credit_lines.csv"), encoding="utf-8-sig") as f:
            ls = list(csv.DictReader(f, delimiter=";"))
        assert len(ls) == sum(len(v["lines"]) for v in DETAILS.values())
        txt = open(os.path.join(tmp, "credit_summary.txt"), encoding="utf-8").read()
        assert "correction" in txt and "full reversal" in txt and "credit notes per invoice" in txt
        assert "% of net sales" in txt
    finally:
        srv.shutdown(); shutil.rmtree(tmp)

if __name__ == "__main__":
    import sys, traceback
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
