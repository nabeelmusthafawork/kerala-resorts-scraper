#!/usr/bin/env bash
# Run districts as parallel workers, one process each.
#
# Why: districts are independent query sets, and every write is an append to
# the same checkpoints/*.jsonl which Store merges by feature id on load, so
# concurrent processes cannot lose each other's work. Google sees --delay
# seconds per worker, not per run.
#
# Usage: ./run-districts.sh [district ...]      (default: all 14)
set -uo pipefail
cd "$(dirname "$0")"

DISTRICTS=("$@")
if [ ${#DISTRICTS[@]} -eq 0 ]; then
  mapfile -t DISTRICTS < <(.venv/bin/python -c "
import sys; sys.path.insert(0,'src')
from resorts_scraper.planner import Planner
print('\n'.join(Planner('config/districts.yaml').districts()))
")
fi

mkdir -p output/logs
PY=.venv/bin/python
pids=()

for d in "${DISTRICTS[@]}"; do
  log="output/logs/${d,,}.log"
  $PY -u -m resorts_scraper.cli run \
      --district "$d" \
      --delay "${DELAY:-2}" \
      --workers "${WORKERS:-4}" \
      --email-workers "${EMAIL_WORKERS:-12}" \
      --no-emails \
      > "$log" 2>&1 &
  pids+=($!)
  echo "started $d (pid ${pids[-1]}) -> $log"
  sleep 2   # stagger so N workers don't all hit Google in the same second
done

echo
echo "all ${#pids[@]} districts started; tailing progress"
while :; do
  alive=0
  for p in "${pids[@]}"; do
    kill -0 "$p" 2>/dev/null && alive=$((alive+1))
  done
  printf '\r%s workers alive | ' "$alive"
  $PY -m resorts_scraper.cli status 2>/dev/null | head -3 | tr '\n' ' '
  if [ "$alive" -eq 0 ]; then echo; break; fi
  sleep 20
done

echo "search+detail phase complete. now the email stage across all districts:"
$PY -u -m resorts_scraper.cli emails --email-workers 12 2>&1 | tee output/logs/emails.log
$PY -u -m resorts_scraper.cli export
