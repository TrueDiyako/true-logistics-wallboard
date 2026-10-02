# True · Logistics wallboard

A rotating TV wallboard (5 minutes per dashboard) that updates itself:

| # | Dashboard | Data | Refresh |
|---|-----------|------|---------|
| 1 | How are we doing: OTIF, dispatched on planned date, response time, credit notes | TraceLink, Outlook (Graph), e-conomic | nightly |
| 2 | What needs doing now: waiting emails, due-not-ready, credits to check, best/worst customers | all | hourly (customers/credits nightly) |
| 3 | Open orders by status | TraceLink + e-conomic names | hourly |
| 4 | Biggest open orders by revenue | TraceLink, e-conomic, Dachser | hourly |

```
GitHub Actions (schedule) --> jobs/*.py --> Upstash Redis --> Vercel page (/api/data) --> TV
```

Nothing runs on anyone's PC. If a source fails, the TV shows a red banner naming it,
the last good data stays on screen, and GitHub e-mails the repository owner.

## One-time setup (about 20 minutes)

1. **GitHub**: create a private repository, push this folder to it.
2. **Vercel**: *Add New -> Project*, import the repository (framework: Next.js, root: `/`).
3. **Redis**: in the Vercel project, *Storage -> Create -> Upstash for Redis* and connect it
   to the project. Vercel adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` itself. Redeploy once.
4. **GitHub secrets** (*Settings -> Secrets and variables -> Actions -> New repository secret*):

   | Secret | Where it comes from |
   |--------|---------------------|
   | `TRACELINK_TOKEN` | TraceLink API token |
   | `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` | the Microsoft Graph app (needs application permission `Mail.Read`) |
   | `APP_SECRET_TOKEN`, `AGREEMENT_GRANT_TOKEN` | e-conomic API tokens |
   | `DACHSER_API_KEY` | Dachser API key **subscribed to Track & Trace (shipmentstatus)** |
   | `KV_REST_API_URL`, `KV_REST_API_TOKEN` | copy from Vercel -> Storage -> your Redis -> `.env.local` tab |

   Under the *Variables* tab add `GRAPH_SECRET_EXPIRES` = the client secret's expiry date
   (`YYYY-MM-DD`). The TV warns 30 days before it runs out.
5. **First run**: *Actions -> nightly-kpis -> Run workflow*, then *live-orders -> Run workflow*.
   Nightly takes ~15 min the first time (it reads 3 months of TraceLink orders), a few minutes after that.
6. **TV**: open the Vercel URL in the TV browser, full screen. `?d=3` pins one dashboard (for testing).

## Recurring maintenance

- **Microsoft Graph client secret**: renew before it expires (max 24 months) and update the
  `CLIENT_SECRET` secret and `GRAPH_SECRET_EXPIRES` variable. The TV warns 30 days ahead.
- Everything else is automatic. The `keepalive` workflow commits a heartbeat every 3 weeks
  so GitHub never pauses the schedules.

## Definitions (agreed)

- **OTIF**: on time = TraceLink delivery date never moved later than the customer's requested
  date in the original e-conomic order; in full = picked equals ordered per flavour (same flavour on
  another label counts as in full; private label, other flavours, removed lines are short).
  Internal customers (2, 1128, 1129, 3019, 1364), backorders and cancelled orders are out.
  Filled displays picked as loose packs count as in full.
- **Dispatched on planned date**: % of shipments whose delivery note was created on or before the
  TraceLink start date (planned dispatch). Shipments without a delivery note can't be measured and are
  shown as their own share.
- **Response time**: first team reply to a new customer email sent to order@/wholesale@, in
  business days (Mon-Fri 08-16). Automated senders, partners (Stiller, carriers), finance and
  QA topics are out. Panel: % answered within 1 business day (headline + weekly trend), the last 4
  complete weeks split into answer-time bands, and the share of no-reply mails that were orders
  already entered in TraceLink (processed but never confirmed).
- **Credit notes**: credit notes / invoices, internal customers out. "Credits to check" =
  invoices credited more than their own amount.
- Graphs end at the last complete week (the running week would look worse than it is).
- **Dashboard 3**: start date within 14 days (overdue up to 60 days, red); "Shipped - not closed"
  = shipped, still unlocked, start date within the last 2 months. "Pakning" and "-- none --" are
  shown under "Not ready".
- **Dashboard 3 cards** show customer, TraceLink order number and the customer's order number
  (the TraceLink order name).
- **Dashboard 4**: open orders, start date today or later, top 20 by current e-conomic value.
  Delivery = current TraceLink delivery date.
  Picked = picked units / ordered units. Transport = order number found in Dachser track & trace
  (sent via eLogistics); everything else shows "Not booked" (incl. ex-works).

## Customer owners (dashboard 2)

`jobs/customer_owners.csv` maps a sender's e-mail domain to the customer and the responsible
person (from the "Customer - Responsible" list), plus `confirm_required`:
`no` = this customer's mails don't expect an answer (e.g. Helsam POs) and stay out of the KPI.
Add a line for every new customer domain (`domain;customer;responsible;confirm_required`), commit,
and the next run uses it. Senders with no line show as "Unassigned".

**Replies without order@ in copy.** Only order@ is read - no personal mailboxes, no extra IT
permissions. When someone replies from their own mailbox without copying order@, the customer's
follow-up usually shows it: it is addressed to that person and/or quotes their reply
("From: frederikke@truecompany.com ... Sent: 30 September 11:51"). Such waits count as answered
(at the quoted reply time when available) and are reported separately as
"answered without order@ in copy", per person, on dashboard 1 - so the team can see the habit.
Replies the customer never responds to stay invisible. `TEAM_REPLY_MAILBOXES` can optionally read
people's Sent Items too (off by default).

## Changing things

- Rotation, layouts, panels: `components/dashboards.tsx` (`DASHBOARDS`). A new dashboard is one
  entry with its own grid layout.
- Rules and thresholds: constants at the top of `jobs/live.py`, `jobs/nightly.py` and the extractors.
- Run all tests: `cd jobs && for t in test_*.py; do python $t || exit 1; done`
