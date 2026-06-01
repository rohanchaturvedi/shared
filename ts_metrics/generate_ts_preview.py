#!/usr/bin/env python3
"""
T&S Metrics — Live HTML Preview Generator
==========================================
SETUP (one time only):
  1. Set your Snowflake email below.
  2. Run: pip install sqlalchemy snowflake-sqlalchemy pandas

USAGE:
  python3 generate_ts_preview.py
  Or just say "generate T&S HTML" to Claude.
"""

# ── CONFIG — update this before first run ─────────────────────────────────────
SNOWFLAKE_USER    = 'ROHAN.CHATURVEDI@CHIME.COM'
SNOWFLAKE_ACCOUNT = 'CHIME'
SNOWFLAKE_WH      = 'ANALYTICS_WH'
SNOWFLAKE_ROLE    = 'SNOWFLAKE_PROD_ANALYTICS_PII_ROLE_OKTA'
# ─────────────────────────────────────────────────────────────────────────────

import sys, os, subprocess
import pandas as pd
from sqlalchemy import create_engine, text
from snowflake.sqlalchemy import URL

BASE  = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(BASE, 'ts_metrics_live.html')

def read_sql(fname):
    with open(os.path.join(BASE, fname)) as f:
        lines = [l for l in f.read().splitlines() if not l.strip().startswith('--')]
    return '\n'.join(lines)

def pct(v, dec=1):
    if v is None or (hasattr(v,'__class__') and v.__class__.__name__ == 'NaTType'): return 'N/A'
    sign = '+' if float(v) >= 0 else ''
    return f"{sign}{float(v)*100:.{dec}f}%"

def bps(v, dec=2):
    if v is None: return 'N/A'
    return f"{float(v):.{dec}f} bps"

def pct_abs(v, dec=1):
    if v is None: return 'N/A'
    return f"{float(v)*100:.{dec}f}%"

def dollar(v):
    m = float(v)/1e6
    return f"${m:,.1f}M"

def fmt_int(v):
    return f"{int(v):,}"

# ── Connect ───────────────────────────────────────────────────────────────────
print("Connecting to Snowflake (browser auth will open)...")
url = URL(user=SNOWFLAKE_USER, authenticator='externalbrowser',
          account=SNOWFLAKE_ACCOUNT, warehouse=SNOWFLAKE_WH, role=SNOWFLAKE_ROLE)
conn = create_engine(url).connect()
print("Connected. Running queries...")

def q(sql): return pd.read_sql(text(sql), conn)

# ── Query 1: Dispute rate by $ ────────────────────────────────────────────────
df1 = q(read_sql('m1_dispute_rate_7d.sql'))
df1.columns = [c.lower() for c in df1.columns]

# ── Query 2: Reason type breakdown ───────────────────────────────────────────
df2 = q(read_sql('m2_reason_type.sql'))
df2.columns = [c.lower() for c in df2.columns]
df2 = df2.sort_values('trxn_month').reset_index(drop=True)

# ── Query 3: Approve/deny ─────────────────────────────────────────────────────
df3 = q(read_sql('m3_approve_deny.sql'))
df3.columns = [c.lower() for c in df3.columns]
df3 = df3.sort_values('resolution_month').reset_index(drop=True)

# ── Query 4: Unit rate ────────────────────────────────────────────────────────
df4 = q(read_sql('m4_unit_rate.sql'))
df4.columns = [c.lower() for c in df4.columns]
df4 = df4.sort_values('trxn_month').reset_index(drop=True)

# ── Query 5: YoY trend ────────────────────────────────────────────────────────
df5 = q(read_sql('m5_yoy_trend.sql'))
df5.columns = [c.lower() for c in df5.columns]

print("Queries complete. Building dashboard...")

# ═════════════════════════════════════════════════════════════════════════════
# EXTRACT VALUES
# ═════════════════════════════════════════════════════════════════════════════

# ── Tab 1 data ────────────────────────────────────────────────────────────────
m1_cur  = df1[df1['mth_offset'] == -1].iloc[0]
m1_py   = df1[df1['mth_offset'] == -13].iloc[0] if -13 in df1['mth_offset'].values else None
m1_lm   = df1[df1['mth_offset'] == -2].iloc[0]

s1_cur  = bps(m1_cur['dispute_rate_7d'])
s1_yoy  = pct(m1_cur.get('yoy_7d'))
s1_mom  = pct(m1_cur.get('mom_7d'))
s1_py   = bps(m1_py['dispute_rate_7d']) if m1_py is not None else 'N/A'

months14   = df1['txn_month'].tolist()
d_7d       = [round(float(v), 2) if pd.notna(v) else None for v in df1['dispute_rate_7d']]

# ── Tab 2 data ────────────────────────────────────────────────────────────────
months13  = df2['trxn_month'].tolist()
d_ut      = [round(float(v), 6) if pd.notna(v) else None for v in df2['ut']]
d_ea      = [round(float(v), 6) if pd.notna(v) else None for v in df2['ea_plus_nonreg']]

m2_cur = df2.iloc[-1];  m2_lm = df2.iloc[-2];  m2_py = df2.iloc[0]
s2_ut_cur = pct_abs(m2_cur['pct_ut']);   s2_ut_lm = pct_abs(m2_lm['pct_ut']);   s2_ut_py = pct_abs(m2_py['pct_ut'])
s2_ea_cur = pct_abs(m2_cur['pct_ea_nonreg']); s2_ea_lm = pct_abs(m2_lm['pct_ea_nonreg']); s2_ea_py = pct_abs(m2_py['pct_ea_nonreg'])

def pivot_row(row):
    return {
        'month': str(row['trxn_month'])[:10],
        'ea':    pct_abs(row['ea']),   'non_reg': pct_abs(row['non_reg']),
        'not_d': pct_abs(row.get('not_disputed', 0)),
        'ut':    pct_abs(row['ut']),   'total':   pct_abs(row['grand_total']),
        'pct_ut': pct_abs(row['pct_ut']), 'pct_ea': pct_abs(row['pct_ea_nonreg'])
    }
pivot2 = [pivot_row(df2.iloc[i]) for i in range(len(df2))]

# ── Tab 3 data ────────────────────────────────────────────────────────────────
months13r = df3['resolution_month'].tolist()
d_adlr    = [round(float(v), 4) if pd.notna(v) else None for v in df3['approval_rate_dlr']]
d_acnt    = [round(float(v), 4) if pd.notna(v) else None for v in df3['approval_rate_cnt']]

m3_cur = df3.iloc[-1];  m3_py = df3.iloc[0]
s3_dlr_cur = pct_abs(m3_cur['approval_rate_dlr'], 0)
s3_dlr_mom = pct(m3_cur.get('mom_dlr'))
s3_dlr_yoy = pct(m3_cur.get('yoy_dlr'))
s3_dlr_py  = pct_abs(m3_py['approval_rate_dlr'], 0)
s3_cnt_cur = pct_abs(m3_cur['approval_rate_cnt'], 0)
s3_cnt_mom = pct(m3_cur.get('mom_cnt'))
s3_cnt_yoy = pct(m3_cur.get('yoy_cnt'))
s3_cnt_py  = pct_abs(m3_py['approval_rate_cnt'], 0)

def approv_row(row):
    return {
        'month':   str(row['resolution_month'])[:10],
        'app_amt': dollar(row['approve_amt']),  'den_amt': dollar(row['deny_amt']),
        'tot_amt': dollar(row['total_amt']),    'rate_d':  pct_abs(row['approval_rate_dlr'],0),
        'app_cnt': fmt_int(row['approve_cnt']), 'den_cnt': fmt_int(row['deny_cnt']),
        'tot_cnt': fmt_int(row['total_cnt']),   'rate_c':  pct_abs(row['approval_rate_cnt'],0)
    }
pivot3 = [approv_row(df3.iloc[i]) for i in range(len(df3))]
tot3 = {
    'app_amt': dollar(df3['approve_amt'].sum()),  'den_amt': dollar(df3['deny_amt'].sum()),
    'tot_amt': dollar(df3['total_amt'].sum()),    'rate_d':  pct_abs(df3['approve_amt'].sum()/df3['total_amt'].sum(), 0),
    'app_cnt': fmt_int(df3['approve_cnt'].sum()), 'den_cnt': fmt_int(df3['deny_cnt'].sum()),
    'tot_cnt': fmt_int(df3['total_cnt'].sum()),   'rate_c':  pct_abs(df3['approve_cnt'].sum()/df3['total_cnt'].sum(), 0)
}

# ── Tab 4 data ────────────────────────────────────────────────────────────────
months14u   = df4['trxn_month'].tolist()
d_unit_bps  = [round(float(v), 2) if pd.notna(v) else None for v in df4['dispute_rate_cnt_bps']]

# skip partial current month (last row desc = iloc[-1] in asc order... but df4 is asc now)
# first row = oldest, last row = current partial, second-to-last = current complete month
m4_cur = df4.iloc[-2];  m4_lm = df4.iloc[-3];  m4_py = df4.iloc[0]
cur_bps = float(m4_cur['dispute_rate_cnt_bps'])
lm_bps  = float(m4_lm['dispute_rate_cnt_bps'])
py_bps  = float(m4_py['dispute_rate_cnt_bps'])
s4_cur  = f"{cur_bps:.2f} bps"
s4_mom  = f"{(cur_bps-lm_bps)/cur_bps*100:+.1f}%" if cur_bps else 'N/A'
s4_yoy  = f"{(cur_bps-py_bps)/cur_bps*100:+.1f}%" if cur_bps else 'N/A'
s4_py   = f"{py_bps:.2f} bps"

def unit_row(row):
    return {
        'month': str(row['trxn_month'])[:10],
        'cnt7d': fmt_int(row['dispute_7d_cnt']), 'tcnt': fmt_int(row['trxn_cnt']),
        'damt':  dollar(row['disputed_amt']),    'tamt': f"${float(row['trxn_amt'])/1e9:.1f}B",
        'rdlr':  f"{float(row['dispute_rate_dlr'])*100:.4f}%",
        'rbps':  f"{float(row['dispute_rate_cnt_bps']):.2f} bps"
    }
pivot4 = [unit_row(df4.iloc[i]) for i in range(len(df4))]

# ── Tab 5 data ────────────────────────────────────────────────────────────────
yoy_years = sorted(df5['txn_year'].unique().tolist())
yoy_series = {}
for yr in yoy_years:
    sub = df5[df5['txn_year'] == yr].sort_values('txn_month_num')
    vals = [None]*12
    for _, row in sub.iterrows():
        idx = int(row['txn_month_num']) - 1
        vals[idx] = round(float(row['dispute_rate_7d']), 2) if pd.notna(row['dispute_rate_7d']) else None
    yoy_series[int(yr)] = vals

# ── Tab 6 summary data ────────────────────────────────────────────────────────
# MoM/YoY for %UT and %EA are absolute values (prior month / prior year values)
summary = [
    {'metric':'7d Dispute $ Rate',  'cur':s1_cur,  'mom':s1_mom,  'yoy':s1_yoy,  'py':s1_py},
    {'metric':'7d Dispute # Rate',  'cur':s4_cur,  'mom':s4_mom,  'yoy':s4_yoy,  'py':s4_py},
    {'metric':'% UT',               'cur':s2_ut_cur,'mom':s2_ut_lm,'yoy':s2_ut_py,'py':s2_ut_py},
    {'metric':'% EA / Non-reg',     'cur':s2_ea_cur,'mom':s2_ea_lm,'yoy':s2_ea_py,'py':s2_ea_py},
    {'metric':'Approval (# Rate)',  'cur':s3_cnt_cur,'mom':s3_cnt_mom,'yoy':s3_cnt_yoy,'py':s3_cnt_py},
    {'metric':'Approval ($ Rate)',  'cur':s3_dlr_cur,'mom':s3_dlr_mom,'yoy':s3_dlr_yoy,'py':s3_dlr_py},
]

# ── YoY palette ───────────────────────────────────────────────────────────────
yr_colors = {yr: c for yr, c in zip(sorted(yoy_years),
    ['#2563eb','#16a34a','#d97706','#111827','#dc2626','#7c3aed'])}

# ═════════════════════════════════════════════════════════════════════════════
# HTML GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def js_list(arr):
    return '[' + ','.join('null' if v is None else str(v) for v in arr) + ']'

def js_str_list(arr):
    return '[' + ','.join(f'"{v}"' for v in arr) + ']'

def color_mom_yoy(val):
    """Returns inline style color for MoM/YoY — red=up, green=down."""
    if val == 'N/A' or not val: return ''
    try:
        num = float(val.replace('%','').replace('+','').replace(' bps',''))
        if num > 0:  return 'color:#dc2626;font-weight:600'
        if num < 0:  return 'color:#16a34a;font-weight:600'
    except: pass
    return 'font-weight:600'

def color_approv(val):
    """Approval rate: up = red (more payouts = bad)."""
    if val == 'N/A' or not val: return ''
    try:
        num = float(val.replace('%','').replace('+','').replace(' bps',''))
        if num > 0: return 'color:#dc2626;font-weight:600'
        if num < 0: return 'color:#16a34a;font-weight:600'
    except: pass
    return 'font-weight:600'

def pivot2_rows_html():
    rows = ''
    for r in pivot2:
        rows += f"<tr><td>{r['month']}</td><td>{r['ea']}</td><td>{r['non_reg']}</td><td>{r['not_d']}</td><td>{r['ut']}</td><td>{r['total']}</td><td>{r['pct_ut']}</td><td>{r['pct_ea']}</td></tr>\n"
    ea_tot = pct_abs(df2['ea'].sum()); nr_tot = pct_abs(df2['non_reg'].sum())
    ut_tot = pct_abs(df2['ut'].sum()); gt_tot = pct_abs(df2['grand_total'].sum())
    pcut_tot = pct_abs(df2['ut'].sum()/df2['grand_total'].sum())
    pcea_tot = pct_abs((df2['ea'].sum()+df2['non_reg'].sum())/df2['grand_total'].sum())
    rows += f'<tr class="total-row"><td>Grand Total</td><td>{ea_tot}</td><td>{nr_tot}</td><td>0.0000%</td><td>{ut_tot}</td><td>{gt_tot}</td><td>{pcut_tot}</td><td>{pcea_tot}</td></tr>'
    return rows

def pivot3_rows_html(kind):
    rows = ''
    for r in pivot3:
        if kind == '$':
            rows += f"<tr><td>{r['month']}</td><td>{r['app_amt']}</td><td>{r['den_amt']}</td><td>{r['tot_amt']}</td><td>{r['rate_d']}</td></tr>\n"
        else:
            rows += f"<tr><td>{r['month']}</td><td>{r['app_cnt']}</td><td>{r['den_cnt']}</td><td>{r['tot_cnt']}</td><td>{r['rate_c']}</td></tr>\n"
    t = tot3
    if kind == '$':
        rows += f'<tr class="total-row"><td>Grand Total</td><td>{t["app_amt"]}</td><td>{t["den_amt"]}</td><td>{t["tot_amt"]}</td><td>{t["rate_d"]}</td></tr>'
    else:
        rows += f'<tr class="total-row"><td>Grand Total</td><td>{t["app_cnt"]}</td><td>{t["den_cnt"]}</td><td>{t["tot_cnt"]}</td><td>{t["rate_c"]}</td></tr>'
    return rows

def pivot4_rows_html():
    rows = ''
    for r in pivot4:
        rows += f"<tr><td>{r['month']}</td><td>{r['cnt7d']}</td><td>{r['tcnt']}</td><td>{r['damt']}</td><td>{r['tamt']}</td><td>{r['rdlr']}</td><td>{r['rbps']}</td></tr>\n"
    return rows

def narrative_html():
    rows = [
        ('7d Dispute $ Rate',   '7d_dispute_dollar'),
        ('7d Dispute # Rate',   '7d_dispute_count'),
        ('% UT',                'pct_ut'),
        ('% EA / Non-reg',      'pct_ea'),
        ('Approval (# Rate)',   'approval_count'),
        ('Approval ($ Rate)',   'approval_dollar'),
    ]
    cards = ''
    for label, key in rows:
        n = NARRATIVE[key]
        icon  = HEALTH_ICON[n['health']]
        color = HEALTH_COLOR[n['health']]
        cards += f"""<div style="background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;margin-bottom:12px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <span style="font-weight:700;font-size:13px;color:#1f2937">{label}</span>
            <span style="font-size:13px">{icon}</span>
            <span style="font-size:11px;font-weight:600;color:{color};text-transform:uppercase;letter-spacing:.3px">{n['health'].title()}</span>
          </div>
          <p style="font-size:12.5px;color:#374151;line-height:1.6;margin-bottom:8px">{n['commentary']}</p>
          <p style="font-size:12px;color:#6b7280;line-height:1.5">
            <span style="font-weight:600;color:#374151">Forward Looking:</span> {n['forward_looking']}
          </p>
        </div>"""
    return cards

def summary_rows_html():
    rows = ''
    for r in summary:
        m = r['metric']; cur = r['cur']; mom = r['mom']; yoy = r['yoy']; py = r['py']
        is_pct    = m.startswith('%')        # % UT and % EA/Non-reg — neutral, no color
        is_approv = 'Approval' in m          # Approval rows — red if up, green if down
        if is_pct:
            mom_style = yoy_style = 'font-weight:600'
        elif is_approv:
            mom_style = color_approv(mom);   yoy_style = color_approv(yoy)
        else:
            mom_style = color_mom_yoy(mom);  yoy_style = color_mom_yoy(yoy)
        rows += f"""<tr>
          <td>{m}</td><td>{cur}</td>
          <td style="{mom_style}">{mom}</td>
          <td style="{yoy_style}">{yoy}</td>
          <td>{py}</td>
        </tr>\n"""
    return rows

def yoy_datasets_js():
    parts = []
    for yr in sorted(yoy_series.keys()):
        col = yr_colors.get(yr, '#999')
        data = js_list(yoy_series[yr])
        parts.append(f'{{label:"{yr}",data:{data},borderColor:"{col}",backgroundColor:"transparent",pointRadius:4}}')
    return '[' + ','.join(parts) + ']'

def stat_cell(label, val, style=''):
    return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value neutral" style="{style}">{val}</div></div>'

def stat_cell_colored(label, val, color_fn):
    style = color_fn(val)
    return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value" style="{style}">{val}</div></div>'

# ── NARRATIVE — update this section monthly after researching Slack + docs ────
# Sources: T&S Portfolio Metrics Review, Dispute Risk 2026 Tracker, Slack #dispute-risk-analytics
# Current narrative reflects: May 2026 performance

NARRATIVE = {
    '7d_dispute_dollar': {
        'health': 'yellow',
        'commentary': (
            'Dispute rate ticked back up from 17bps → 19bps (transaction week 5/11 → 5/18) after briefly dipping '
            'from the March/April tax-season peak. Loss rate rose from 10.4% → 11.0% (resolution week 5/17), '
            'driven by 60K Debit EA and 15K Instant Transfer losses. April loss closed at 7.8 bps (~18% above '
            'goal), with ~$17MM in Chime impersonation/scam claims filed since Jan 2026 as the primary driver. '
            'Scam daily losses dropped sharply in early May ($240K/week → ~$13K/day by 5/7) as bulk closures '
            'and SWAT routing took hold, but a fresh uptick emerged in the week of 5/18.'
        ),
        'forward_looking': (
            'Fast Travel Scan ID experiment ramped to 100% (6/1) — biometric step-up for impossible-travel logins. '
            'Card Face Auth (Incode) launched 5/28 for high-risk FPF cohorts; running 2 months. '
            'New SWAT Routing Scam Trend Decplat Rule v1 (5/29) uses dispute questionnaire signals to route FPF scam disputes. '
            'SWAT Routing Dormancy BD V1 (5/28) routes ~21K dormant/Bangladesh-blocked users to SWAT. '
            'ATOM v3 refresh shipped — estimated ~$300–700K/year dispute loss savings from improved ScanID precision.'
        )
    },
    '7d_dispute_count': {
        'health': 'yellow',
        'commentary': (
            'Unit rate remains elevated YoY, with small-dollar dispute volume holding high through May. '
            'SPF (Semi-Professional Fraud) continues as the dominant typology — organized fraud rings '
            'confirmed via ANI linkage and device-sharing analysis. New merchant fraud ring identified in '
            'Philly/Darby area (BRAIDSBYSHAMYA, VOICE OF THE CHILDREN L, PARTNERS A., MILLIONARE MIR, '
            'C&B LUXURY AUTO) — ghost merchants active since 5/8 with $50K+ NB at fixed ~$1.9K–$2.9K amounts. '
            'Prison commissary fraud also spiked: UNION SUPPLY DIRECT ($9K from 55 users in May).'
        ),
        'forward_looking': (
            'P2P scam questionnaire & M2G interstitial at 100% since 5/12 — "during-the-scam" friction expected '
            'to reduce UT scam filing rates over coming months. '
            'SSD_rate_limit mFPF v2 migration live (5/20) — updated self-serve disablement using improved FPF scores. '
            'High Risk ANI routing rules V1/V2/V3 all live in May, progressively tightening coverage. '
            'Atom_new_device_block_v5_ATOMv3 (5/29) updated to exclude 2 reason codes with in-app guardrail handling.'
        )
    },
    'pct_ut': {
        'health': 'green',
        'commentary': (
            'UT share stable in the 71–72% range through May, consistent with prior year. '
            'Tax season pressure normalizing as expected into summer. '
            'SPF typology continues to dominate scam UT claims; questionnaire data from p2p_scam_questionnaire '
            'experiment now feeding richer signals to dispute team for scam identification and denial. '
            'SWAT Routing SCAM trend bulk-actioned users rule (5/21) targets April FPF attack cohort specifically.'
        ),
        'forward_looking': (
            '3DS Step-Up Choice vs OTP Only experiment (started 5/4, 6 weeks) — converting in-app auth; '
            'monitoring for UT dispute rate impact on newly-approved transactions. '
            'Shift-Deny-to-OOB experiment (5/4, 6 weeks) converts 3DS denials to in-app auth for recent phone-change users. '
            'New SWAT Scam Trend Decplat Rule (5/29) uses questionnaire answers for FPF scam routing — '
            'expected to improve SWAT denial rates on scam UT claims.'
        )
    },
    'pct_ea': {
        'health': 'yellow',
        'commentary': (
            'EA NB jumped from ~$178K → ~$248K WoW (resolution week 5/17), driven by debit purchase (~$177K). '
            'Non-Reg NB rate increased 0.36% → 0.55% in the same week; recat losses (non-reg reclassified to '
            'EA/UT) running ~$146K/week — members exploiting PVC-eligible reason codes for quick credits. '
            'New merchant fraud ring in Philly area and prison commissary spike (UNION SUPPLY DIRECT) are '
            'adding EA/Non-reg volume. Compliance approval awaited for PVC clock reset post-recat fix.'
        ),
        'forward_looking': (
            'Recat loss fix (PVC clock reset post reclassification) pending compliance approval — '
            'expected to close $100K+/week exposure once approved. '
            'SWAT_Routing_gambling_MCC_v5 (5/19) updated threshold to ≥$1200 for gambling/lost-stolen claims. '
            'mFPF v2 SSD migration experiment (3 weeks from 5/22) monitoring for EA approval rate impact. '
            'Non-reg False Claim Rate Optimization: three opportunities targeting ~$150K/month scoped for Q2.'
        )
    },
    'approval_count': {
        'health': 'green',
        'commentary': (
            'FC 15D rate dropped sharply for the most recent mature cohort (4.74% → 3.29%, week of 4/26), '
            'across all products: ATM 5.5%, Debit 3.9%, Credit 2.3%, PF 1.3%. '
            'FC 7D ticked up 1.95% → 2.36% (dispute week 5/3 → 5/10), mainly ATM. '
            'Unit approval rate has stabilized after the April uptick driven by scam claim approvals; '
            'HIGH_RISK_Routing_Policy routing high-risk disputes to SWAT is keeping approval rates in check.'
        ),
        'forward_looking': (
            'priority policy non-safeguard v5 (mFPF v2 migration, 5/19) updates dispute queue prioritization '
            'to use improved FPF model — expected to improve SWAT agent efficiency. '
            '3DS Step-Up Choice and Shift-Deny-to-OOB experiments both in-flight — both aimed at higher '
            'approvals on newly-authenticated transactions; monitoring for downstream dispute impact. '
            'Negative Balance Early Reminders experiment (5/19–7/1) has dispute loss as a guardrail metric.'
        )
    },
    'approval_dollar': {
        'health': 'green',
        'commentary': (
            'Dollar approval rate stabilizing after the April +16% MoM surge. Loss rate at 11.0% (week 5/17) '
            'vs. 10.4% prior week — EA debit purchase and Instant Transfer the main drivers. '
            'Recat losses ($146K/week) inflating approved dollar amounts as non-reg claims shift to PVC-eligible '
            'EA/UT reasons. ~$5MM in fake scam claim credits issued YTD 2026 remains a key loss driver. '
            'ATOM v3 shipped with directional −22.5% dispute loss per user (~$300–700K/year annualized).'
        ),
        'forward_looking': (
            'Card Face Auth (Incode, 5/28) targets FPF deterrence for high-risk cohorts — '
            'building on P2P FaceAuth result (−23.6% annualized dispute loss). '
            'Inspector/Vero Chargeback Tooling fix in progress: recovering 20–30% of ~$1.6M stuck CB cases (~$100–400K loss reduction). '
            'Email Linkage Controls scoping underway: 8% of scam losses tied to shared-email clusters ($400K YTD). '
            'IVR Authentication feasibility check targeting delivery before June.'
            'Watch: Negative Balance Pre-Closure Notice experiment showed stat-sig reduction in NB closures — '
            'may modestly affect dispute patterns for members kept active longer.'
        )
    }
}

HEALTH_ICON = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
HEALTH_COLOR = {'green': '#16a34a', 'yellow': '#ca8a04', 'red': '#dc2626'}

run_date = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>T&S Metrics — Live Preview</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
  body{{background:#f5f6fa;color:#1a1a2e}}
  header{{background:#2d1b69;color:white;padding:14px 28px;display:flex;align-items:center;gap:12px}}
  header .logo{{font-size:20px;font-weight:700}} header .title{{font-size:15px;color:#c9b8f5}}
  header .run-date{{margin-left:auto;font-size:11px;color:#a78bfa}}
  .tabs{{background:white;border-bottom:1px solid #e2e5f0;padding:0 24px;display:flex;gap:2px;overflow-x:auto}}
  .tab{{padding:12px 16px;font-size:12.5px;font-weight:500;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}}
  .tab.active{{color:#2d1b69;border-bottom-color:#7c3aed;font-weight:600}}
  .tab:hover:not(.active){{color:#374151;background:#f9fafb}}
  .content{{display:none;padding:24px;max-width:1300px;margin:0 auto}} .content.active{{display:block}}
  .section-title{{font-size:14px;font-weight:600;color:#374151;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #e5e7eb}}
  .card{{background:white;border-radius:8px;border:1px solid #e5e7eb;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
  .chart-wrap{{position:relative;height:300px}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px}}
  th{{background:#f8f9fc;color:#6b7280;font-weight:600;text-align:right;padding:7px 12px;border-bottom:1px solid #e5e7eb;font-size:11px;text-transform:uppercase;letter-spacing:.3px}}
  th:first-child{{text-align:left}} td{{padding:6px 12px;border-bottom:1px solid #f3f4f6;text-align:right;color:#374151;font-size:12.5px}}
  td:first-child{{text-align:left;font-weight:500}} tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#f9fafb}} tr.total-row td{{font-weight:700;background:#f8f9fc;border-top:1px solid #d1d5db}}
  .stat-row{{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
  .stat{{background:white;border-radius:8px;border:1px solid #e5e7eb;padding:14px 18px;flex:1;min-width:130px}}
  .stat-label{{font-size:10.5px;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}}
  .stat-value{{font-size:20px;font-weight:700;color:#1f2937}}
  .stat-value.neutral{{color:#1f2937}}
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
  .summary-table th{{background:#1e293b;color:white;font-size:12px;padding:10px 14px;text-align:center}}
  .summary-table th:first-child{{text-align:left}}
  .summary-table td{{text-align:center;padding:9px 14px}}
  .summary-table td:first-child{{text-align:left;font-weight:600}}
  .summary-table tr:nth-child(even) td{{background:#f8fafc}}
  .mini-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:20px}}
  .mini-card{{background:white;border-radius:6px;border:1px solid #e5e7eb;padding:14px}}
  .mini-title{{font-size:11px;font-weight:600;color:#6b7280;margin-bottom:8px}}
  .mini-chart-wrap{{position:relative;height:160px}}
</style>
</head>
<body>
<header>
  <div class="logo">⬡ hex</div>
  <div class="title">T&S Metrics</div>
  <div class="run-date">Generated: {run_date}</div>
</header>
<div class="tabs">
  <div class="tab active"  onclick="showTab(0)">1. 7d Dispute Rate $</div>
  <div class="tab"         onclick="showTab(1)">2. Rate by Reason Type</div>
  <div class="tab"         onclick="showTab(2)">3. Approve / Deny</div>
  <div class="tab"         onclick="showTab(3)">4. 7d Unit Rate</div>
  <div class="tab"         onclick="showTab(4)">5. YoY Trend</div>
  <div class="tab"         onclick="showTab(5)">6. Summary</div>
</div>

<!-- TAB 1 -->
<div class="content active" id="tab0">
  <div class="stat-row">
    {stat_cell("Current Month", s1_cur)}
    {stat_cell_colored("YoY", s1_yoy, color_mom_yoy)}
    {stat_cell_colored("MoM", s1_mom, color_mom_yoy)}
    {stat_cell("Last Year Same Month", s1_py)}
  </div>
  <div class="card"><div class="section-title">7d Dispute Rate Trend (bps)</div>
    <div class="chart-wrap"><canvas id="c1"></canvas></div></div>
  <div class="card"><div class="section-title">All Seasoning Windows</div>
    <div style="overflow-x:auto"><table>
      <tr><th>Month</th><th>Offset</th><th>7d</th><th>14d</th><th>30d</th><th>45d</th><th>60d</th><th>90d</th><th>120d</th><th>150d</th><th>180d</th></tr>
      {''.join(f"<tr><td>{r['txn_month']}</td><td>{int(r['mth_offset'])}</td><td>{round(float(r['dispute_rate_7d']),2) if pd.notna(r.get('dispute_rate_7d')) else '—'}</td><td>{round(float(r['dispute_rate_14d']),2) if pd.notna(r.get('dispute_rate_14d')) else '—'}</td><td>{round(float(r['dispute_rate_30d']),2) if pd.notna(r.get('dispute_rate_30d')) else '—'}</td><td>{round(float(r['dispute_rate_45d']),2) if pd.notna(r.get('dispute_rate_45d')) else '—'}</td><td>{round(float(r['dispute_rate_60d']),2) if pd.notna(r.get('dispute_rate_60d')) else '—'}</td><td>{round(float(r['dispute_rate_90d']),2) if pd.notna(r.get('dispute_rate_90d')) else '—'}</td><td>{round(float(r['dispute_rate_120d']),2) if pd.notna(r.get('dispute_rate_120d')) else '—'}</td><td>{round(float(r['dispute_rate_150d']),2) if pd.notna(r.get('dispute_rate_150d')) else '—'}</td><td>{round(float(r['dispute_rate_180d']),2) if pd.notna(r.get('dispute_rate_180d')) else '—'}</td></tr>" for _, r in df1.iterrows())}
    </table></div></div>
</div>

<!-- TAB 2 -->
<div class="content" id="tab1">
  <div class="stat-row">
    {stat_cell("% UT — Current", s2_ut_cur)}
    {stat_cell("% UT — Last Month", s2_ut_lm)}
    {stat_cell("% UT — Last Year Same Month", s2_ut_py)}
  </div>
  <div class="stat-row">
    {stat_cell("% EA/NonReg — Current", s2_ea_cur)}
    {stat_cell("% EA/NonReg — Last Month", s2_ea_lm)}
    {stat_cell("% EA/NonReg — Last Year Same Month", s2_ea_py)}
  </div>
  <div class="card"><div class="section-title">7d Dispute Rate by Reason Type — Last 13 Mature Months</div>
    <div style="overflow-x:auto"><table>
      <tr><th>Month</th><th>EA</th><th>Non-reg</th><th>not_disputed</th><th>UT</th><th>Grand Total</th><th>% UT</th><th>% EA/NonReg</th></tr>
      {pivot2_rows_html()}
    </table></div></div>
  <div class="two-col">
    <div class="card"><div class="section-title">UT 7d Dispute Rate</div>
      <div class="chart-wrap"><canvas id="c2a"></canvas></div></div>
    <div class="card"><div class="section-title">EA + Non-reg 7d Dispute Rate</div>
      <div class="chart-wrap"><canvas id="c2b"></canvas></div></div>
  </div>
</div>

<!-- TAB 3 -->
<div class="content" id="tab2">
  <div class="stat-row">
    {stat_cell_colored("$ Approval — YoY", s3_dlr_yoy, color_approv)}
    {stat_cell_colored("$ Approval — MoM", s3_dlr_mom, color_approv)}
    {stat_cell("$ Approval — Current", s3_dlr_cur)}
    {stat_cell("$ Approval — Last Year Same Month", s3_dlr_py)}
  </div>
  <div class="stat-row">
    {stat_cell_colored("Unit Approval — YoY", s3_cnt_yoy, color_approv)}
    {stat_cell_colored("Unit Approval — MoM", s3_cnt_mom, color_approv)}
    {stat_cell("Unit Approval — Current", s3_cnt_cur)}
    {stat_cell("Unit Approval — Last Year Same Month", s3_cnt_py)}
  </div>
  <div class="two-col">
    <div class="card"><div class="section-title">Dispute Amount — Approve / Deny ($)</div><table>
      <tr><th>Resolution Month</th><th>Approve</th><th>Deny</th><th>Grand Total</th><th>Approval Rate</th></tr>
      {pivot3_rows_html('$')}
    </table></div>
    <div class="card"><div class="section-title">Dispute Count — Approve / Deny (Units)</div><table>
      <tr><th>Resolution Month</th><th>Approve</th><th>Deny</th><th>Grand Total</th><th>Approval Rate</th></tr>
      {pivot3_rows_html('#')}
    </table></div>
  </div>
  <div class="two-col">
    <div class="card"><div class="section-title">$ Approval Rate Trend</div>
      <div class="chart-wrap"><canvas id="c3a"></canvas></div></div>
    <div class="card"><div class="section-title">Unit Approval Rate Trend</div>
      <div class="chart-wrap"><canvas id="c3b"></canvas></div></div>
  </div>
</div>

<!-- TAB 4 -->
<div class="content" id="tab3">
  <div class="stat-row">
    {stat_cell("Current Month", s4_cur)}
    {stat_cell_colored("MoM", s4_mom, color_mom_yoy)}
    {stat_cell_colored("YoY", s4_yoy, color_mom_yoy)}
    {stat_cell("Last Year Same Month", s4_py)}
  </div>
  <div class="card"><div class="section-title">7d Dispute Unit Rate Trend (bps)</div>
    <div class="chart-wrap"><canvas id="c4"></canvas></div></div>
  <div class="card"><div class="section-title">Unit Rate Data</div><table>
    <tr><th>Month</th><th>Dispute 7d #</th><th>Trxn #</th><th>Disputed $</th><th>Trxn $</th><th>Rate ($)</th><th>Rate (bps)</th></tr>
    {pivot4_rows_html()}
  </table></div>
</div>

<!-- TAB 5 -->
<div class="content" id="tab4">
  <div class="card"><div class="section-title">Year-wise Monthly Trend of 7d $ Dispute Rate (bps)</div>
    <div class="chart-wrap" style="height:380px"><canvas id="c5"></canvas></div></div>
</div>

<!-- TAB 6 -->
<div class="content" id="tab5">
  <div class="card"><div class="section-title">Metrics Summary (Current Month vs Prior Periods)</div>
    <table class="summary-table">
      <tr><th>Metric</th><th>Current Month</th><th>MoM</th><th>YoY</th><th>Same Month, Last Year</th></tr>
      {summary_rows_html()}
    </table></div>
  <div class="card">
    <div class="section-title">Commentary &amp; Forward Looking — May 2026 Performance</div>
    <div style="font-size:11px;color:#9ca3af;margin-bottom:14px">
      Sources: T&amp;S Portfolio Metrics Review · Dispute Risk 2026 Tracker · #dispute-risk-analytics
    </div>
    {narrative_html()}
  </div>
  <div class="section-title" style="margin-top:4px">Trend Charts (for doc sharing)</div>
  <div class="mini-grid">
    <div class="mini-card"><div class="mini-title">7d Dispute $ Rate (bps)</div><div class="mini-chart-wrap"><canvas id="m1"></canvas></div></div>
    <div class="mini-card"><div class="mini-title">UT Rate</div><div class="mini-chart-wrap"><canvas id="m2"></canvas></div></div>
    <div class="mini-card"><div class="mini-title">EA + Non-reg Rate</div><div class="mini-chart-wrap"><canvas id="m3"></canvas></div></div>
    <div class="mini-card"><div class="mini-title">$ Approval Rate</div><div class="mini-chart-wrap"><canvas id="m4"></canvas></div></div>
    <div class="mini-card"><div class="mini-title">Unit Approval Rate</div><div class="mini-chart-wrap"><canvas id="m5"></canvas></div></div>
    <div class="mini-card"><div class="mini-title">7d Dispute Unit Rate (bps)</div><div class="mini-chart-wrap"><canvas id="m6"></canvas></div></div>
  </div>
</div>

<script>
const PURPLE='#7c3aed', BLUE='#60a5fa';
const months14   = {js_str_list(months14)};
const months13   = {js_str_list(months13)};
const months13r  = {js_str_list(months13r)};
const months14u  = {js_str_list(months14u)};
const d_7d       = {js_list(d_7d)};
const d_ut       = {js_list(d_ut)};
const d_ea       = {js_list(d_ea)};
const d_adlr     = {js_list(d_adlr)};
const d_acnt     = {js_list(d_acnt)};
const d_unit_bps = {js_list(d_unit_bps)};

function lineOpts(showLeg, yFmt) {{
  return {{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:showLeg,labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:24,font:{{size:12}}}}}},tooltip:{{mode:'index'}}}},
    scales:{{x:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}}}},
             y:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}},callback:yFmt||(v=>v)}}}}}},
    elements:{{point:{{radius:4}},line:{{tension:0}}}}}};
}}
function miniOpts() {{
  return {{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}},
    scales:{{x:{{display:false}},y:{{display:false}}}},
    elements:{{point:{{radius:0}},line:{{tension:0,borderWidth:1.8}}}}}};
}}
function mkDs(data,color,fill) {{
  return {{data,borderColor:color,backgroundColor:fill?color+'22':'transparent',fill:!!fill,pointBackgroundColor:color}};
}}
function miniDs(data) {{
  return {{data,borderColor:BLUE,backgroundColor:'transparent',pointBackgroundColor:BLUE}};
}}

new Chart('c1',{{type:'line',data:{{labels:months14,datasets:[{{...mkDs(d_7d,PURPLE,true),label:'7d Rate (bps)'}}]}},options:lineOpts(true)}});
new Chart('c2a',{{type:'line',data:{{labels:months13,datasets:[{{...mkDs(d_ut,'#dc2626',true),label:'UT Rate'}}]}},options:lineOpts(true,v=>v.toFixed(4)+'%')}});
new Chart('c2b',{{type:'line',data:{{labels:months13,datasets:[{{...mkDs(d_ea,'#2563eb',true),label:'EA+NonReg Rate'}}]}},options:lineOpts(true,v=>v.toFixed(4)+'%')}});
new Chart('c3a',{{type:'line',data:{{labels:months13r,datasets:[{{...mkDs(d_adlr,'#059669',true),label:'$ Approval Rate'}}]}},options:lineOpts(true,v=>(v*100).toFixed(0)+'%')}});
new Chart('c3b',{{type:'line',data:{{labels:months13r,datasets:[{{...mkDs(d_acnt,'#d97706',true),label:'Unit Approval Rate'}}]}},options:lineOpts(true,v=>(v*100).toFixed(0)+'%')}});
new Chart('c4',{{type:'line',data:{{labels:months14u,datasets:[{{...mkDs(d_unit_bps,PURPLE,true),label:'Unit Rate (bps)'}}]}},options:lineOpts(true,v=>v.toFixed(2)+' bps')}});
new Chart('c5',{{type:'line',data:{{labels:['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],
  datasets:{yoy_datasets_js()}}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'right',labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:24,font:{{size:12}}}}}}}},
    scales:{{x:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}}}},
             y:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}},title:{{display:true,text:'7d $ Dispute Rate (bps)',font:{{size:11}}}}}}}},
    elements:{{point:{{radius:4}},line:{{tension:0}}}}}}}});

new Chart('m1',{{type:'line',data:{{labels:months14, datasets:[miniDs(d_7d)]}},      options:miniOpts()}});
new Chart('m2',{{type:'line',data:{{labels:months13, datasets:[miniDs(d_ut)]}},      options:miniOpts()}});
new Chart('m3',{{type:'line',data:{{labels:months13, datasets:[miniDs(d_ea)]}},      options:miniOpts()}});
new Chart('m4',{{type:'line',data:{{labels:months13r,datasets:[miniDs(d_adlr)]}},    options:miniOpts()}});
new Chart('m5',{{type:'line',data:{{labels:months13r,datasets:[miniDs(d_acnt)]}},    options:miniOpts()}});
new Chart('m6',{{type:'line',data:{{labels:months14u,datasets:[miniDs(d_unit_bps)]}},options:miniOpts()}});

function showTab(i) {{
  document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('active',i===j));
  document.querySelectorAll('.content').forEach((c,j)=>c.classList.toggle('active',i===j));
}}
</script>
</body>
</html>"""

with open(OUT, 'w') as f: f.write(html)
print(f"Saved: {OUT}")
subprocess.run(['open', OUT])
print("Done — dashboard opened in browser.")
