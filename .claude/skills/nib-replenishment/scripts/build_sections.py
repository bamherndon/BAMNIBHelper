#!/usr/bin/env python3
"""Split slow-moving NIB stock into two floor sections.

  CLEARANCE          nothing sold this year -> price to move, consider at-cost
  OLDIE BUT GOODIE   sold this year then went quiet -> proven demand, re-expose

The distinction is deliberate: "used to sell but stopped" is a re-merchandising
problem, "never sold" is a pricing problem. A set that sold once two years ago
is NOT an oldie-but-goodie -- the test is sales inside the measured window.

Age guards, learned the hard way:
  --min-shelf-days   (30)  nothing that just hit the floor. Note days-since-
                           received resets on a restock, so a long-dead SKU
                           that was just re-received looks brand new.
  --min-dead-days   (150)  a never-sold set needs a full season before it is
                           called dead. This also keeps a recent big drop out
                           of Clearance until it has had its first holiday, and
                           lets it fall in automatically once it ages past the
                           threshold -- no cohort list to maintain.
  --quiet-days      (120)  how long since the last sale before a seller counts
                           as stalled.
  A SKU dead >= 2x quiet-days is treated as dead even if recently re-received.

Usage:
  build_sections.py --heartland merged.json --outdir . [--margin 0.8156]
"""
import argparse, csv, json, os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heartland", required=True, help="merged run_report rows")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--date", default="", help="YYYY-MM-DD for filenames")
    ap.add_argument("--margin", type=float, default=0.8156,
                    help="cost/retail ratio; 0.8156 = 18.4%% margin")
    ap.add_argument("--min-shelf-days", type=int, default=30)
    ap.add_argument("--min-dead-days", type=int, default=150)
    ap.add_argument("--quiet-days", type=int, default=120)
    ap.add_argument("--tagged", default="", help="optional CSV/JSON of already red-tagged SKUs")
    a = ap.parse_args()

    tagged = set()
    if a.tagged and os.path.exists(a.tagged):
        try:
            tagged = set(json.load(open(a.tagged)))
        except Exception:
            tagged = {r["sku"] for r in csv.DictReader(open(a.tagged)) if r.get("sku")}

    clearance, oldie, held = [], [], []
    for r in json.load(open(a.heartland)):
        q = r.get("current_inventory.qty_on_hand") or 0
        if q <= 0:
            continue
        cost = r.get("current_inventory.cost_on_hand") or 0
        sold = r.get("source_sales.net_qty_sold") or 0
        dsls = r.get("current_inventory.days_since_last_sold")
        dslr = r.get("current_inventory.days_since_last_received") or 0

        rec = {
            "sku": r.get("item.public_id"), "name": r.get("item.description"),
            "units": q, "cost_on_hand": round(cost, 2),
            "unit_cost": round(cost / q, 2),
            "retail_ea": round(cost / q / a.margin, 2),
            "retail_total": round(cost / a.margin, 2),
            "sold_ytd": sold,
            "days_since_last_sold": dsls if dsls is not None else "",
            "days_since_received": dslr,
            "already_tagged": "YES" if r.get("item.public_id") in tagged else "",
        }

        long_dead = dsls is not None and dsls >= a.quiet_days * 2
        is_clear = sold == 0 and (dslr >= a.min_dead_days or long_dead)
        is_oldie = sold >= 1 and dsls is not None and dsls >= a.quiet_days
        if not (is_clear or is_oldie):
            continue
        if dslr < a.min_shelf_days:          # just landed; give it a chance
            held.append(rec)
        elif is_clear:
            clearance.append(rec)
        else:
            oldie.append(rec)

    for g in (clearance, oldie, held):
        g.sort(key=lambda x: -x["cost_on_hand"])

    suffix = f"_{a.date}" if a.date else ""
    cols = list((clearance or oldie or held)[0].keys())
    paths = {}
    for name, grp in [("clearance", clearance), ("oldie_but_goodie", oldie)]:
        p = os.path.join(a.outdir, f"{name}{suffix}.csv")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(grp)
        paths[name] = p

    def tot(g, k="cost_on_hand"):
        return sum(x[k] for x in g)

    print(f"CLEARANCE         {len(clearance):>3} SKUs {sum(x['units'] for x in clearance):>4}u  "
          f"${tot(clearance):>9,.2f} cost  ${tot(clearance,'retail_total'):>9,.2f} retail  -> {paths['clearance']}")
    print(f"OLDIE BUT GOODIE  {len(oldie):>3} SKUs {sum(x['units'] for x in oldie):>4}u  "
          f"${tot(oldie):>9,.2f} cost  ${tot(oldie,'retail_total'):>9,.2f} retail  -> {paths['oldie_but_goodie']}")
    print(f"held back (<{a.min_shelf_days}d on shelf): {len(held)} SKUs, ${tot(held):,.2f}")
    for r in held:
        print(f"    {r['sku']:<10}{r['name'][:42]:<44}${r['cost_on_hand']:>8,.2f}  received {r['days_since_received']}d ago")

    for title, grp, extra in [("CLEARANCE", clearance, "lastsold"), ("OLDIE BUT GOODIE", oldie, "sold/quiet")]:
        print(f"\n{title}")
        print(f"  {'sku':<10}{'name':<44}{'u':>3}{'cost$':>9}{'ea$':>9}  {extra}")
        for r in grp:
            tail = (str(r["days_since_last_sold"] or "never") if title == "CLEARANCE"
                    else f"{r['sold_ytd']} sold / {r['days_since_last_sold']}d quiet")
            tag = "  [tagged]" if r["already_tagged"] else ""
            print(f"  {r['sku']:<10}{r['name'][:42]:<44}{r['units']:>3}{r['cost_on_hand']:>9,.2f}"
                  f"{r['retail_ea']:>9,.2f}  {tail}{tag}")


if __name__ == "__main__":
    main()
