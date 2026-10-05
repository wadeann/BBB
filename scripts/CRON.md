# Daily Stock Pick - runs at 18:00 on trading days (Mon-Fri)
# To install: crontab scripts/CRON.md
# Or: cat scripts/CRON.md | crontab -
0 18 * * 1-5 /home/wade/workspace/ai/codexA/src/scripts/run_daily.sh
