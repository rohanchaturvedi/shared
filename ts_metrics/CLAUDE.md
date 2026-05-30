# T&S Metrics — HTML Preview

## Command
When the user says **"generate T&S HTML"** (or "run T&S preview", "show T&S dashboard"):
Run: `python3 <path-to-this-folder>/generate_ts_preview.py`

This connects to Snowflake, runs all 5 metric queries, and opens a live HTML dashboard in the browser.

---

## First-time setup (one person, one time)
1. Open `generate_ts_preview.py` and set your Snowflake email at the top:
   ```
   SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'
   ```
2. Install dependencies if needed:
   ```
   pip install sqlalchemy snowflake-sqlalchemy pandas
   ```
3. Run the command — a browser window will open for Okta SSO auth.

---

## What the dashboard produces
A 6-tab HTML file (`ts_metrics_live.html`) saved in this folder:

| Tab | Contents |
|-----|---------|
| 1. 7d Dispute Rate $ | All seasoning windows (7d–180d) + YoY/MoM stat cards + line chart |
| 2. Rate by Reason Type | UT / EA / Non-reg pivot table + % UT / % EA/NonReg stat cards + 2 line charts |
| 3. Approve / Deny | $ and unit approval pivot tables + approval rate stat cards + 2 line charts |
| 4. 7d Unit Rate (bps) | Unit rate data table + stat cards (YoY, MoM, current, last year) + line chart |
| 5. YoY Trend | Multi-line chart, one line per year (last 5 years) |
| 6. Summary | **Summary table + Commentary + Trend mini-charts (for doc sharing)** |

---

## Tab 6 — Commentary & Forward Looking (update monthly)

Tab 6 includes a **Commentary & Forward Looking** section with narrative for each metric.
This section is **not auto-generated from data** — it requires monthly research and human judgment.

### How to update the narrative each month
In `generate_ts_preview.py`, find the `NARRATIVE` dict near the top of the file.
Update each metric's `commentary` and `forward_looking` text, and set `health` to `green`, `yellow`, or `red`.

### Where to research (Slack channels + docs)
Each month before running, check:
- **#dispute-risk-analytics** — weekly dispute rate and loss updates, WoW trends
- **#dispute-risk-internal** — team discussions on what's driving rate changes
- **#spending-and-disputes** — cross-functional discussions on merchant/fraud trends
- **#risk-rule-requests** — new rules deployed or updated (policy changes affecting dispute outcomes)
- **#chime-experiments** — active experiments that may impact dispute rates or approval rates
- **T&S Portfolio Metrics Review** (Google Doc) — official monthly narrative from the team
- **Dispute Risk 20XX Tracker** (Google Doc, updated weekly) — ongoing investigations and actions

### What to look for
- **Rule deployments** (#risk-rule-requests): new SWAT routing rules, model migrations (mFPF), high-risk policies
- **Active experiments** (#chime-experiments): anything touching authentication, dispute policy, or credit decisions
  that could raise or lower dispute rates / approval rates
- **Fraud patterns**: new merchant rings, scam typologies, ATO trends being investigated
- **Mitigation actions**: bulk closures, merchant blocks, SOP changes

### Health indicator guide
- 🟢 `green` — metric is stable or improving, no material concerns
- 🟡 `yellow` — elevated or trending in wrong direction, team is monitoring/acting
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
