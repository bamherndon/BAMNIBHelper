# Weekly NIB replenishment analysis (autonomous)

You are running unattended from cron. **Nobody can answer questions.** Make the
documented default choice and state what you assumed in the email.

**You have no browser.** The `claude-in-chrome` tools do not exist in a headless
session. Do not try to use them, and do not try to build a cart or place an
order — that is deliberately a human step. Your job is the analysis and the
email.

Follow `.claude/skills/nib-replenishment/SKILL.md` for the math and the
Heartland pull. The deviations below override it.

## Inputs

The wrapper script prepends a RUN CONTEXT block with today's date, the
collection, the catalog JSON path, and whether the catalog fetch succeeded.
**Use those values**; do not re-derive them.

- Target: **2 months** of inventory
- Date window: **Jan 1 of the current year → today**. Compute `--window-days`
  as the real day count.
- Department filter: `New Sets` / `New In Box`

## Sequence

1. **Heartland.** Pull sales + inventory per the skill (Step 1). Use
   `scripts/merge_report.py` and honor its exit code — if the union is short of
   the reported total, pull another sort order. Never proceed on partial data.
   If Heartland is unreachable, send the failure email and stop.
2. **Catalog.** If `catalog_fetch_ok` is 0, send the failure email explaining
   that the storefront could not be read without a session, and stop — a buy
   list without current prices and availability is worse than none. Otherwise
   convert `catalog_json_path` into the CSV shape `build_reorder.py` expects
   (`sku, name, pack_size, case_price, unit_cost, available, tags`). Pack size
   comes from `(Pack of N)` in the title; `price` is the **case** price.
3. **Inbound — deliberately not accounted for.** Open orders on the NIB Shopify
   site are **ignored by this run.** Reading them needs a browser you don't
   have, and Heartland is not a substitute: POs there are created only when a
   shipment is received, so anything ordered-but-not-yet-arrived is invisible to
   it. Do **not** pass `--inbound`, and do **not** scavenge a stale
   `inbound*.json` from the repo — out-of-date inbound data is worse than none,
   because it looks authoritative. Just disclose the gap (see below).
4. **Compute the buy list.** Run `scripts/build_reorder.py --target-months 2`
   with no `--inbound` argument.
5. **Compute the floor sections.** Run
   `.claude/skills/nib-replenishment/scripts/build_sections.py --heartland <merged.json>
   --outdir . --date <YYYY-MM-DD>` (add `--tagged .rundata/redtagged.json` if
   that file exists). This splits slow stock into **Clearance** (nothing sold
   this year) and **Oldie but Goodie** (sold this year, then went quiet). Do
   not re-derive these rules by hand — the thresholds encode decisions that
   were argued out already, including why a set that sold once two years ago
   is Clearance and not an oldie-but-goodie.
6. **Email** the summary to the address in the RUN CONTEXT.
7. **Only after the email is actually sent**, `touch` the `success_marker` path
   from the RUN CONTEXT. The wrapper treats a missing marker as failure no
   matter what you wrote or what exit code it saw, so never create it early,
   and never create it if the email did not go out.

## Email — success

Subject: `NIB replenishment — <YYYY-MM-DD> — <N> to buy, $<total> | <C> clearance, <O> oldie`

Open with two lines, in this order:

1. **The caveat, before any numbers:**
   *"Open orders on the NIB Shopify site are NOT accounted for in this
   analysis. Any set you have already ordered but not yet received will still
   appear below as needed — check your open orders before carting, or you will
   double-order."*
2. What to do: *review and cart these interactively; this job cannot cart for
   you.*

Then:

- **Buy list** — table of SKU, name, units needed, pack size, cases, case price.
  Totals: items, units needed vs units bought by the case, the pack-size
  overshoot, and the dollar total. Call out where the overshoot is severe.
- **Notify Me candidates** — sold-out shortfalls ranked by `units_per_month`,
  with `days_since_last_sold` beside each. Split into **recent demand (<50
  days)**, worth a restock alert, and **stale (>100 days)**, whose annual totals
  came from a burst earlier in the year. Cap at 15 rows; give the full count.
- **CLEARANCE** — SKU, name, units, cost, retail each, and days since last sold
  (or "never"). Mark anything already red-tagged, and say how many of the
  section is already tagged — those have had a markdown that failed, so they
  are the at-cost candidates rather than candidates for another small cut.
  Give section totals (SKUs, units, cost, retail). Call out any **dated**
  product by name (advent calendars, year-stamped sets) — those lose nearly
  all value at year end and are the only genuinely time-critical lines.
- **OLDIE BUT GOODIE** — same columns but show units sold this year and days
  quiet instead of last-sold. Section totals. Note what share of the value sits
  in the top five, since that usually decides how much shelf it deserves.
- If the script held anything back for being under 30 days on the shelf, list
  those separately so nobody thinks they were missed.
- **Caveats** — repeat the open-orders gap in one line, then: how many catalog
  SKUs did not match Heartland (those have no velocity and cannot appear on
  either list, so genuinely new sets are invisible to this run), plus anything
  else that would change the reading.

Attach `clearance_<date>.csv` and `oldie_but_goodie_<date>.csv`. The Gmail tool
takes `attachments: [{filename, mimeType: "text/csv", content: <base64>}]`, so
base64 each file first (`base64 -w0 <file>`) and pass the string as `content`.
If an attachment fails for any reason, still send the email with the tables
inline and say the files could not be attached.

Keep the buy list first — it is time-sensitive because stock sells out. The
two sections are standing work and go below it.

Keep it plain text and skimmable. No preamble.

## Email — failure

Subject: `NIB replenishment FAILED — <YYYY-MM-DD>`

Which step failed, the actual error, and what you did and did not do. Never
substitute stale data to produce a list anyway.

## Rules

- No browser, no cart, no checkout, no login, no payment details.
- Write CSVs to the repo root. They are gitignored — do not commit or push.
