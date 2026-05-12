#!/bin/bash
# Nightly autoresearch — runs up to 10 experiments to improve scoring_prompt.md.
# Scheduled via crontab; logs to autoresearch/nightly.log.

set -euo pipefail

REPO="/Users/gaurav/job-search-agent"
LOG="$REPO/autoresearch/nightly.log"
PYTHON="/opt/anaconda3/bin/python3"

cd "$REPO"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') nightly autoresearch start ===" >> "$LOG"

# Run up to 10 experiments; stop early if plateau reached (5 consecutive failures)
"$PYTHON" autoresearch/loop.py \
    --max-experiments 10 \
    --plateau-patience 5 \
    >> "$LOG" 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ===" >> "$LOG"
