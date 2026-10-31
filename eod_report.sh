#!/bin/bash
# End-of-day report: writes the daily recap + AI review into daily_review.txt,
# which the dashboard displays. Fired by the eod-review.timer after market close.
cd /root/engine || exit 1
{
  echo "Generated: $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo
  ./venv/bin/python recap.py
  echo
  echo "================================================================"
  echo
  ./venv/bin/python review.py
} > daily_review.txt 2>&1
