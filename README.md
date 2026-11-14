# BAMNIBHelper

Replenishment tooling for a Bricks & Minifigs store. Decides what to reorder from
the corporate NIB Shopify catalog by joining it against Heartland Retail sales
velocity and on-hand inventory.

## What's here

```
.claude/skills/nib-replenishment/
  SKILL.md                  workflow, data sources, and the traps
  scripts/merge_report.py   unions paginated Heartland run_report results
  scripts/build_reorder.py  the join + reorder math
```

Data CSVs are gitignored — they hold wholesale costs and sales figures, and
they're reproducible by re-running the skill.

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
