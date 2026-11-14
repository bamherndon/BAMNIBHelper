# BAMNIBHelper

Replenishment tooling for a Bricks & Minifigs store. Decides what to reorder from
the corporate NIB Shopify catalog by joining it against Heartland Retail sales
velocity and on-hand inventory.

## Prerequisites

- **[HeartlandMCP](https://github.com/bamherndon/HeartlandMCP)** — required.
  All sales velocity and on-hand inventory data comes from it.
- Claude Code with Chrome browser automation, for the corporate Shopify side.

## What's here

```
.claude/skills/nib-replenishment/
  SKILL.md                  workflow, data sources, and the traps
  scripts/merge_report.py   unions paginated Heartland run_report results
  scripts/build_reorder.py  the join + reorder math
```

Data CSVs are gitignored — they hold wholesale costs and sales figures, and
they're reproducible by re-running the skill.

## Scheduled run

A crontab entry runs `scripts/weekly_run.sh` on Sundays at 1:07pm ET. It pulls
Heartland, fetches the catalog, computes the buy list, and emails it.

Two things it deliberately does **not** do, because a headless session has no
browser:

- **Build the cart.** That stays an interactive step.
- **Account for open orders on the NIB Shopify site.** Already-ordered sets will
  still show as needed. Heartland is not a fallback here — POs are only created
  there once a shipment is received. The email leads with this caveat.

On failure it emails a `FAILED` notice rather than going quiet.

## The math

```
units_per_month = qty_sold / (window_days / 30.44)
target          = ceil(units_per_month * target_months)   # default 2 months
need            = max(0, target - on_hand - inbound)
cases           = ceil(need / pack_size)
```

Velocity is measured over the full calendar window, not over "months with
sales" — the latter overstates intermittent sellers. Corporate sells by the
case, so `need` and `cases * pack_size` usually differ a lot; the overshoot is
normally the real decision.

## Usage

```bash
python3 .claude/skills/nib-replenishment/scripts/merge_report.py \
  --out merged.json <heartland-run_report-files...>

python3 .claude/skills/nib-replenishment/scripts/build_reorder.py \
  --heartland merged.json \
  --corp corp_<collection>_<date>.csv \
  --inbound inbound.json \
  --target-months 2 --window-days 317 \
  --out reorder_<date>.csv
```

See `SKILL.md` for how to pull each input.
