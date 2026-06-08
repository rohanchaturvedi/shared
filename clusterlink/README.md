# Clusterlink — PII linkage cluster analysis dashboard

A self-contained pipeline that links Chime disputers via shared PII (email, phone, SSN digest, address, device ID), characterizes each cluster against 8 risk signals, and produces an interactive 8-tab HTML dashboard for fraud investigators and leadership.

**Why:** individual-level fraud signals miss coordinated rings. A borderline member who shares a device with 30 other disputers, 80% of whom are already cancelled, is unmistakably part of a ring — but no single-member model catches that. Cluster-level analysis does.

---

## Getting this folder

You only need the `clusterlink/` folder — sparse-clone it from the parent repo:

```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/rohanchaturvedi/shared.git
cd shared
git sparse-checkout set clusterlink
mv clusterlink ~/claude/           # or wherever you keep your Claude projects
cd ~/claude/clusterlink
```

Or download as ZIP from `https://github.com/rohanchaturvedi/shared/tree/main/clusterlink` → Code → Download ZIP → extract just `clusterlink/`.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Snowflake access** | Role `SNOWFLAKE_PROD_ANALYTICS_PII_ROLE_OKTA` (or any role with read on the tables listed below) |
| **Okta SSO browser auth** | First run opens a browser window for SAML auth |
| **Python 3.9+** | `pip install sqlalchemy snowflake-sqlalchemy pandas numpy` |
| **Mac/Linux/WSL** | Tested on macOS; Snowflake auth must run in foreground (not detached background) so the SSO browser can open |

---

## First-time setup (one minute)

Open all three Python files and set your Chime Snowflake email at the top:

```python
SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'
```

That's it. No other config needed.

---

## Running the pipeline (3 steps, ~5 minutes total)

```bash
python3 linkage_01_pii_pull.py        # ~60s — pulls 114K disputers + PII + devices, runs EDA
python3 linkage_02_cluster.py         # ~30s — Union-Find clustering, no Snowflake needed
python3 generate_clusterlink_html.py  # ~3min — pulls fresh metrics, builds HTML, opens browser
```

Intermediate CSVs land in `outputs/` (auto-created). The HTML dashboard lands at `clusterlink_live.html` and opens in your browser.

**To refresh the dashboard:** re-run the third script anytime. It re-pulls all metric blocks from Snowflake fresh, so the numbers always reflect current data.

**To change the seed window:** edit `SEED_START`/`SEED_END` at the top of `linkage_01_pii_pull.py` and `generate_clusterlink_html.py`, then re-run all 3 scripts.

---

## What you get — the 8-tab dashboard

| Tab | What it shows |
|---|---|
| **1. Overview** | Executive summary (5 dynamic bullets), pipeline diagram, KPIs, cluster size distribution |
| **2. Loss Savings** | Addressable loss by signal threshold, single-signal breakdown, per-linkage-attribute breakdown — the business case |
| **3. Loss Concentration** | Pareto of cluster loss + FC/NB stacked composition |
| **4. Behavioral Patterns** | 4 scatter plots (decline rate, fraud-flagged, ScanID required, password-fail) vs loss + stacked decline-code mix |
| **5. Funding & P2P** | Funding source pie, dispute-to-inflow histogram, P2P-vs-DD bubble chart, stacked funding mix for top P2P-heavy clusters |
| **6. Smoking-Gun Rings** | 3 example cluster cards, SSN-vs-address bubble, density distribution, attribute-anatomy radar |
| **7. Geographic** | Top 10 states by loss, state-level high-conviction count |
| **8. Glossary, Methodology & Data** | Network diagram, all 8 signal thresholds, EDA findings, full glossary, data sources, **all 15 data tables consolidated** |

Every tab follows the same pattern: **narrative → KPI cards → charts → dynamic "Insight from this run" callout at the end**. Heavy data tables are all in Tab 8.

---

## Architecture

### The 3 scripts

| Script | Output | What it does |
|---|---|---|
| `linkage_01_pii_pull.py` | `outputs/01–05_*.csv` | Pulls seed disputers (Oct 2025–Mar 2026, dispute $ ≥ $500), pulls + normalizes 5 PII attributes, peeks Snowflake schemas, reports null rates and sharing distributions |
| `linkage_02_cluster.py` | `outputs/06–09_*.csv` | EDA-driven cleaning (gmail dot-strip, 555 phone removal, 'NA' device filter), Union-Find clustering on 6 cluster dimensions (cluster_1 = transitive, cluster_2…6 = single-attribute) |
| `generate_clusterlink_html.py` | `clusterlink_live.html` | Pulls 7 fresh metric blocks from Snowflake, aggregates to cluster level (avg-of-per-member-peak for scores, sum-of-numerator/denominator for rates), runs email text analytics, computes composite risk + savings projections, renders 8-tab HTML |

### Snowflake tables used

| Table | Purpose |
|---|---|
| `rest.test.ub_dispute_exception_reporting_base` | Dispute count, $, FC, NB, scores (FPF/mFPF/PVC), reason, intake |
| `edw_pii_db.core.dim_user_pii` | Email, phone, SSN digest, address, state — linkage attributes |
| `edw_db.core.dim_member_v10` | User status, account age, program tier |
| `analytics.looker.device_sessions` | Device IDs per user |
| `analytics.test.login_requests` | Auth events — pw_fail, MFA, ScanID |
| `edw_db.core.fct_realtime_auth_event` | Auth attempts + declines |
| `edw_db.core.ftr_transaction` | Funding, transfers, P2P, withdrawals |
| `risk.prod.spotme_eligible_direct_deposits` | Direct deposit inflow |
| `ml.model_inference.member_level_fpf_model_v2_score` | FPF v2 member-level score |

---

## How clusters are formed

Each unique attribute value (one email, one phone, one address, etc.) is a virtual node. Each member node is connected to the attribute values they own. We run Union-Find across this bipartite graph — any two members reachable through any chain of shared attribute values end up in the same connected component.

**cluster_1** = full transitive closure across all 5 attributes (the primary view).
**cluster_2** = email-only · **cluster_3** = address-only · **cluster_4** = SSN-only · **cluster_5** = phone-only · **cluster_6** = device-only.

The 5,738-member "giant component" that emerges from device-chain transitivity (legit members sharing devices through app reinstalls over years) is excluded from actionable views and flagged separately on Tab 8.

---

## The 8 risk signals (composite scoring)

Each actionable cluster (6–999 members) is ranked against 8 signals. If it appears in the top 50 of a signal, it "hits" that signal. Sum hits = composite score.

| Signal | Filter |
|---|---|
| Total Loss $ | none |
| Loss Rate % | dispute $ > $10K |
| Dispute $/Member | none |
| Decline Rate % | > 100 auth attempts |
| Fraud-Flagged Decline % | > 50 auth attempts |
| Cancelled % | none |
| Avg Peak FPF v2 Score | none |
| Top-State Share % | none |

**Score aggregation rule:** for each member, take their max (peak) score over the window. Then at cluster level, **average** those member-peaks. This is more robust than cluster-max — a single outlier doesn't dominate.

**Rate aggregation rule:** sum-of-numerator ÷ sum-of-denominator at the cluster level. Never average individual member rates.

---

## File layout after running

```
clusterlink/
├── README.md                          ← you're reading it
├── clusterlink_skill.md               ← Claude Code skill spec (trigger phrases, run command)
├── linkage_01_pii_pull.py
├── linkage_02_cluster.py
├── generate_clusterlink_html.py
├── .gitignore                         ← blocks outputs from being committed
├── outputs/                           ← created on first run, gitignored
│   ├── 01_seed_users.csv
│   ├── 02_pii_attributes.csv
│   ├── 03_device_ids.csv
│   ├── 04_analytics_summary.csv
│   ├── 05_sharing_distribution.csv
│   ├── 06_pii_cleaned.csv
│   ├── 07_member_clusters.csv         ← consumed by generate_clusterlink_html.py
│   ├── 08_cluster_size_distribution.csv
│   └── 09_top_clusters.csv
└── clusterlink_live.html              ← the dashboard, opens in browser
```

`outputs/` and `clusterlink_live.html` are git-ignored — they contain Chime PII and must never be committed.

---

## Triggering via Claude Code

If you're using Claude Code, point Claude at `clusterlink_skill.md` — that file defines the trigger phrases ("run clusterlink analysis", "generate clusterlink HTML") and tells Claude to invoke the 3 scripts in order.

---

## Maintaining / extending

| To do this | Edit |
|---|---|
| Change the dispute amount threshold | `dispute_amount >= 500` in `linkage_01_pii_pull.py` and `generate_clusterlink_html.py` |
| Change the seed window | `SEED_START` / `SEED_END` (3 scripts have it) |
| Change cluster size cutoffs (actionable / giant) | `GIANT_THRESHOLD = 1000` in `generate_clusterlink_html.py` |
| Change the 8 signals | `SIGNAL_DEFS` list in `generate_clusterlink_html.py` |
| Add a new model score | New Snowflake block + extend the cluster aggregation |

---

## Safety notes

- Generated CSVs in `outputs/` contain PII (emails, hashed SSNs, addresses). The included `.gitignore` blocks them from being committed back. Don't override that.
- The dashboard HTML contains aggregated cluster data — no per-member PII — but still keep it internal.
- All Snowflake reads are read-only via your assigned role. The pipeline writes nothing to Snowflake.
