#!/usr/bin/env python3
"""Merge paginated Heartland run_report results into one deduped JSON array.

run_report caps at 200 rows/page and large results get spilled to tool-result
files. Pages that come back inline are NOT on disk, so the reliable way to get
every row is to issue the same query under two different sorts (so the tail of
one ordering lands mid-page in the other) and union the files.

Usage:
    merge_report.py --out merged.json FILE [FILE ...]
    merge_report.py --out merged.json --dir <tool-results-dir> --since-minutes 30

Exits non-zero if the union is smaller than the "total" the API reported.
"""
import argparse, glob, json, os, sys, time


def group_key(row):
    """Dedupe on the grouping dimensions (every non-metric field)."""
    k = tuple(sorted((f, v) for f, v in row.items() if "." in f and
                     not f.startswith(("source_sales.", "current_inventory."))))
    return k or tuple(sorted(row.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="scan this dir for run_report result files")
    ap.add_argument("--since-minutes", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    files = list(a.files)
    if a.dir:
        cutoff = time.time() - a.since_minutes * 60
        files += [f for f in glob.glob(os.path.join(a.dir, "*run_report*"))
                  if os.path.getmtime(f) >= cutoff]
    if not files:
        sys.exit("no input files")

    rows, total = {}, None
    for f in sorted(set(files)):
        try:
            data = json.load(open(f))
        except Exception as e:
            print(f"  skip {os.path.basename(f)}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict) or "results" not in data:
            continue
        total = data.get("total", total)
        for r in data["results"]:
            rows[group_key(r)] = r
        print(f"  {os.path.basename(f)}: {len(data['results'])} rows", file=sys.stderr)

    out = list(rows.values())
    json.dump(out, open(a.out, "w"))
    print(f"merged {len(out)} unique rows (API reported total={total}) -> {a.out}")
    if total is not None and len(out) < total:
        sys.exit(f"INCOMPLETE: missing {total - len(out)} rows. "
                 f"Re-run the query under another sort and add that file.")


if __name__ == "__main__":
    main()
