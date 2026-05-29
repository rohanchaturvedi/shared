# T&S Metrics — HTML Preview

## Command
When the user says **"generate T&S HTML"** (or "run T&S preview", "show T&S dashboard"):
Run: `python3 ~/claude/shared/TS_metric/generate_ts_preview.py`

This connects to Snowflake, runs all 5 metric queries, and opens a live HTML dashboard in the browser.

## First-time setup (one person, one time)
1. Open `generate_ts_preview.py` and set your Snowflake email:
   ```
   SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'
   ```
2. Install dependencies if needed:
   ```
   pip install sqlalchemy snowflake-sqlalchemy pandas
   ```
3. Run the command above — a browser window will open for Okta SSO auth.

## What it produces
A 6-tab HTML dashboard saved as `ts_metrics_live.html` in this folder:
- Tab 1: 7d dispute rate by $ (all seasoning windows + YoY/MoM stat cards)
- Tab 2: Dispute rate by reason type (UT / EA / Non-reg pivot + charts)
- Tab 3: Approve/deny by resolution month ($ and unit, with approval rate charts)
- Tab 4: 7d dispute unit rate in bps
- Tab 5: Year-over-year monthly trend (last 5 years)
- Tab 6: Summary table + mini trend charts for doc sharing

## Files in this folder
| File | Purpose |
|------|---------|
| `generate_ts_preview.py` | Main script — runs queries, builds HTML |
| `m1_dispute_rate_7d.sql` | 7d dispute rate by $ (all seasoning windows) |
| `m2_reason_type.sql` | Dispute rate by reason type (UT/EA/Non-reg) |
| `m3_approve_deny.sql` | Approve/deny metrics (ex. de minimis) |
| `m4_unit_rate.sql` | 7d dispute unit rate |
| `m5_yoy_trend.sql` | YoY monthly trend (last 4+ years) |
