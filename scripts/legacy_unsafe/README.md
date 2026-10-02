# Legacy / Unsafe Data Builders

**WARNING: These scripts are UNSAFE for production use.**

They contain synthetic data generation, test fixtures, and incorrect corporate actions.
They must NOT be used to overwrite any production historical dataset files:
- `data/backtest/security_master.csv`
- `data/backtest/historical_status_intervals.csv`
- `data/backtest/historical_sector_intervals.csv`
- `data/backtest/corporate_actions.csv`

Moved here from `scripts/` on 2026-10-02 per Codex audit requirement.
