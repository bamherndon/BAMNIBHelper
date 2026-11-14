#!/usr/bin/env python3
"""Join a corporate Shopify collection against Heartland sales history and
emit a reorder list sized to N months of demand.

Inputs
  --heartland  merged run_report rows (from merge_report.py). Needs metrics:
               current_inventory.qty_on_hand, current_inventory.cost_on_hand,
               source_sales.net_qty_sold, source_sales.total_cost,
               current_inventory.days_since_last_sold
  --corp       collection dump: '@@'-delimited or comma CSV, needs columns
               sku, pack_size, case_price, available  (+ name/full_title)
  --inbound    optional JSON {"sku": units_already_on_the_way}
  --window-days  calendar days the sales figures cover (denominator)

Velocity is units/month over the FULL calendar window:
    units_per_month = qty_sold / (window_days / 30.44)
Do not divide by "months with sales" -- that overstates intermittent sellers.

    target  = ceil(units_per_month * target_months)
    need    = max(0, target - on_hand - inbound)
    cases   = ceil(need / pack_size)
"""
import argparse, csv, json, math, re, sys


def read_corp(path):
    raw = open(path, encoding="utf-8").read()
    # get_page_text collapses tabs to spaces, so dumps use '@@' as delimiter
    if "@@" in raw.splitlines()[0]:
        lines = [l for l in raw.split("\n") if l.strip()]
        hdr = lines[0].split("@@")
        recs = [dict(zip(hdr, l.split("@@"))) for l in lines[1:]
                if len(l.split("@@")) == len(hdr)]
    else:
        recs = list(csv.DictReader(open(path, encoding="utf-8")))
    out = []
    for r in recs:
        title = r.get("name") or r.get("full_title") or ""
        pack = r.get("pack_size")
        if not pack:
            m = re.search(r"\(Pack of (\d+)\)", title, re.I)
            pack = m.group(1) if m else 1
        name = re.sub(r"\s*\(Pack of \d+\)\s*", "", title, flags=re.I)
        # Strip a leading "<setnum> - " only. Must start with a digit, or this
        # eats real names ("BB-8 Astromech Droid", "Rides - Fire Truck").
        name = re.sub(r"^\s*\d[\w-]*\s*[-–]\s+", "", name).strip()
        out.append({"sku": (r.get("sku") or "").strip(), "name": name,
                    "pack_size": int(pack), "case_price": float(r["case_price"]),
                    "available": int(r.get("available", 1)),
                    "tags": r.get("tags", "")})
    return out


def setnums(pid, desc):
    """Heartland SKUs carry suffixes (10280-2, 77258-cert) and some items use an
    internal id whose set number only appears in the description."""
    out = {pid, pid.split("-")[0]} if pid else set()
    m = re.match(r"\s*(\d{4,6})\b", desc or "")
    if m:
        out.add(m.group(1))
    return {k for k in out if k}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heartland", required=True)
    ap.add_argument("--corp", required=True)
    ap.add_argument("--inbound")
    ap.add_argument("--target-months", type=float, default=2.0)
    ap.add_argument("--window-days", type=float, required=True)
    ap.add_argument("--min-velocity", type=float, default=0.0,
                    help="drop items selling slower than this (units/month)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    months = a.window_days / 30.44
    inbound = json.load(open(a.inbound)) if a.inbound else {}

    agg = {}
    for r in json.load(open(a.heartland)):
        pid = (r.get("item.public_id") or "").strip()
        for k in setnums(pid, r.get("item.description")):
            x = agg.setdefault(k, {"qty": 0, "cogs": 0.0, "units": 0,
                                   "cost": 0.0, "last_sold": None, "skus": set()})
            x["qty"] += r.get("source_sales.net_qty_sold") or 0
            x["cogs"] += r.get("source_sales.total_cost") or 0
            x["units"] += r.get("current_inventory.qty_on_hand") or 0
            x["cost"] += r.get("current_inventory.cost_on_hand") or 0
            s = r.get("current_inventory.days_since_last_sold")
            if s is not None:
                x["last_sold"] = s if x["last_sold"] is None else min(x["last_sold"], s)
            x["skus"].add(pid)

    rows = []
    for c in read_corp(a.corp):
        h = agg.get(c["sku"])
        qty = h["qty"] if h else 0
        upm = round(qty / months, 2)
        oh = h["units"] if h else 0
        inb = int(inbound.get(c["sku"], 0))
        target = math.ceil(upm * a.target_months)
        need = 0 if upm < a.min_velocity else max(0, target - oh - inb)
        cases = math.ceil(need / c["pack_size"]) if need else 0
        rows.append({
            "sku": c["sku"], "name": c["name"],
            "corp_available": c["available"],
            "units_per_month": upm, "qty_sold_ytd": qty,
            "target_units": target, "units_on_hand": oh, "units_inbound": inb,
            "units_needed": need,
            "pack_size": c["pack_size"], "cases_to_order": cases,
            "units_if_ordered": cases * c["pack_size"],
            "overshoot_units": cases * c["pack_size"] - need,
            "case_price": c["case_price"], "order_cost": round(cases * c["case_price"], 2),
            "cost_on_hand": round(h["cost"], 2) if h else 0.0,
            "days_since_last_sold": h["last_sold"] if h and h["last_sold"] is not None else "",
            "matched_heartland": 1 if h else 0, "tags": c["tags"],
        })

    rows.sort(key=lambda r: (-r["units_needed"], -r["units_per_month"]))
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    buy = [r for r in rows if r["units_needed"] > 0 and r["corp_available"]]
    gone = [r for r in rows if r["units_needed"] > 0 and not r["corp_available"]]
    print(f"wrote {a.out}")
    print(f"collection items={len(rows)}  matched to Heartland={sum(r['matched_heartland'] for r in rows)}")
    print(f"BUY  : {len(buy)} items, {sum(r['units_needed'] for r in buy)} units short, "
          f"{sum(r['units_if_ordered'] for r in buy)} units by the case, "
          f"${sum(r['order_cost'] for r in buy):,.2f} "
          f"({sum(r['overshoot_units'] for r in buy)} overshoot)")
    print(f"SHORT BUT SOLD OUT: {len(gone)} items, {sum(r['units_needed'] for r in gone)} units")
    print(f"\n{'sku':<8}{'name':<38}{'u/mo':>6}{'tgt':>5}{'oh':>4}{'in':>4}{'need':>6}{'pack':>5}{'cases':>6}{'cost$':>9}")
    for r in buy:
        print(f"{r['sku']:<8}{r['name'][:36]:<38}{r['units_per_month']:>6.2f}{r['target_units']:>5}"
              f"{r['units_on_hand']:>4}{r['units_inbound']:>4}{r['units_needed']:>6}"
              f"{r['pack_size']:>5}{r['cases_to_order']:>6}{r['order_cost']:>9.2f}")


if __name__ == "__main__":
    main()
