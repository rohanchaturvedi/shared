# generate-ts-html

Generate a fresh T&S Metrics HTML dashboard. Two phases: (1) pull fresh Snowflake data, (2) research current context from Slack and Glean, update commentary, regenerate HTML.

## Before starting

Find `generate_ts_preview.py` — it lives in `rohanchaturvedi/shared/ts_metrics/`. If `SNOWFLAKE_USER` is still `YOUR.EMAIL@CHIME.COM`, update it to the user's Chime email before running.

## Phase 1 — Pull fresh Snowflake data

```bash
python3 generate_ts_preview.py
```

A browser window opens for Okta SSO. After auth, the script runs 5 queries and writes `ts_metrics_live.html`. Note the key numbers — they inform commentary.

## Phase 2 — Research fresh commentary

Search all of the following before writing commentary:

**Slack — last 4 weeks in each channel:**
- `#dispute-risk-analytics` — weekly rate updates, loss metrics, WoW trends
- `#dispute-risk-internal` — team analysis on what's driving rate changes
- `#risk-rule-requests` — new SWAT routing policies, model migrations (mFPF, ZTA), high-risk routing rules
- `#chime-experiments` — active experiments touching dispute rates, approval rates, authentication, or credit decisions
- `#spending-and-disputes` — merchant rings, scam typologies, cross-functional fraud discussions

**Glean searches:**
- "T&S metrics [current month year]"
- "dispute rate [current month year]"
- "dispute loss [current month year]"

**Look specifically for:**
- What is driving the YoY and MoM changes in dispute rate
- New fraud typologies (scam types, merchant rings, ATO patterns)
- Mitigation actions (bulk closures, merchant blocks, SOP changes, new rules deployed)
- Active experiments expected to affect dispute or approval rates
- RAG status from any official monthly review doc

## Phase 3 — Update the NARRATIVE dict

In `generate_ts_preview.py`, find the `NARRATIVE` dict (~line 333). Update all 6 keys:

| Key | Metric |
|-----|--------|
| `7d_dispute_dollar` | 7d Dispute Rate by $ |
| `7d_dispute_count` | 7d Dispute Unit Rate |
| `pct_ut` | % UT |
| `pct_ea` | % EA / Non-reg |
| `approval_count` | Unit Approval Rate |
| `approval_dollar` | $ Approval Rate |

For each key:
- `health`: `"green"`, `"yellow"`, or `"red"` based on current trajectory
- `commentary`: 3–5 sentences on what is driving the current number vs. last month and last year
- `forward_looking`: 3–5 sentences on actions in flight, experiments to watch, what changes next month

Also update the comment just above the dict: `# Current narrative reflects: [Month Year] performance`

## Phase 4 — Regenerate

```bash
python3 generate_ts_preview.py
```

`ts_metrics_live.html` opens in the browser. The file is fully self-contained and shareable — no database connection needed to view it.
