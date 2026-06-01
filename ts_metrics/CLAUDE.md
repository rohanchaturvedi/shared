# T&S Metrics — HTML Preview

## How to run

Say **`/generate-ts-html`** to Claude (or plain English: "generate T&S HTML").

Claude will:
1. Run `generate_ts_preview.py` — pulls fresh Snowflake data (5 queries, Okta SSO auth)
2. Search Slack + Glean for current month context
3. Update the `NARRATIVE` dict in the script with fresh commentary and RAG status
4. Re-run the script to produce a fully updated `ts_metrics_live.html`

> The numbers AND the commentary both refresh every time. Numbers won't change much if run twice in the same month; commentary always reflects current Slack/Glean context.

---

## First-time setup (one person, one time)

1. Clone this repo: `git clone https://github.com/rohanchaturvedi/shared.git`
2. Open `ts_metrics/generate_ts_preview.py` and set your email:
   ```
   SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'
   ```
3. Install dependencies if needed:
   ```
   pip install sqlalchemy snowflake-sqlalchemy pandas
   ```
4. Copy the skill to your Claude commands folder:
   ```
   cp .claude/commands/generate-ts-html.md ~/.claude/commands/
   ```
5. Launch Claude from the `ts_metrics/` directory and say `/generate-ts-html`

---

## What the dashboard produces

A 6-tab HTML file (`ts_metrics_live.html`) — fully self-contained, shareable with no database connection needed:

| Tab | Contents |
|-----|---------|
| 1. 7d Dispute Rate $ | All seasoning windows (7d–180d) + YoY/MoM stat cards + line chart |
| 2. Rate by Reason Type | UT / EA / Non-reg pivot table + % UT / % EA/NonReg stat cards + 2 line charts |
| 3. Approve / Deny | $ and unit approval pivot tables + approval rate stat cards + 2 line charts |
| 4. 7d Unit Rate (bps) | Unit rate data table + stat cards (YoY, MoM, current, last year) + line chart |
| 5. YoY Trend | Multi-line chart, one line per year (last 5 years) |
| 6. Summary | **Summary table + Commentary + Trend mini-charts (for doc sharing)** |

---

## Tab 6 — Commentary & Forward Looking

This section is auto-researched and written by Claude each time you run `/generate-ts-html`. Claude searches Slack and Glean for current context and updates the `NARRATIVE` dict in `generate_ts_preview.py` before regenerating.

### Sources Claude searches each run
- **Slack:** `#dispute-risk-analytics`, `#dispute-risk-internal`, `#risk-rule-requests`, `#chime-experiments`, `#spending-and-disputes`
- **Glean:** T&S metrics, dispute rate, dispute loss for the current month
- **Docs:** T&S Portfolio Metrics Review, Dispute Risk Tracker

### Health indicator guide
- 🟢 `green` — stable or improving, no material concerns
- 🟡 `yellow` — elevated or trending wrong, team monitoring/acting
- 🔴 `red` — significant concern, active remediation underway

---

## Files in this folder

| File | Purpose |
|------|---------|
| `generate_ts_preview.py` | Main script — runs Snowflake queries, builds HTML, opens browser |
| `m1_dispute_rate_7d.sql` | 7d dispute rate by $ (all seasoning windows) |
| `m2_reason_type.sql` | Dispute rate by reason type (UT / EA / Non-reg) |
| `m3_approve_deny.sql` | Approve/deny metrics (ex. de minimis) |
| `m4_unit_rate.sql` | 7d dispute unit rate |
| `m5_yoy_trend.sql` | YoY monthly trend (last 4+ years) |
| `../.claude/commands/generate-ts-html.md` | Claude Code skill — copy to `~/.claude/commands/` |
