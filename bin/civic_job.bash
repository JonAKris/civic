#!/usr/bin/env bash
# Cron wrapper: civic_job.bash nightly|weekly   (flock prevents overlapping runs)
set -euo pipefail
JOB="${1:?usage: civic_job.bash nightly|weekly}"
APP=/home/jon/civic
mkdir -p "$APP/logs"
exec 9>"/tmp/civic_${JOB}.lock"
flock -n 9 || { echo "$(date -Is) civic $JOB already running"; exit 0; }
cd "$APP"
source .venv/bin/activate
echo "=== $(date -Is) civic $JOB ==="
python -m civic.cli "$JOB"
echo "=== $(date -Is) civic $JOB done ==="
