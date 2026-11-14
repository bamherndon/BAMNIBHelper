---
name: nib-replenishment
description: Build a reorder/buy list for a Bricks & Minifigs corporate Shopify collection (bam-nib.myshopify.com) by joining it against Heartland Retail NIB sales velocity and on-hand stock. Use when asked to decide what to order from a corporate preorder/replenishment collection, size an order to N months of inventory, check which sets are selling vs. overstocked, add a computed buy list to the corporate cart, or pick which sold-out sets to set restock ("Notify me") alerts on.
---

# NIB replenishment buy list

Joins a corporate Shopify collection against Heartland sales history and produces
a buy list sized to N months of demand. Default target: **2 months**.

Scripts live in `scripts/` next to this file. Run them with `python3`; no deps.

## Parameters to confirm first

| Thing | Default | Notes |
|---|---|---|
| Collection handle | — | **Ask.** Was `holiday-replenishment-preorder`. Others exist. |
| Target months | 2 | |
| Date window | Jan 1 → today | Sets the velocity denominator |
| Department filter | `New Sets` / `New In Box` | Heartland custom fields |

## Step 1 — Heartland sales + inventory

One `run_report` call per page. Group and metrics:

```
groups:  ["item.public_id", "item.description"]
metrics: ["current_inventory.qty_on_hand", "current_inventory.cost_on_hand",
          "source_sales.net_qty_sold", "source_sales.total_cost",
          "current_inventory.days_since_last_sold",
          "current_inventory.days_since_last_received"]
item_filters: {"$and":[{"custom@department":{"$in":["New Sets"]}},
                       {"custom@sub_department":{"$in":["New In Box"]}}]}
exclude_zeroes: true, include_links: true, per_page: 200
```

`include_links: true` matters — it inflates each row enough that the result
spills to a tool-result file on disk instead of burning context.

**Pagination is the annoying part.** ~841 rows = 5 pages, but a final short page
comes back *inline* and never lands on disk. Don't transcribe it. Instead re-run
the same query with `sort: ["item.public_id,desc"]` — the tail of ascending order
then sits mid-page in a full 200-row file. Union the files:

```bash
python3 scripts/merge_report.py --out merged.json <tool-result files...>
```

It dedupes on the grouping fields and **exits non-zero if the union is smaller
than the `total` the API reported**. If it fails, pull another sort order and
re-run. Do not proceed on a partial pull.

## Step 2 — the corporate collection

Browser only. The sandbox has no outbound network, so `curl` returns
`local_rate_limited`.

Already logged in normally. Navigate to:

```
https://bam-nib.myshopify.com/collections/<HANDLE>/products.json?limit=250
```

That JSON is ~190KB. Three limits bite, in this order:

1. `javascript_tool` truncates its return at roughly 1KB — never return the dump.
2. `get_page_text` caps at 50KB.
3. Results under ~25KB come back inline (context); larger ones persist to a file.

So: build a compact delimited dump **into the DOM**, padded past the persist
threshold, then read it with `get_page_text` so it lands on disk.

```js
const d = await fetch('/collections/<HANDLE>/products.json?limit=250').then(r=>r.json());
const D = '@@';
const rows = d.products.map(p => {
  const v = p.variants[0] || {};
  const m = p.title.match(/\(Pack of (\d+)\)/i);
  const pack = m ? +m[1] : 1;
  const price = parseFloat(v.price || 0);
  return [(v.sku||'').trim(), p.title.replace(/@/g,''), pack, price.toFixed(2),
          (price/pack).toFixed(2), v.available?1:0, v.grams||'',
          (p.tags||[]).join('|'), p.vendor||'', p.product_type||'',
          (p.published_at||'').slice(0,10), p.handle||''].join(D);
});
const csv = ["sku","full_title","pack_size","case_price","unit_cost","available",
             "grams","tags","vendor","product_type","published_at","handle"].join(D)
            + "\n" + rows.join("\n");
document.body.innerHTML = '<pre id="dump"></pre>';
document.getElementById('dump').textContent = csv + "\n###ENDDATA###\n" + "PADPADPAD\n".repeat(7000);
"rows=" + rows.length + " csvchars=" + csv.length;
```

**Use `@@`, not tabs** — `get_page_text` collapses tabs to spaces and the columns
become unparseable. Strip everything after `###ENDDATA###`.

### Collections bigger than ~170 products

`products.json` caps at 250/page, so loop `&page=N` until a short page. And one
dump can't exceed the 50KB `get_page_text` cap — **~172 rows per chunk** is the
proven size (~29KB). Build the full row set once, stash it on `window`, then emit
one chunk per `get_page_text` call:

```js
window.__HDR = <header>; window.__ROWS = all; window.__CHUNK = 172;
// then per chunk i:
const slice = window.__ROWS.slice(i*172, (i+1)*172);
document.body.innerHTML='<pre id="dump"></pre>';
document.getElementById('dump').textContent =
  window.__HDR + "\n" + slice.join("\n") + "\n###ENDDATA###\n" + "PADPADPAD\n".repeat(7000);
```

`window` state survives between `javascript_tool` calls as long as you don't
navigate. Dedupe the reassembled chunks on `sku|handle` and assert the count.

### Which collection?

Handles are not guessable and the collections are **disjoint** — verify, don't
assume one contains another.

| Handle | Count (2026-11-14) | Available | Notes |
|---|---:|---:|---|
| `holiday-replenishment-preorder` | 167 | 106 | Seasonal drop |
| `all-products` | 515 | 34 | Standing catalog, 93% sold out |
| `all` | 847 | — | Shopify built-in; the real superset |

`all-products` ∩ `holiday-replenishment-preorder` = **0 SKUs**. Their union is
682 of the 847 in `all`, so ~165 products live in neither. Use `/collections/all`
for full coverage.

`price` is the **case** price. Per-unit = `price / pack_size`. Pack size only
appears in the title as `(Pack of N)`.

**MSRP and margin % are not in this JSON.** Those badges are rendered by the
storefront theme. Scrape the collection page DOM if they're needed.

## Step 3 — inbound orders

Placed orders live on a different host: `https://shopify.com/70241091760/account/orders`.
`/account/order` (singular) 404s — use `orders`.

**`javascript_tool` is blocked on `shopify.com`** (returns `[BLOCKED: Cookie/query
string data]`). Use `read_page` / `get_page_text` there instead.

Order cards truncate their line items, so open each open order individually to
get full quantities. Quantities are in **cases** — multiply by pack size.

Statuses on the list page have been wrong before; confirm with the user which
orders are genuinely open rather than assuming. Write the result as:

```json
{"10374": 3, "40806": 6, "21366": 2}   // sku -> units inbound
```

## Step 4 — build the list

```bash
python3 scripts/build_reorder.py \
  --heartland merged.json \
  --corp corp_<handle>_<date>.csv \
  --inbound inbound.json \
  --target-months 2 \
  --window-days 317 \
  --out reorder_<date>.csv
```

Math:

```
units_per_month = qty_sold / (window_days / 30.44)
target          = ceil(units_per_month * target_months)
need            = max(0, target - on_hand - inbound)
cases           = ceil(need / pack_size)
```

SKU matching handles Heartland suffixes (`10280-2`, `77258-cert`) and items whose
public_id is an internal number with the set number only in the description
(`108332` → "71050 Spider-Man…"). Expect ~85% match rate; unmatched items are
genuinely new sets.

## Step 5 — cart

Adding to cart is fine on request. **Never check out.** Verify empty first, add
with the AJAX API, then confirm the total matches the computed figure:

```js
const items = BUY.map(s => ({id: variantIdFor(s), quantity: 1}));  // qty = CASES
await fetch('/cart/add.js', {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({items})});
```

Re-check availability right before adding — items sell out mid-session.

## Step 6 — always offer the sold-out list

**After the cart is built, ask the user whether they want to review the sold-out
shortfalls.** Do not skip this and do not wait to be asked. The corporate site
has a "Notify me" button on sold-out products, and the user acts on this list by
signing up for restock alerts — so surfacing it is half the value of the run.
The user handles the Notify-me clicks themselves; just produce the ranked list.

Typically there are far more sold-out shortfalls than buyable ones (253 vs 6 on
2026-11-14), so rank them and cut the tail. Sort by `units_per_month`, but
**print `days_since_last_sold` beside it** — that column decides the list:

- **< ~50 days stale** → live demand, worth a restock alert.
- **> ~100 days stale** → the annual total came from a burst earlier in the
  year; demand may be spent. Say so rather than ranking it high.

Cheap polybag/mini-build multipacks (`30xxx`, 30-packs at ~$3.50–4.00/unit) are
consistently the top performers — flag them first.

## Things that will mislead you

- **`get_item_sales_velocity` is a trap.** Its `avg_qty_per_month` divides by
  *months with sales*, not months in range, so intermittent sellers look fast.
  And `date.month_of_year` is 1–12 with no year, so any range over 12 months
  silently sums Feb-2025 into Feb-2026. Prefer the calendar-window math above.
- **Never derive unit counts from money.** `cost_on_hand / unit_cost` gives
  fractions because your cost basis ≠ current case price. Pull
  `current_inventory.qty_on_hand`.
- **Pack sizes dominate the result.** Most items need 1 unit and ship in 3- or
  4-packs, so a 52-unit need becomes a 112-unit order. Always report units
  needed, units actually bought, and the overshoot — the overshoot is usually
  the real decision.
- **`ceil()` gives anything with a single sale a target of 1.** Use
  `--min-velocity` to cut the long tail.
- **A flat Jan–Nov average understates December.** Say so when the order is
  seasonal; offer recency weighting.
