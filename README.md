# True · Logistics wallboard

A rotating TV wallboard (5 minutes per dashboard) that updates itself:

| # | Dashboard | Data | Refresh |
|---|-----------|------|---------|
| 1 | How are we doing: OTIF, short picks, response time, credit notes | TraceLink, Outlook (Graph), e-conomic | nightly |
| 2 | What needs doing now: waiting emails, due-not-ready, credits to check, best/worst customers | all | hourly (customers/credits nightly) |
| 3 | Open orders by status | TraceLink + e-conomic names | hourly |
| 4 | Biggest open orders by revenue | TraceLink, e-conomic, Dachser | hourly |
| 5 | Production plan: this week on Laudenberg and Bossar, and which orders it covers | Drive plan, TraceLink | hourly |

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

## Hourly trigger (needed - GitHub's own schedule is unreliable)

GitHub delays or drops scheduled runs when it is busy (on 5 Oct only 1 of 13 hourly runs
started). The `live.yml` schedule stays as a backup; the reliable hourly start comes from a
free external cron that presses "Run workflow" through GitHub's API:

1. GitHub -> your avatar -> Settings -> Developer settings -> Personal access tokens ->
   **Fine-grained tokens -> Generate new token**. Name `wallboard-trigger`, expiry 1 year,
   Repository access: **Only select repositories -> true-logistics-wallboard**,
   Permissions -> Repository -> **Actions: Read and write**. Copy the token.
2. cron-job.org (free account) -> **Create cronjob**:
   - URL `https://api.github.com/repos/TrueDiyako/true-logistics-wallboard/actions/workflows/live.yml/dispatches`
   - Schedule: every hour at minute 7, hours 6-18, Monday-Friday, time zone Europe/Copenhagen
   - Advanced -> Request method **POST**, request body `{"ref":"main"}`, headers
     `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`
   - Save and click "Test run": the answer must be **204**, and a "Manually run" appears
     under Actions -> live-orders within a minute.
3. Renew the token before it expires (cron-job.org mails you when calls start failing).

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
- **Short picks**: per shipped order, the share of its ordered cases not picked (per flavour; swaps and
  bag/case differences are not shorts; removed lines are). The line is the weekly average of orders;
  second headline = % of orders with at least one short line.
- **OTIF ranking** groups e-conomic name variants per company (HELSAM A/S + Helsam Helsingør). Orders
  placed in bags (Matas, Heinemann: ordered = 12 x picked) count as in full.
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
- **Dashboard 2, weclapp orders not in TraceLink**: weclapp sales orders created from 6 Oct 2026
  (not cancelled, not the test customer) whose customer PO has no TraceLink order of True Company
  GmbH (1364). TraceLink is searched by "customer | PO" names and by POs listed in batch-order
  descriptions; exact PO first, then a shortened PO ("Order 2" for "Order 2 - Hammer Fitness
  Shop"); one TraceLink entry matches one weclapp order. Refreshed hourly. Needs the GitHub secret
  `WECLAPP_TOKEN`. (The OTIF best/worst customer ranking is still computed nightly into `lk:kpi`
  -> `customers`, just not shown.)
- **Dashboard 4**: open orders, start date today or later, top 20 by current e-conomic value, plus their
  share of all open order value. Delivery = current TraceLink delivery date. Transport = Dachser Track &
  Trace (shipmenthistory): looked up by TraceLink order number (Glostrup bookings) and customer order
  (Hannover bookings) with every key: `DACHSER_API_KEY` (Hannover), `DACHSER_API_KEY_DK` (+ `_DK2`, `_DK3`
  for further Danish logins). States: Not booked / Booked / In transit / Delivered.
  Red "Book now" when not booked 2 working days before the start date: switch on with the repository
  variable `BOOKING_WARNING = 1` once all Danish Dachser logins are visible.
- **Dashboard 5**: plan from "Production plan and Data.xlsx" (Drive, shared by link; comment columns are
  not read). Bags / 12 = cases. Article = language prefix (60 DE/NL, 62 CZ/SK, 63 EN/DK, 64 AU/NZ,
  66 US, 67 CA) + flavour code. New production covers open reservations by start date across both lines;
  current stock is ignored; shipped and internal orders are skipped.
- **Dachser checks**: `jobs/dachser_probe.py` (key check, or `python dachser_probe.py <reference>`) and
  `jobs/dachser_backtest.py` (90 days of bookings), plus the manual GitHub workflow "dachser-probe".
  Picked = picked units / ordered units. Transport = order number found in Dachser track & trace
  (sent via eLogistics); everything else shows "Not booked" (incl. ex-works).

## Customer owners (dashboard 2)

`jobs/customer_owners.csv` maps a sender's e-mail domain to the customer and the responsible
person (from the "Customer - Responsible" list), plus `confirm_required`:
`no` = this customer's mails don't expect an answer (e.g. Helsam POs) and stay out of the KPI.
Add a line for every new customer domain (`domain;customer;responsible;confirm_required`), commit,
and the next run uses it. Senders with no line show as "Unassigned".

**Replies in a new thread** (new subject, possibly to a sister domain of the same
customer, e.g. humblegroup.com for a PO from humblegroupusa.com) count when the team mail
goes to the same company (customer list, else domain) and either mentions the PO/order
reference of the customer's mail in its subject or opening text (within 10 days), or is
sent within 1 business day of it (any subject). Customers marked `confirm_required = no`
(Helsam, Coop, Green Sales) stay out of the KPI.

**Credits to check** = chains of invoices and credit notes (same customer and heading or
order reference) whose total balance is negative. A heading that was invoiced, credited and
re-invoiced several times but nets positive (EUROBRANDS EB51) still counts in the credit KPI
but is not "to check".

**Replies without order@ in copy.** Only order@ is read - no personal mailboxes, no extra IT
permissions. When someone replies from their own mailbox without copying order@, the customer's
follow-up usually shows it: it is addressed to that person and/or quotes their reply
("From: frederikke@truecompany.com ... Sent: 30 September 11:51"). Such waits count as answered
(at the quoted reply time when available) and are reported separately as
"answered without order@ in copy", per person, on dashboard 1 - so the team can see the habit.
Replies the customer never responds to stay invisible. `TEAM_REPLY_MAILBOXES` can optionally read
people's Sent Items too (off by default).

## Changing things

- Rotation (5 dashboards, 5 minutes each), layouts, panels: `components/dashboards.tsx` (`DASHBOARDS`). A new dashboard is one
  entry with its own grid layout.
- Rules and thresholds: constants at the top of `jobs/live.py`, `jobs/nightly.py` and the extractors.
- Run all tests: `cd jobs && for t in test_*.py; do python $t || exit 1; done`
