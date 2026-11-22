#!/usr/bin/env bash
# Weekly NIB replenishment analysis.
#
# Invoked HOURLY by cron; it decides for itself whether a run is due. This
# machine is a laptop that is frequently asleep, so a plain "Sundays at 1pm"
# entry does not work:
#   * 2026-11-15 the job fired 47s after wake, 75s before wifi associated --
#     curl got HTTP 000 and the remote Gmail connector could not initialise.
#   * 2026-11-22 the machine was asleep at 13:07 and cron simply never fired.
# So: run if due (catch-up), and wait for the network before doing anything.

set -uo pipefail

REPO="/home/heman/git/BAMNIBHelper"
PROMPT="$REPO/scripts/weekly_prompt.md"
LOGDIR="$REPO/.logs"
DATADIR="$REPO/.rundata"
STAMPFILE="$DATADIR/last_success"
LOCK="$DATADIR/.lock"
MAILTO="herndon.va@bricksandminifigs.com"
COLLECTION="all"
STORE="https://bam-nib.myshopify.com"
NET_WAIT_SECS=600          # how long to wait for the network after waking
RUN_HOUR=13                # Sunday 1pm local, the intended slot
STALE_DAYS=8               # hard catch-up if we have missed a whole cycle

mkdir -p "$LOGDIR" "$DATADIR"

# Never let two runs overlap (hourly cron + a long run).
exec 9>"$LOCK"
flock -n 9 || exit 0

# ---- is a run due? --------------------------------------------------------
now=$(date +%s)
last=0
[ -r "$STAMPFILE" ] && last=$(cat "$STAMPFILE" 2>/dev/null || echo 0)
age_days=$(( (now - last) / 86400 ))
dow=$(date +%u)   # 7 = Sunday
hour=$(date +%H)
today_start=$(date -d "today 00:00" +%s)

due=0
if [ "$dow" = "7" ] && [ "$((10#$hour))" -ge "$RUN_HOUR" ] && [ "$last" -lt "$today_start" ]; then
  due=1; reason="scheduled Sunday slot"
elif [ "$age_days" -ge "$STALE_DAYS" ]; then
  due=1; reason="catch-up: ${age_days}d since last success"
fi
[ "${FORCE_RUN:-0}" = "1" ] && { due=1; reason="FORCE_RUN"; }
[ "$due" = "1" ] || exit 0

STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$LOGDIR/weekly_$STAMP.log"
ls -1t "$LOGDIR"/weekly_*.log 2>/dev/null | tail -n +13 | xargs -r rm -f
exec 2>>"$LOG"
echo "=== run $STAMP ($reason) ===" >>"$LOG"

export PATH="/home/heman/.nvm/versions/node/v24.18.0/bin:$PATH"
cd "$REPO" || exit 1

# ---- wait for the network -------------------------------------------------
# Cron can fire seconds after a lid-open, before wifi has a lease.
net_ok=0
waited=0
[ "${SKIP_NET_CHECK:-0}" = "1" ] && { net_ok=1; NET_WAIT_SECS=0; }
while [ "$waited" -lt "$NET_WAIT_SECS" ]; do
  if curl -sS -m 10 -o /dev/null "$STORE/collections/$COLLECTION/products.json?limit=1" 2>/dev/null; then
    net_ok=1; break
  fi
  sleep 15; waited=$((waited + 15))
done
echo "network_ok=$net_ok after ${waited}s" >>"$LOG"
if [ "$net_ok" = "0" ]; then
  echo "ABORT: no network after ${NET_WAIT_SECS}s; leaving stamp untouched so this retries next hour" >>"$LOG"
  exit 1
fi

# ---- fetch the catalog ----------------------------------------------------
RAW="$DATADIR/corp_${COLLECTION}_$(date +%F).json"
FETCH_OK=1; FETCH_NOTE=""; TOTAL=0

if [ -n "${CATALOG_CSV:-}" ] && [ -r "${CATALOG_CSV}" ]; then
  TOTAL=$(($(wc -l <"$CATALOG_CSV") - 1)); RAW="$CATALOG_CSV"
  FETCH_NOTE="pre-staged catalog CSV via CATALOG_CSV (curl skipped)"
else
  : >"$RAW.tmp"
  for PAGE in 1 2 3 4 5 6 7 8; do
    BODY="$DATADIR/.page$PAGE.json"; CODE=000
    for try in 1 2 3; do
      CODE=$(curl -sS -m 60 -o "$BODY" -w '%{http_code}' -H 'Accept: application/json' \
        "$STORE/collections/$COLLECTION/products.json?limit=250&page=$PAGE") && [ "$CODE" = "200" ] && break
      sleep $((try * 10))
    done
    if [ "$CODE" != "200" ]; then
      FETCH_OK=0; FETCH_NOTE="catalog fetch failed on page $PAGE (HTTP $CODE) after 3 tries"; break
    fi
    N=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['products']))" "$BODY" 2>/dev/null || echo 0)
    TOTAL=$((TOTAL + N)); cat "$BODY" >>"$RAW.tmp"; echo >>"$RAW.tmp"
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
PY
  else
    FETCH_OK=0
  fi
  rm -f "$RAW.tmp" "$DATADIR"/.page*.json
fi
echo "fetch_ok=$FETCH_OK total=$TOTAL note=$FETCH_NOTE" >>"$LOG"

# ---- run the analysis -----------------------------------------------------
# Claude must touch this file as its last action; its absence means failure,
# regardless of what the model wrote in prose or what exit code we got.
OKMARK="$DATADIR/.ok_$STAMP"

TOOLS=(
  Bash Read Write Edit Glob Grep
  mcp__heartland-retail__run_report mcp__heartland-retail__list_locations
  mcp__claude_ai_Gmail__send_message
)

CONTEXT="RUN CONTEXT (authoritative):
- today: $(date +%F)
- collection: $COLLECTION
- catalog_fetch_ok: $FETCH_OK
- catalog_product_count: $TOTAL
- catalog_json_path: $RAW
- catalog_fetch_note: ${FETCH_NOTE:-none}
- email_to: $MAILTO
- log: $LOG
- success_marker: $OKMARK
  After the email is SENT, and only then, run: touch $OKMARK
"

timeout 45m claude -p "$CONTEXT

$(cat "$PROMPT")" \
  --allowedTools "${TOOLS[@]}" --permission-mode acceptEdits >>"$LOG" 2>&1
RC=$?

if [ -f "$OKMARK" ]; then
  rm -f "$OKMARK"
  echo "$now" >"$STAMPFILE"
  echo "=== SUCCESS (rc=$RC) ===" >>"$LOG"
  exit 0
fi

# ---- fail loudly ----------------------------------------------------------
echo "=== FAILED (rc=$RC, no success marker) ===" >>"$LOG"
TAIL="$(tail -c 3000 "$LOG")"
timeout 5m claude -p "Send a plain-text email via Gmail to $MAILTO.
Subject: NIB replenishment FAILED — $(date +%F)
Body: The weekly run did not complete. Exit code $RC (124 = 45-minute timeout).
Catalog fetch ok: $FETCH_OK. No cart was built -- this job never builds carts.
It will retry automatically. Log: $LOG

Last output:
$TAIL

Send the email and nothing else." \
  --allowedTools mcp__claude_ai_Gmail__send_message >>"$LOG" 2>&1

# Stamp is deliberately NOT updated, so the catch-up path retries.
exit 1
