#!/usr/bin/env bash
# Full autonomous pipeline: second-wave queries for every district -> phone
# retry passes -> multi-pass email sweep -> final exports -> sync to phone.
#
# Designed to be left running unattended:
#   setsid nohup ./finalize.sh > output/logs/finalize.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
LOG=output/logs
mkdir -p "$LOG"
DISTRICTS=(Kozhikode Wayanad Kasaragod Kannur Malappuram Palakkad Thrissur Ernakulam Idukki Kottayam Alappuzha Pathanamthitta Kollam Thiruvananthapuram)

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ---- 1. second-wave queries (capped localities get alternate wordings) ----
log "PHASE 1: second-wave queries, all 14 districts, 7 workers at a time"
pids=()
for d in "${DISTRICTS[@]}"; do
  $PY -u -m resorts_scraper.cli run --district "$d" \
      --requeue-caps --no-details --no-emails --delay 2 \
      > "$LOG/secondwave_${d}.log" 2>&1 &
  pids+=($!)
  sleep 2
  # throttle: at most 7 concurrent Google workers
  while [ "$(pgrep -cf 'cli run --district')" -ge 7 ]; do sleep 10; done
done
for p in "${pids[@]}"; do wait "$p"; done
log "PHASE 1 done: $(grep -hc '^\[' $LOG/secondwave_*.log | paste -sd+ | bc) new queries attempted"

# ---- 2. phone retry passes (sharded, high attempt cap) ----
log "PHASE 2: phone passes, shards 1-4, detail-tries 8"
for i in 1 2 3 4; do
  $PY -u -m resorts_scraper.cli phones --shard $i/4 --workers 6 \
      --detail-tries 8 --delay 0.3 > "$LOG/finalphones_$i.log" 2>&1 &
done
wait
log "PHASE 2 done: $(status=$($PY -m resorts_scraper.cli status 2>/dev/null | grep '^with phone'); echo "$status")"

# ---- 3. email sweep, repeat until no site is left or 4 passes ----
log "PHASE 3: email sweep (loop)"
for pass in 1 2 3 4; do
  todo=$($PY -c "import sys; sys.path.insert(0,'src')
from resorts_scraper.store import Store
print(len(Store('checkpoints').missing_emails()))")
  log "  pass $pass: $todo sites remaining"
  [ "$todo" -eq 0 ] && break
  for i in 1 2 3 4 5 6; do
    $PY -u -m resorts_scraper.cli emails --shard $i/6 --email-workers 10 \
        > "$LOG/finalemails_${pass}_$i.log" 2>&1 &
  done
  wait
done
log "PHASE 3 done"

# ---- 4. final exports + stats ----
log "PHASE 4: final exports"
$PY -m resorts_scraper.cli export -o output/resorts_report.csv          > "$LOG/export_raw.log" 2>&1
$PY -m resorts_scraper.cli export --clean -o output/kerala_resorts.csv  > "$LOG/export_clean.log" 2>&1
$PY - > output/FINAL_STATS.md <<'PYEOF'
import csv, collections, sys, datetime
sys.path.insert(0, 'src')
from resorts_scraper.store import Store
from resorts_scraper.clean import filter_places

s = Store('checkpoints')
raw = s.all_places()
kept, rep = filter_places(raw)
import json
print("# Kerala resorts - final report")
print()
print(f"Generated: {datetime.date.today().isoformat()}")
print()
print("## Headline numbers")
print()
print("| metric | value |")
print("|---|---|")
n = lambda f: sum(1 for p in kept if f(p))
print(f"| places discovered (raw) | {len(raw)} |")
print(f"| Kerala resorts (filtered) | {len(kept)} |")
print(f"| districts covered | {len(set(p.district for p in kept))} |")
print(f"| with phone | {n(lambda p: p.phone)} ({n(lambda p: p.phone)*100//max(len(kept),1)}%) |")
print(f"| with website | {n(lambda p: p.website)} ({n(lambda p: p.website)*100//max(len(kept),1)}%) |")
print(f"| with email | {n(lambda p: p.email)} ({n(lambda p: p.email)*100//max(len(kept),1)}%) |")
print(f"| with description | {n(lambda p: p.description)} ({n(lambda p: p.description)*100//max(len(kept),1)}%) |")
print(f"| with rating | {n(lambda p: p.rating)} ({n(lambda p: p.rating)*100//max(len(kept),1)}%) |")
print()
print("## Queries")
print()
c = collections.Counter(r.get('status') for r in s.queries.values())
print(f"Total {len(s.queries)}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
print()
print("## Filter breakdown (raw -> kept)")
print()
print("| filter | dropped |")
print("|---|---|")
for k in ("drop_outside_state_geo", "drop_other_state_address", "drop_not_lodging", "drop_duplicate", "drop_no_coords"):
    print(f"| {k.replace('drop_','')} | {rep[k]} |")
print()
print("## By district")
print()
print("| district | resorts | phones | emails |")
print("|---|---|---|---|")
bd = collections.defaultdict(list)
for p in kept: bd[p.district].append(p)
for d in sorted(bd, key=lambda d: -len(bd[d])):
    ps = bd[d]
    print(f"| {d} | {len(ps)} | {sum(1 for p in ps if p.phone)} | {sum(1 for p in ps if p.email)} |")
print()
print("## Top categories")
print()
for t, c in collections.Counter(p.title for p in kept).most_common(8):
    print(f"- {t}: {c}")
print()
print("## Files")
print()
print("- `output/kerala_resorts.csv` - filtered, the deliverable")
print("- `output/resorts_report.csv` - raw, unfiltered")
PYEOF
log "PHASE 4 done: $(wc -l < output/kerala_resorts.csv) lines in final CSV"

# ---- 5. sync both CSVs + stats + checkpoints to the phone ----
log "PHASE 5: syncing to phone"
if [ -n "${SSHPASS:-}" ]; then
  tar czf /tmp/final_sync.tgz \
    output/kerala_resorts.csv output/resorts_report.csv output/FINAL_STATS.md \
    checkpoints README.md docs 2>/dev/null
  SSHPASS="$SSHPASS" sshpass -e scp -P 8022 -o StrictHostKeyChecking=no \
    /tmp/final_sync.tgz u0_a318@10.70.22.197:/data/data/com.termux/files/home/final_sync.tgz \
    && SSHPASS="$SSHPASS" sshpass -e ssh -p 8022 -o StrictHostKeyChecking=no u0_a318@10.70.22.197 \
    'unset LD_PRELOAD; cd /data/data/com.termux/files/home/storage/resorts-scraper && tar xzf ../../final_sync.tgz && rm ../../final_sync.tgz && echo PHONE_SYNC_OK'
  rm -f /tmp/final_sync.tgz
else
  log "SSHPASS not set - skipping phone sync"
fi
log "ALL PHASES COMPLETE"
$PY -m resorts_scraper.cli status 2>/dev/null | head -5
