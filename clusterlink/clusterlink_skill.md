# Clusterlink — Claude Code skill spec

## Command
When the user says **"run clusterlink analysis"** (or "generate clusterlink HTML", "refresh clusterlink dashboard"):

Run the three scripts in order, in foreground (so Snowflake Okta SSO browser auth can open):

```bash
python3 <path-to-clusterlink>/linkage_01_pii_pull.py
python3 <path-to-clusterlink>/linkage_02_cluster.py
python3 <path-to-clusterlink>/generate_clusterlink_html.py
```

The third script opens the resulting HTML dashboard (`clusterlink_live.html`) in the user's default browser.

## If only refreshing (clusters already formed)
When the user says **"refresh clusterlink"** or **"regenerate clusterlink dashboard"** and `outputs/07_member_clusters.csv` already exists:

Skip Phase 1 + 2 — just run:
```bash
python3 <path-to-clusterlink>/generate_clusterlink_html.py
```

Phase 1 + 2 only need to be re-run when the seed window or PII normalization rules change. Phase 3 always pulls fresh Snowflake metrics each run.

---

## First-time setup (one user, one time)

1. **Set Snowflake email** in all 3 Python files:
   ```
   SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'
   ```
2. **Install dependencies** if needed:
   ```
   pip install sqlalchemy snowflake-sqlalchemy pandas numpy
   ```
3. **Run the command** — a browser window opens for Okta SSO on first run; subsequent runs reuse the auth.

---

## What the dashboard produces

A self-contained interactive HTML file (`clusterlink_live.html`) with 8 tabs:

| Tab | Contents |
|-----|---------|
| 1. Overview | Executive summary (5 dynamic bullets) + pipeline diagram + KPIs + cluster size chart |
| 2. Loss Savings | Addressable loss by signal-threshold + single-signal + per-attribute breakdown |
| 3. Loss Concentration | Pareto chart + FC/NB stacked composition of top 10 |
| 4. Behavioral Patterns | 4 scatter plots vs loss + stacked decline-code mix + decline code legend |
| 5. Funding & P2P | Funding source pie + dispute-to-inflow histogram + P2P-vs-DD bubble + stacked funding mix |
| 6. Smoking-Gun Rings | 3 example cluster cards + SSN-vs-address bubble + density distribution + attribute radar |
| 7. Geographic | State-level dispute $ vs loss + state-level high-conv count |
| 8. Glossary, Methodology & Data | Network diagram + signal thresholds + EDA + glossary + data sources + **full 15 data tables** consolidated |

Each tab structure: **narrative → KPI cards → charts → dynamic "Insight from this run" callout at the end**. All heavy data tables live on Tab 8.

---

## Static vs dynamic text

The dashboard has two kinds of text:

**Static** (fixed every run — same words across teams):
- Tab descriptions and "story" intros
- Glossary cards
- Methodology and network diagram
- Signal threshold definitions

**Dynamic** (regenerated from data each run):
- All KPI numbers
- Executive summary bullets (Tab 1) — referencing actual top cluster IDs, dollar amounts, states
- "Insight from this run" callouts at the bottom of each tab
- Smoking-gun example cards (Tab 6)
- Every table and chart

When the underlying data shifts (new disputers, refreshed metrics), the dynamic text shifts with it. Static narrative stays put.

---

## Files in this folder

| File | Purpose |
|------|---------|
| `linkage_01_pii_pull.py` | Phase 1 — pulls disputer seed, PII attributes, device IDs from Snowflake, runs EDA |
| `linkage_02_cluster.py` | Phase 2 — EDA cleaning + Union-Find clustering on 6 cluster dimensions |
| `generate_clusterlink_html.py` | Phase 3 — pulls cluster characterization metrics, builds and opens HTML dashboard |
| `README.md` | Full project documentation — setup, architecture, signals, file layout |
| `clusterlink_skill.md` | This file — Claude Code skill spec |
| `.gitignore` | Blocks `outputs/` and the HTML file from being committed back (PII safety) |

---

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| `invalid identifier '...'` errors | A Snowflake column name changed. Check the failing block, search Glean for the new schema, update the SQL |
| Browser doesn't open for SSO | Script ran in background. Re-run in foreground (no `&`, no detached terminal) |
| Phase 3 fails on missing CSV | Phase 1 or 2 hasn't been run yet — run them first |
| FPF v2 score column 0% populated | Score table may not have entries for your seed window. Check `predict_created_ts` range in Snowflake |
