import json
import datetime as dt, json, os, shutil, tempfile, threading, csv
from http.server import BaseHTTPRequestHandler, HTTPServer
import response_time_extract as rx

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
_n = [0]

def M(conv, frm, when, subject="", to=(), cc=(), draft=False):
    _n[0] += 1
    e = lambda a: {"emailAddress": {"address": a}}
    return {"id": f"m{_n[0]}", "conversationId": conv, "subject": subject,
            "from": e(frm), "toRecipients": [e(a) for a in to], "ccRecipients": [e(a) for a in cc],
            "receivedDateTime": when, "sentDateTime": when, "isDraft": draft}

# --- real thread: Wholefoods PO 97730 (sent 15 Sep, chased 1 Oct, answered 2 Oct) ---
WF = [M("wf", "awirkus@wholefoods.ie", "2026-09-15T13:41:00Z", "Purchase Order - 97730", to=["order@truecompany.com"]),
      M("wf", "awirkus@wholefoods.ie", "2026-10-01T09:15:00Z", "Re: Purchase Order - 97730", to=["order@truecompany.com"]),
      M("wf", "order@truecompany.com", "2026-10-02T05:12:43Z", "SV: Purchase Order - 97730", to=["awirkus@wholefoods.ie"])]

def waits(msgs):
    return rx.build_waits(msgs, NOW)[0]

def test_wholefoods_real_case():
    [w] = waits(WF)
    assert w["answered"] and w["first_in_thread"] and w["is_order"]
    assert w["chasers_before_reply"] == 1
    # 15 Sep 15:41-16:00 (0.32) + 11 workdays 16-30 Sep (88) + 1 Oct (8) + 2 Oct before 08 (0)
    assert w["business_hours"] == 96.32, w["business_hours"]
    assert w["received_local"] == "2026-09-15 15:41" and w["replied_local"] == "2026-10-02 07:12"

def test_quick_reply_then_thank_you_is_open_wait():
    g = [M("g", "contabilidad@gourmandise.es", "2026-10-02T07:30:00Z", "GOURMANDISE NEW ORDER PC26387", to=["order@truecompany.com"]),
         M("g", "order@truecompany.com", "2026-10-02T07:56:48Z", "SV: GOURMANDISE NEW ORDER PC26387", to=["contabilidad@gourmandise.es"]),
         M("g", "contabilidad@gourmandise.es", "2026-10-02T08:08:17Z", "Re: GOURMANDISE NEW ORDER PC26387", to=["order@truecompany.com"])]
    w = waits(g)
    assert len(w) == 2
    first = [x for x in w if x["first_in_thread"]][0]
    assert first["answered"] and first["business_hours"] == 0.45
    second = [x for x in w if not x["first_in_thread"]][0]
    assert not second["answered"]

def test_internal_forward_does_not_count_as_reply():
    t = [M("ea", "vj@euroarcade.net", "2026-09-30T08:00:00Z", "Euro Arcade Introduction", to=["order@truecompany.com"]),
         M("ea", "order@truecompany.com", "2026-09-30T14:10:25Z", "VS: Euro Arcade Introduction", to=["true@balticassist.com"]),
         M("ea", "order@truecompany.com", "2026-10-01T11:06:45Z", "VS: Euro Arcade Introduction", to=["sales@truecompany.com"])]
    [w] = waits(t)
    assert not w["answered"]

def test_reply_to_colleague_at_same_company_counts():
    t = [M("x", "jurica.triska@arket.com", "2026-09-29T08:00:00Z", "ARKET Kastrup", to=["wholesale@truegum.com"]),
         M("x", "andreas@truecompany.com", "2026-09-29T09:00:00Z", "RE: ARKET Kastrup",
           to=["mariana.reis@arket.com"], cc=["wholesale@truegum.com"])]
    [w] = waits(t)
    assert w["answered"] and w["replied_by"] == "andreas@truecompany.com"

def test_freemail_needs_exact_address():
    t = [M("f", "sotirova.darina@gmail.com", "2026-10-01T08:00:00Z", "A new order", to=["order@truecompany.com"]),
         M("f", "order@truecompany.com", "2026-10-01T09:00:00Z", "SV: A new order", to=["someone.else@gmail.com"])]
    assert not waits(t)[0]["answered"]
    t.append(M("f", "order@truecompany.com", "2026-10-01T10:00:00Z", "SV: A new order", to=["sotirova.darina@gmail.com"]))
    w = waits(t)[0]
    assert w["answered"] and w["business_hours"] == 2.0

def test_automated_senders_and_auto_replies_ignored():
    t = [M("a", "noreply@gls-denmark.com", "2026-10-01T15:34:00Z", "GLS pakke", to=["order@truecompany.com"]),
         M("b", "Logistik-Disposition@rewe-group.com", "2026-10-02T06:23:00Z", "151 - Mahnung", to=["wholesale@truegum.com"]),
         M("c", "KONTRAKTLOGISTIK.LANGENHAGEN-FL@DACHSER.COM", "2026-10-02T05:34:00Z", "Lieferscheinnummer", to=["order@truecompany.com"]),
         M("d", "jane@shop.se", "2026-10-02T06:00:00Z", "Automatic reply: Order", to=["order@truecompany.com"])]
    rows, msgs = rx.build_waits(t, NOW)
    assert rows == [] and {m["_class"] for m in msgs} == {"automated"}

def test_weekend_and_holiday_excluded():
    fri = dt.datetime(2026, 9, 25, 13, 0, tzinfo=UTC)          # 15:00 local Fri
    mon = dt.datetime(2026, 9, 28, 7, 0, tzinfo=UTC)           # 09:00 local Mon
    assert rx.business_hours(fri, mon) == 2.0
    a = dt.datetime(2026, 12, 23, 14, 0, tzinfo=UTC)           # 15:00 local Wed
    b = dt.datetime(2026, 12, 28, 8, 0, tzinfo=UTC)            # 09:00 local Mon (24,25 holidays)
    assert rx.business_hours(a, b) == 2.0

def test_dst_boundaries():
    assert rx.cph_offset(dt.datetime(2026, 10, 25, 0, 59, tzinfo=UTC)) == dt.timedelta(hours=2)
    assert rx.cph_offset(dt.datetime(2026, 10, 25, 1, 0, tzinfo=UTC)) == dt.timedelta(hours=1)
    assert rx.cph_offset(dt.datetime(2026, 3, 29, 0, 59, tzinfo=UTC)) == dt.timedelta(hours=1)
    assert rx.cph_offset(dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC)) == dt.timedelta(hours=2)
    assert rx.cph_offset(dt.datetime(2027, 3, 28, 1, 0, tzinfo=UTC)) == dt.timedelta(hours=2)

def test_team_initiated_thread_is_not_first_response():
    t = [M("t", "order@truecompany.com", "2026-09-30T08:00:00Z", "Your order", to=["buyer@shop.dk"]),
         M("t", "buyer@shop.dk", "2026-09-30T09:00:00Z", "Re: Your order", to=["order@truecompany.com"])]
    [w] = waits(t)
    assert not w["first_in_thread"] and not w["customer_initiated_thread"]

def test_two_customers_same_thread_tracked_separately():
    t = [M("c", "a@buyer.de", "2026-09-30T08:00:00Z", "Order", to=["order@truecompany.com"]),
         M("c", "b@carrier.pl", "2026-09-30T08:30:00Z", "RE: Order", to=["order@truecompany.com"]),
         M("c", "order@truecompany.com", "2026-09-30T09:00:00Z", "SV: Order", to=["a@buyer.de"])]
    w = {x["customer"]: x for x in waits(t)}
    assert w["a@buyer.de"]["answered"] and not w["b@carrier.pl"]["answered"]

def test_drafts_and_duplicates_dropped():
    m = M("d", "a@buyer.de", "2026-09-30T08:00:00Z", "Order", to=["order@truecompany.com"])
    dup = dict(m, id="copy")
    dr = M("d", "order@truecompany.com", "2026-09-30T08:10:00Z", "draft", to=["a@buyer.de"], draft=True)
    rows, msgs = rx.build_waits([m, dup, dr], NOW)
    assert len(msgs) == 1 and len(rows) == 1 and not rows[0]["answered"]

def test_reply_in_other_thread_matched_by_order_number():
    # real: Firtal sends from purchase@mg.firtal.com, team answers purchase@firtal.com in a new mail
    t = [M("p1", "purchase@mg.firtal.com", "2026-07-03T02:04:00Z", "PO #10084717-Tru from Firtal Web A/S", to=["wholesale@truegum.com"]),
         M("p2", "order@truecompany.com", "2026-07-03T08:40:00Z", "SV: Delivery of PO 10084717-Tru", to=["purchase@firtal.com"])]
    [w] = waits(t)
    assert w["answered"] and w["replied_via"] == "other thread" and w["in_kpi"]
    assert w["business_hours"] == 2.67

def test_other_thread_unrelated_mail_only_counts_within_one_business_day():
    # option C: any team mail to the same company within 1 business day counts (agreed trade-off);
    # an unrelated mail 2 business days later does not, and a reply after FALLBACK_DAYS doesn't either
    t = [M("p1", "a@buyer.de", "2026-07-03T08:00:00Z", "New order 4711", to=["order@truecompany.com"]),   # Friday
         M("p2", "order@truecompany.com", "2026-07-07T11:00:00Z", "Price list 2027", to=["a@buyer.de"]),  # Tuesday
         M("p3", "order@truecompany.com", "2026-07-20T09:00:00Z", "SV: New order 4711", to=["a@buyer.de"])]
    [w] = waits(t)
    assert not w["answered"]
    t[1] = M("p2", "order@truecompany.com", "2026-07-03T09:00:00Z", "Price list 2027", to=["a@buyer.de"])
    [w] = waits(t)
    assert w["answered"] and w["replied_via"] == "new thread (same company)"

def test_humble_reply_in_new_thread_to_sister_domain():
    # real (21 Sep 2026): POs from orders@humblegroupusa.com 07:52Z; Marc answers 12:30Z in a NEW thread
    # "Humble Group US - " to cameron.morris@humblegroup.com (other domain), order@ in copy,
    # mentioning PO-TRC-20260921-4FA0 in the text
    po1 = M("a", "orders@humblegroupusa.com", "2026-09-21T07:52:22Z",
            "Purchase Order PO-TRC-20260921-4FA0 — Humble Group USA Inc.", to=["order@truecompany.com"])
    po2 = M("b", "orders@humblegroupusa.com", "2026-09-21T07:52:56Z",
            "Purchase Order PO-TRC-20260921-NEOW — Humble Group USA Inc.", to=["order@truecompany.com"])
    rep = M("c", "marc@truecompany.com", "2026-09-21T12:30:20Z", "Humble Group US - ",
            to=["cameron.morris@humblegroup.com"], cc=["order@truecompany.com"])
    rep["bodyPreview"] = ("Hi Cameron, Thanks for the order, we will rush to get it ready as soon as possible. "
                          "PO-TRC-20260921-4FA0 (US Products - Airfreight) Requested Shipment Date 25th of September")
    w = {x["subject"][:35]: x for x in waits([po1, po2, rep])}
    a, b = w["Purchase Order PO-TRC-20260921-4FA0"], w["Purchase Order PO-TRC-20260921-NEOW"]
    assert a["answered"] and a["replied_via"] == "new thread (reference)"
    assert b["answered"] and b["replied_via"] == "new thread (same company)"      # same day, same company
    assert rx.company_of("cameron.morris@humblegroup.com") == rx.company_of("orders@humblegroupusa.com") == "humble"

def test_references_and_freemail_has_no_company():
    assert rx.references("Purchase Order PO-TRC-20260921-NEOW — Humble") == {"po-trc-20260921-neow"}
    assert rx.references("Købsordre KO054417 fra Sügro") == {"ko054417"}
    assert rx.references("Hello there 2026") == set()
    assert rx.company_of("someone@gmail.com") == ""

def test_broken_thread_matched_by_normalised_subject():
    t = [M("a", "sales@ecocareinnovation.com", "2026-07-01T08:28:00Z", "Re[3]: SV: SV: URGENT: NEW ORDER GREECE", to=["order@truecompany.com"]),
         M("b", "order@truecompany.com", "2026-07-01T09:35:00Z", "SV: Re[4]: SV: SV: URGENT: NEW ORDER GREECE", to=["sales@ecocareinnovation.com"])]
    [w] = waits(t)
    assert w["answered"] and w["replied_via"] == "other thread"
    assert not w["new_topic"] and not w["in_kpi"]   # opener is a reply -> follow-up, not headline

def test_personal_mailbox_and_finance_out_of_headline():
    t = [M("v", "buyer@siradis.ch", "2026-07-02T10:16:00Z", "Stock question", to=["valentina@truecompany.com"]),
         M("f", "ekonomi@gsdmail.se", "2026-07-06T11:21:00Z", "Faktura 19440 true APS", to=["order@truecompany.com"])]
    w = {x["customer"]: x for x in waits(t)}
    assert not w["buyer@siradis.ch"]["to_shared_address"] and not w["buyer@siradis.ch"]["in_kpi"]
    assert w["ekonomi@gsdmail.se"]["finance"] and not w["ekonomi@gsdmail.se"]["in_kpi"]

def test_partners_and_new_automated_patterns():
    t = [M("1", "tina.lehnert@dk.dsv.com", "2026-07-10T10:34:00Z", "DSV: NAESJ-E4G50", to=["order@truecompany.com"]),
         M("2", "alexandra.maughan@stiller.co.uk", "2026-07-10T10:34:00Z", "Stock", to=["order@truecompany.com"]),
         M("3", "po_do_not_reply@sallinggroup.com", "2026-07-22T20:10:00Z", "Difference Notice:4047331534", to=["order@truecompany.com"]),
         M("4", "sys_uc4@minden.edeka.de", "2026-07-02T07:54:00Z", "Bestellung 2070002215 von EDEKA Meissner", to=["order@truecompany.com"]),
         M("5", "dmatmailbestellungen.atmailbox@dm.at", "2026-07-07T06:11:00Z", "dm-Bestellung AT01", to=["order@truecompany.com"]),
         M("6", "dse@eumail.docusign.net", "2026-07-10T11:28:00Z", "Contenera", to=["order@truecompany.com"])]
    rows, msgs = rx.build_waits(t, NOW)
    assert rows == []
    assert [m["_class"] for m in msgs] == ["automated", "partner", "partner", "automated", "automated", "automated"] or \
           sorted(m["_class"] for m in msgs) == ["automated"] * 4 + ["partner"] * 2

def test_duplicate_copy_in_other_thread_dropped():
    a = M("x1", "jakub@eurobrands.com.pl", "2026-07-24T11:06:00Z", "EB51", to=["order@truecompany.com"])
    b = dict(a, id="copy", conversationId="x2")
    rows, msgs = rx.build_waits([a, b], NOW)
    assert len(msgs) == 1 and len(rows) == 1

def test_supplier_confirmations_newsletters_and_qa_topics_out():
    t = [M("1", "jakodan@jakodan.dk", "2026-09-30T12:41:00Z", "Jakodan A/S: Ordrebekræftelse # 1000331096", to=["order@truecompany.com"]),
         M("2", "hello@exa.ai", "2026-09-11T19:31:00Z", "Using Exa", to=["order@truecompany.com"]),
         M("3", "x@shop.se", "2026-09-25T06:22:00Z", "Recall: New Items", to=["order@truecompany.com"]),
         M("4", "e.stefani@iph.com.cy", "2026-09-01T05:36:00Z", "Packaging Compliance with Regulation (EU) 2025/40 (PPWR)", to=["order@truecompany.com"]),
         M("5", "finance@proshop.com", "2026-08-05T07:57:00Z", "Rykker 1 - Kundenr.: 131786181", to=["order@truecompany.com"])]
    rows, msgs = rx.build_waits(t, NOW)
    cls = {m["_from"]: m["_class"] for m in msgs}
    assert cls["jakodan@jakodan.dk"] == "automated" and cls["hello@exa.ai"] == "non-customer"
    assert cls["x@shop.se"] == "automated"
    w = {r["customer"]: r for r in rows}
    assert w["e.stefani@iph.com.cy"]["non_logistics"] and not w["e.stefani@iph.com.cy"]["in_kpi"]
    assert w["finance@proshop.com"]["finance"] and not w["finance@proshop.com"]["in_kpi"]

def test_buckets_and_distribution():
    mk = lambda ans, bh, d="2026-09-28 09:00": {"answered": ans, "business_hours": bh, "received_local": d}
    rs = [mk(True, 3), mk(True, 8), mk(True, 12), mk(True, 24), mk(True, 296), mk(False, 50)]
    assert [rx.bucket(r) for r in rs] == ["within 1 day", "within 1 day", "within 2 days",
                                          "within 3 days", "over 3 days", "no reply"]
    [d] = rx.distribution(rs, lambda r: rx.iso_week_of(r["received_local"]))
    assert d["period"] == "2026-W40" and d["waits"] == 6 and d["no reply %"] == 16.7

def test_owner_mapping_and_no_confirmation_customers():
    # real (2 Oct): Siradis -> Andreas; Firtal sends from mg.firtal.com -> Frederikke;
    # Helsam POs say "contact us only if the date can't be met" -> no reply expected
    t = [M("s", "purchasing@siradis.ch", "2026-09-30T07:26:00Z", "***Siradis - Order True Co - PO-00808***", to=["order@truecompany.com"]),
         M("f", "purchase@mg.firtal.com", "2026-09-18T02:05:00Z", "PO #10087128-Tru from Firtal Web A/S", to=["wholesale@truegum.com"]),
         M("h", "tn@helsam.dk", "2026-09-21T07:36:00Z", "Købsordre 238436", to=["wholesale@truegum.com"]),
         M("x", "fred@sm-cocktails.at", "2026-09-29T10:18:00Z", "Initial order / B&B Trost", to=["order@truecompany.com"])]
    w = {r["customer"]: r for r in waits(t)}
    assert w["purchasing@siradis.ch"]["owner"] == "Andreas" and w["purchasing@siradis.ch"]["in_kpi"]
    assert w["purchase@mg.firtal.com"]["owner"] == "Frederikke"
    assert w["tn@helsam.dk"]["no_reply_needed"] and not w["tn@helsam.dk"]["in_kpi"]
    assert w["fred@sm-cocktails.at"]["owner"] == "" and w["fred@sm-cocktails.at"]["in_kpi"]

def test_lvk_is_partner_and_debit_note_is_finance():
    t = [M("l", "david@lvk.co", "2026-09-30T14:48:00Z", "FW: CN Foods - True ApS Order", to=["order@truecompany.com"]),
         M("c", "purchaseledger@clfwholesale.com", "2026-09-18T09:59:00Z", "Short Shelf-Life Claim August 2026", to=["order@truecompany.com"])]
    rows, msgs = rx.build_waits(t, NOW)
    assert {m["_from"]: m["_class"] for m in msgs}["david@lvk.co"] == "partner"
    assert rx.is_finance("Debit note for short shelf-life claim")

def test_personal_mailbox_discovery():
    msgs = [M("a", "buyer@x.dk", "2026-09-28T06:00:00Z", "Order", to=["order@truecompany.com", "Frederikke@truecompany.com"],
              cc=["sales@truecompany.com", "invoice@truecompany.com"]),
            M("b", "andreas@truecompany.com", "2026-09-28T07:00:00Z", "RE: Order", to=["buyer@x.dk"], cc=["wholesale@truegum.com"])]
    assert rx.personal_mailboxes(msgs) == ["andreas@truecompany.com", "frederikke@truecompany.com"]

def test_reply_inferred_from_quoted_header_in_followup():
    # real Prezentex 172026: Frederikke answers from her own mailbox; the customer's
    # next mail (to order@) quotes "From: Frederikke Pingel <...> Sent: Friday, October 2, 2026 9:04 AM"
    t = [M("z", "varga.martin@prezentex.hu", "2026-09-30T08:49:00Z", "172026", to=["order@truecompany.com"]),
         M("z", "varga.martin@prezentex.hu", "2026-10-02T07:24:25Z", "RE: 172026", to=["order@truecompany.com"])]
    body = ("Hi Frederikke,\r\nSour Cola is really important due to ALDI promo.\r\n\r\n"
            "From: Frederikke Pingel <Frederikke@truecompany.com>\r\nSent: Wednesday, September 30, 2026 11:51 AM\r\n"
            "To: Varga Martin <varga.martin@prezentex.hu>\r\nSubject: RE: 172026")
    rows, _ = rx.build_waits(t, NOW, body_of=lambda i: body)
    [w] = [r for r in rows if r["first_in_thread"]]
    assert w["answered"] and w["without_order_copy"] and w["replied_by"] == "frederikke@truecompany.com"
    assert w["replied_local"] == "2026-09-30 11:51"              # the quoted reply time, not the follow-up
    assert w["business_hours"] == round(rx.business_hours(rx.parse_ts("2026-09-30T08:49:00Z"),
                                                          rx.parse_ts("2026-09-30T09:51:00Z")), 2)

def test_reply_inferred_from_followup_addressed_to_colleague():
    # real GSD: order to wholesale@, next customer mail goes To Frederikke@ ("tack för order")
    t = [M("g", "samanttha.nilsson@gsdmail.se", "2026-09-28T05:52:00Z", "Bestillning til BIG DOLLAR HOLSTEBRO", to=["wholesale@truegum.com"]),
         M("g", "order@gsdmail.se", "2026-09-28T06:09:00Z", "Sv: Bestillning til BIG DOLLAR HOLSTEBRO", to=["Frederikke@truecompany.com", "order@truecompany.com"])]
    rows, _ = rx.build_waits(t, NOW, body_of=lambda i: "Godmorgon och tack för order!")
    w = [r for r in rows if r["first_in_thread"]][0]
    assert w["answered"] and w["without_order_copy"] and w["replied_by"] == "frederikke@truecompany.com"
    assert w["replied_via"].endswith("(time = customer follow-up)")

def test_no_evidence_stays_unanswered_and_visible_reply_is_not_outside():
    t = [M("o", "inkop@outofhome.se", "2026-09-25T07:21:00Z", "Inköpsorder 371727", to=["wholesale@truegum.com"]),
         M("o", "inkop@outofhome.se", "2026-10-02T09:24:00Z", "Re: Inköpsorder 371727", to=["wholesale@truegum.com"])]
    rows, _ = rx.build_waits(t, NOW, body_of=lambda i: "Sending a reminder about this e-mail.")
    assert not [r for r in rows if r["first_in_thread"]][0]["answered"]
    [w] = waits(WF)
    assert w["answered"] and not w["without_order_copy"]

def test_quoted_header_variants():
    q = rx.quoted_team_replies("Fra: Andreas Mendoza <andreas@truecompany.com>\nSendt: 2. oktober 2026 11:15\n"
                               "Från: Valentina Trujillo <valentina@truecompany.com>\nSkickat: den 1 oktober 2026 09:04\n"
                               "On Tue, Sep 29, 2026 at 2:05 PM Marc Ferrigno <marc@truecompany.com> wrote:\n"
                               "From: Order | True Co. <order@truecompany.com>\nSent: Friday, October 2, 2026 9:04 AM\n"
                               "From: Robert <purchasing@siradis.ch>\nSent: Friday, October 2, 2026 9:04 AM")
    got = {a: rx.to_local(t).strftime("%Y-%m-%d %H:%M") for a, t in q}
    assert got == {"andreas@truecompany.com": "2026-10-02 11:15", "valentina@truecompany.com": "2026-10-01 09:04",
                   "marc@truecompany.com": "2026-09-29 14:05", "order@truecompany.com": "2026-10-02 09:04"}

def test_reply_from_personal_mailbox_counts():
    # real: Coop 4511864346 - Frederikke answered from her own mailbox, order@ not in copy
    t = [M("c", "order_ready@coop.dk", "2026-09-28T07:37:00Z", "4511864346 - DK", to=["wholesale@truegum.com"]),
         M("p", "frederikke@truecompany.com", "2026-09-28T11:02:00Z", "SV: 4511864346 - DK", to=["order_ready@coop.dk"])]
    [w] = waits(t)
    assert w["answered"] and w["replied_by"] == "frederikke@truecompany.com"

def test_graph_gzip_and_cut_off_page_is_fetched_again():
    # real (7 Oct): a Graph page arrived cut off at 439 KB -> invalid JSON; must be re-fetched
    import gzip
    good = json.dumps({"value": [], "@odata.nextLink": None}).encode()
    answers = [b'{"value": [{"id": "AAMk', gzip.compress(good)]
    class R:
        def __init__(s, b): s.b = b
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return s.b
    g = rx.Graph(); g._auth = lambda: "tok"
    orig, sleep = rx.urllib.request.urlopen, rx.time.sleep
    rx.urllib.request.urlopen, rx.time.sleep = (lambda req, timeout=120: R(answers.pop(0))), (lambda s: None)
    try:
        assert g.get("https://graph/x") == {"value": [], "@odata.nextLink": None} and answers == []
        answers[:] = [b"{broken"] * 5
        try:
            g.get("https://graph/x"); assert False
        except RuntimeError as e:
            assert "unreadable after 5 tries" in str(e)
    finally:
        rx.urllib.request.urlopen, rx.time.sleep = orig, sleep

def test_reg_domain():
    assert rx.reg_domain("purchase@mg.firtal.com") == "firtal.com"
    assert rx.reg_domain("a@stiller.co.uk") == "stiller.co.uk"
    assert rx.reg_domain("j@eurobrands.com.pl") == "eurobrands.com.pl"

def test_summary_text():
    rows, _ = rx.build_waits(WF, NOW)
    s = rx.summarise(rows)
    assert "median first response:   96.3 business hours" in s
    assert "FIRST RESPONSE - headline" in s and "sent to a personal mailbox" in s
    assert "answered WITHOUT order@ in copy: 0 of 1" in s
    assert "over 3 days" in s and "Slowest answered" in s and "12.0  2026-09-15" in s
    assert "answered within 1 bday:  0%" in s and "customer had to chase:   1" in s

def test_graph_auth_paging_and_run():
    hits = {"token": 0, "pages": 0}
    page2 = {"value": WF[2:]}
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            hits["token"] += 1
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            assert "grant_type=client_credentials" in body and "client_secret=sec" in body
            self._send({"access_token": "tok", "expires_in": 3600})
        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer tok"
            hits["pages"] += 1
            hits.setdefault("paths", []).append(self.path)
            if "page2" in self.path:
                self._send(page2)
            else:
                assert "%24filter=receivedDateTime%20ge%202026-07-01T00%3A00%3A00Z" in self.path, self.path
                self._send({"value": WF[:2], "@odata.nextLink": f"{rx.GRAPH_BASE}/page2"})
        def _send(self, obj):
            b = json.dumps(obj).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    rx.AUTH_BASE, rx.GRAPH_BASE = base, base
    rx.TENANT_ID, rx.CLIENT_ID, rx.CLIENT_SECRET = "t", "c", "sec"
    tmp = tempfile.mkdtemp(); rx.OUTPUT_DIR = tmp
    try:
        rx.INFER_FROM_FOLLOWUPS = False
        rows, msgs = rx.run(rx.Graph(), now_utc=NOW)               # default: only order@
        rx.INFER_FROM_FOLLOWUPS = True
        assert hits["token"] == 1 and hits["pages"] == 2
        rx.TEAM_REPLY_MAILBOXES = ["frederikke@truecompany.com"]
        rows, msgs = rx.run(rx.Graph(), now_utc=NOW)
        rx.TEAM_REPLY_MAILBOXES = []
        assert hits["pages"] == 6                                   # order@ (2) + Sent Items (2)
        assert any("frederikke%40truecompany.com/mailFolders/sentitems/messages" in p for p in hits["paths"])
        assert len(rows) == 1 and rows[0]["answered"]
        with open(os.path.join(tmp, "response_waits.csv"), encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f, delimiter=";"))
        assert r[0]["business_hours"] == "96.32"
        assert os.path.getsize(os.path.join(tmp, "response_senders.csv")) > 0
        assert os.path.exists(os.path.join(tmp, "response_unanswered_orders.csv"))
        assert os.path.getsize(os.path.join(tmp, "response_distribution_weekly.csv")) > 0
    finally:
        srv.shutdown(); shutil.rmtree(tmp)

if __name__ == "__main__":
    import sys
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e:
                import traceback; fails += 1; print("FAIL", n, repr(e)); traceback.print_exc()
    sys.exit(fails)
