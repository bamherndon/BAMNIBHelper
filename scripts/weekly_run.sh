#!/usr/bin/env bash
# Weekly NIB replenishment analysis. Invoked by cron; see `crontab -l`.
#
# Headless Claude cannot drive Chrome (the claude-in-chrome tools are injected
# by the browser extension into interactive sessions only, and are not a
# configured MCP server). So this script does the catalog fetch itself with
# curl -- cron runs it as a plain shell, outside any tool sandbox -- and hands
# the JSON to Claude along with the Heartland pull.
#
# It therefore produces the ANALYSIS and emails it. Building the cart still
# requires an interactive session with the browser extension.

set -uo pipefail

REPO="/home/heman/git/BAMNIBHelper"
PROMPT="$REPO/scripts/weekly_prompt.md"
LOGDIR="$REPO/.logs"
DATADIR="$REPO/.rundata"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$LOGDIR/weekly_$STAMP.log"
MAILTO="herndon.va@bricksandminifigs.com"
COLLECTION="all"
STORE="https://bam-nib.myshopify.com"

mkdir -p "$LOGDIR" "$DATADIR"
ls -1t "$LOGDIR"/weekly_*.log 2>/dev/null | tail -n +13 | xargs -r rm -f

export PATH="/home/heman/.nvm/versions/node/v24.18.0/bin:$PATH"
cd "$REPO" || exit 1

exec 2>>"$LOG"
echo "=== NIB replenishment run $STAMP ===" >>"$LOG"
date >>"$LOG"

# ---- 1. Fetch the catalog -------------------------------------------------
RAW="$DATADIR/corp_${COLLECTION}_$(date +%F).json"
FETCH_OK=1
FETCH_NOTE=""
TOTAL=0

# An interactive session (which has the browser) can pre-stage a parsed catalog
# CSV; CATALOG_CSV=<path> then skips the curl entirely. Also how you re-run the
# analysis without re-scraping.
if [ -n "${CATALOG_CSV:-}" ] && [ -r "${CATALOG_CSV}" ]; then
  TOTAL=$(($(wc -l <"$CATALOG_CSV") - 1))
  RAW="$CATALOG_CSV"
  FETCH_NOTE="pre-staged catalog CSV supplied via CATALOG_CSV (curl skipped)"
  echo "using pre-staged catalog: $RAW ($TOTAL rows)" >>"$LOG"
else
: >"$RAW.tmp"
for PAGE in 1 2 3 4 5 6 7 8; do
  BODY="$DATADIR/.page$PAGE.json"
  CODE=$(curl -sS -m 60 -o "$BODY" -w '%{http_code}' \
    -H 'Accept: application/json' \
    "$STORE/collections/$COLLECTION/products.json?limit=250&page=$PAGE")
  if [ "$CODE" != "200" ]; then
    FETCH_OK=0
    FETCH_NOTE="catalog fetch failed on page $PAGE with HTTP $CODE (the storefront may require an authenticated session)"
    break
  fi
  N=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['products']))" "$BODY" 2>/dev/null || echo 0)
  TOTAL=$((TOTAL + N))
  cat "$BODY" >>"$RAW.tmp"
  echo >>"$RAW.tmp"
  [ "$N" -lt 250 ] && break
done

if [ "$FETCH_OK" = "1" ] && [ "$TOTAL" -gt 0 ]; then
  python3 - "$RAW.tmp" "$RAW" <<'PY'
import json, sys
prods = []
for line in open(sys.argv[1]):
    line = line.strip()
    if line:
        prods.extend(json.loads(line)["products"])
seen, out = set(), []
for p in prods:
    if p["id"] not in seen:
        seen.add(p["id"]); out.append(p)
json.dump({"products": out}, open(sys.argv[2], "w"))
print(f"catalog: {len(out)} products -> {sys.argv[2]}")
PY
else
  FETCH_OK=0
fi
rm -f "$RAW.tmp" "$DATADIR"/.page*.json
fi
echo "fetch_ok=$FETCH_OK total=$TOTAL note=$FETCH_NOTE" >>"$LOG"

# ---- 2. Hand off to Claude ------------------------------------------------
TOOLS=(
  Bash Read Write Edit Glob Grep
  mcp__heartland-retail__run_report
  mcp__heartland-retail__list_locations
  mcp__claude_ai_Gmail__send_message
)

CONTEXT="RUN CONTEXT (authoritative, overrides guesses):
- today: $(date +%F)
- collection: $COLLECTION
- catalog_fetch_ok: $FETCH_OK
- catalog_product_count: $TOTAL
- catalog_json_path: $RAW
- catalog_fetch_note: ${FETCH_NOTE:-none}
- email_to: $MAILTO
- log: $LOG
"

timeout 45m claude -p "$CONTEXT

$(cat "$PROMPT")" \
  --allowedTools "${TOOLS[@]}" \
  --permission-mode acceptEdits \
  >>"$LOG" 2>&1
RC=$?
echo "=== exit $RC ===" >>"$LOG"

# ---- 3. Fail loudly -------------------------------------------------------
if [ $RC -ne 0 ]; then
  TAIL="$(tail -c 3000 "$LOG")"
  timeout 5m claude -p "Send a plain-text email via Gmail to $MAILTO.
Subject: NIB replenishment FAILED — $(date +%F)
Body: The scheduled weekly run exited with code $RC (124 = hit the 45-minute
timeout). Catalog fetch ok: $FETCH_OK. No cart was built -- this job never
builds carts. Full log: $LOG

Last output:
$TAIL

Send the email and nothing else." \
    --allowedTools mcp__claude_ai_Gmail__send_message \
    >>"$LOG" 2>&1
fi

exit $RC
