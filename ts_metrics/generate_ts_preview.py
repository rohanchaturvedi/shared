#!/usr/bin/env python3
"""
T&S Metrics — Live HTML Preview Generator
=========================================
SETUP (one time only):
  1. Set your Snowflake email below.
  2. Run: pip install sqlalchemy snowflake-sqlalchemy pandas

USAGE:
  python3 generate_ts_preview.py
  Or just say "generate T&S HTML" to Claude.
"""

# ── CONFIG — update this before first run ─────────────────────────────────────
SNOWFLAKE_USER    = 'YOUR.EMAIL@CHIME.COM'   # <-- change this
SNOWFLAKE_ACCOUNT = 'CHIME'
SNOWFLAKE_WH      = 'ANALYTICS_WH'
SNOWFLAKE_ROLE    = 'SNOWFLAKE_PROD_ANALYTICS_PII_ROLE_OKTA'
# ─────────────────────────────────────────────────────────────────────────────

import sys, os, subprocess
import pandas as pd
from sqlalchemy import create_engine, text
from snowflake.sqlalchemy import URL

BASE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(BASE, 'ts_metrics_live.html')

def read_sql(fname):
    with open(os.path.join(BASE, fname)) as f:
        lines = [l for l in f.read().splitlines() if not l.strip().startswith('--')]
    return '\n'.join(lines)

def pct(v, dec=1):
    if v is None or pd.isna(v): return 'N/A'
    sign = '+' if float(v) >= 0 else ''
    return f"{sign}{float(v)*100:.{dec}f}%"

def bps(v, dec=2):
    if v is None or pd.isna(v): return 'N/A'
    return f"{float(v):.{dec}f} bps"

def pct_abs(v, dec=1):
    if v is None or pd.isna(v): return 'N/A'
    return f"{float(v)*100:.{dec}f}%"

def dollar(v):
    return f"${float(v)/1e6:,.1f}M"

def fmt_int(v):
    return f"{int(v):,}"

# ── Connect ───────────────────────────────────────────────────────────────────
print("Connecting to Snowflake (browser auth will open)...")
url = URL(user=SNOWFLAKE_USER, authenticator='externalbrowser',
          account=SNOWFLAKE_ACCOUNT, warehouse=SNOWFLAKE_WH, role=SNOWFLAKE_ROLE)
conn = create_engine(url).connect()
print("Connected. Running queries...")

def q(sql): return pd.read_sql(text(sql), conn)

df1 = q(read_sql('m1_dispute_rate_7d.sql'));   df1.columns = [c.lower() for c in df1.columns]
df2 = q(read_sql('m2_reason_type.sql'));       df2.columns = [c.lower() for c in df2.columns]; df2 = df2.sort_values('trxn_month').reset_index(drop=True)
df3 = q(read_sql('m3_approve_deny.sql'));      df3.columns = [c.lower() for c in df3.columns]; df3 = df3.sort_values('resolution_month').reset_index(drop=True)
df4 = q(read_sql('m4_unit_rate.sql'));         df4.columns = [c.lower() for c in df4.columns]; df4 = df4.sort_values('trxn_month').reset_index(drop=True)
df5 = q(read_sql('m5_yoy_trend.sql'));         df5.columns = [c.lower() for c in df5.columns]
print("All queries done. Building dashboard...")

# ── Extract values ────────────────────────────────────────────────────────────
m1_cur = df1[df1['mth_offset'] == -1].iloc[0]
m1_py  = df1[df1['mth_offset'] == -13].iloc[0] if -13 in df1['mth_offset'].values else None
s1_cur = bps(m1_cur['dispute_rate_7d']); s1_yoy = pct(m1_cur.get('yoy_7d')); s1_mom = pct(m1_cur.get('mom_7d'))
s1_py  = bps(m1_py['dispute_rate_7d']) if m1_py is not None else 'N/A'
months14 = df1['txn_month'].tolist()
d_7d = [round(float(v),2) if pd.notna(v) else None for v in df1['dispute_rate_7d']]

months13 = df2['trxn_month'].tolist()
d_ut = [round(float(v),6) if pd.notna(v) else None for v in df2['ut']]
d_ea = [round(float(v),6) if pd.notna(v) else None for v in df2['ea_plus_nonreg']]
m2_cur = df2.iloc[-1]; m2_lm = df2.iloc[-2]; m2_py = df2.iloc[0]
s2_ut_cur=pct_abs(m2_cur['pct_ut']); s2_ut_lm=pct_abs(m2_lm['pct_ut']); s2_ut_py=pct_abs(m2_py['pct_ut'])
s2_ea_cur=pct_abs(m2_cur['pct_ea_nonreg']); s2_ea_lm=pct_abs(m2_lm['pct_ea_nonreg']); s2_ea_py=pct_abs(m2_py['pct_ea_nonreg'])

months13r = df3['resolution_month'].tolist()
d_adlr = [round(float(v),4) if pd.notna(v) else None for v in df3['approval_rate_dlr']]
d_acnt = [round(float(v),4) if pd.notna(v) else None for v in df3['approval_rate_cnt']]
m3_cur = df3.iloc[-1]; m3_py = df3.iloc[0]
s3_dlr_cur=pct_abs(m3_cur['approval_rate_dlr'],0); s3_dlr_mom=pct(m3_cur.get('mom_dlr')); s3_dlr_yoy=pct(m3_cur.get('yoy_dlr')); s3_dlr_py=pct_abs(m3_py['approval_rate_dlr'],0)
s3_cnt_cur=pct_abs(m3_cur['approval_rate_cnt'],0); s3_cnt_mom=pct(m3_cur.get('mom_cnt')); s3_cnt_yoy=pct(m3_cur.get('yoy_cnt')); s3_cnt_py=pct_abs(m3_py['approval_rate_cnt'],0)

months14u  = df4['trxn_month'].tolist()
d_unit_bps = [round(float(v),2) if pd.notna(v) else None for v in df4['dispute_rate_cnt_bps']]
m4_cur=df4.iloc[-2]; m4_lm=df4.iloc[-3]; m4_py=df4.iloc[0]
cur_bps=float(m4_cur['dispute_rate_cnt_bps']); lm_bps=float(m4_lm['dispute_rate_cnt_bps']); py_bps=float(m4_py['dispute_rate_cnt_bps'])
s4_cur=f"{cur_bps:.2f} bps"; s4_mom=f"{(cur_bps-lm_bps)/cur_bps*100:+.1f}%" if cur_bps else 'N/A'
s4_yoy=f"{(cur_bps-py_bps)/cur_bps*100:+.1f}%" if cur_bps else 'N/A'; s4_py=f"{py_bps:.2f} bps"

yoy_years = sorted(df5['txn_year'].unique().tolist())
yoy_series = {}
for yr in yoy_years:
    sub = df5[df5['txn_year']==yr].sort_values('txn_month_num')
    vals = [None]*12
    for _, row in sub.iterrows():
        vals[int(row['txn_month_num'])-1] = round(float(row['dispute_rate_7d']),2) if pd.notna(row['dispute_rate_7d']) else None
    yoy_series[int(yr)] = vals
yr_colors = {yr:c for yr,c in zip(sorted(yoy_series.keys()),['#2563eb','#16a34a','#d97706','#111827','#dc2626','#7c3aed'])}

summary = [
    {'metric':'7d Dispute $ Rate',  'cur':s1_cur,  'mom':s1_mom,  'yoy':s1_yoy,  'py':s1_py},
    {'metric':'7d Dispute # Rate',  'cur':s4_cur,  'mom':s4_mom,  'yoy':s4_yoy,  'py':s4_py},
    {'metric':'% UT',               'cur':s2_ut_cur,'mom':s2_ut_lm,'yoy':s2_ut_py,'py':s2_ut_py},
    {'metric':'% EA / Non-reg',     'cur':s2_ea_cur,'mom':s2_ea_lm,'yoy':s2_ea_py,'py':s2_ea_py},
    {'metric':'Approval (# Rate)',  'cur':s3_cnt_cur,'mom':s3_cnt_mom,'yoy':s3_cnt_yoy,'py':s3_cnt_py},
    {'metric':'Approval ($ Rate)',  'cur':s3_dlr_cur,'mom':s3_dlr_mom,'yoy':s3_dlr_yoy,'py':s3_dlr_py},
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def js_list(a):  return '['+','.join('null' if v is None else str(v) for v in a)+']'
def js_sl(a):    return '['+','.join(f'"{v}"' for v in a)+']'

def color_std(val):
    try:
        n = float(str(val).replace('%','').replace('+','').replace(' bps',''))
        if n > 0: return 'color:#dc2626;font-weight:600'
        if n < 0: return 'color:#16a34a;font-weight:600'
    except: pass
    return 'font-weight:600'

def color_approv(val):  # approval rate up = red (more losses)
    return color_std(val)

def stat(label, val, style=''):
    return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value" style="color:#1f2937;font-size:20px;font-weight:700;{style}">{val}</div></div>'

def stat_c(label, val, fn):
    return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value" style="{fn(val)};font-size:20px;font-weight:700">{val}</div></div>'

def pivot2_html():
    rows=''
    for _,r in df2.iterrows():
        rows+=f"<tr><td>{str(r['trxn_month'])[:10]}</td><td>{pct_abs(r['ea'])}</td><td>{pct_abs(r['non_reg'])}</td><td>0.0000%</td><td>{pct_abs(r['ut'])}</td><td>{pct_abs(r['grand_total'])}</td><td>{pct_abs(r['pct_ut'])}</td><td>{pct_abs(r['pct_ea_nonreg'])}</td></tr>"
    gt=df2['grand_total'].sum()
    rows+=f'<tr class="tr"><td>Grand Total</td><td>{pct_abs(df2["ea"].sum())}</td><td>{pct_abs(df2["non_reg"].sum())}</td><td>0.0000%</td><td>{pct_abs(df2["ut"].sum())}</td><td>{pct_abs(gt)}</td><td>{pct_abs(df2["ut"].sum()/gt)}</td><td>{pct_abs((df2["ea"].sum()+df2["non_reg"].sum())/gt)}</td></tr>'
    return rows

def pivot3_html(kind):
    rows=''
    for _,r in df3.iterrows():
        if kind=='$': rows+=f"<tr><td>{str(r['resolution_month'])[:10]}</td><td>{dollar(r['approve_amt'])}</td><td>{dollar(r['deny_amt'])}</td><td>{dollar(r['total_amt'])}</td><td>{pct_abs(r['approval_rate_dlr'],0)}</td></tr>"
        else:         rows+=f"<tr><td>{str(r['resolution_month'])[:10]}</td><td>{fmt_int(r['approve_cnt'])}</td><td>{fmt_int(r['deny_cnt'])}</td><td>{fmt_int(r['total_cnt'])}</td><td>{pct_abs(r['approval_rate_cnt'],0)}</td></tr>"
    if kind=='$': rows+=f'<tr class="tr"><td>Grand Total</td><td>{dollar(df3["approve_amt"].sum())}</td><td>{dollar(df3["deny_amt"].sum())}</td><td>{dollar(df3["total_amt"].sum())}</td><td>{pct_abs(df3["approve_amt"].sum()/df3["total_amt"].sum(),0)}</td></tr>'
    else:         rows+=f'<tr class="tr"><td>Grand Total</td><td>{fmt_int(df3["approve_cnt"].sum())}</td><td>{fmt_int(df3["deny_cnt"].sum())}</td><td>{fmt_int(df3["total_cnt"].sum())}</td><td>{pct_abs(df3["approve_cnt"].sum()/df3["total_cnt"].sum(),0)}</td></tr>'
    return rows

def pivot4_html():
    rows=''
    for _,r in df4.iterrows():
        rows+=f"<tr><td>{str(r['trxn_month'])[:10]}</td><td>{fmt_int(r['dispute_7d_cnt'])}</td><td>{fmt_int(r['trxn_cnt'])}</td><td>{dollar(r['disputed_amt'])}</td><td>${float(r['trxn_amt'])/1e9:.1f}B</td><td>{float(r['dispute_rate_dlr'])*100:.4f}%</td><td>{float(r['dispute_rate_cnt_bps']):.2f} bps</td></tr>"
    return rows

def summary_html():
    rows=''
    for r in summary:
        m=r['metric']; is_pct='%' in m and 'Approval' not in m; is_app='Approval' in m
        if is_pct:   ms=ys='font-weight:600'
        elif is_app: ms=color_approv(r['mom']); ys=color_approv(r['yoy'])
        else:        ms=color_std(r['mom']);     ys=color_std(r['yoy'])
        rows+=f"<tr><td>{m}</td><td>{r['cur']}</td><td style='{ms}'>{r['mom']}</td><td style='{ys}'>{r['yoy']}</td><td>{r['py']}</td></tr>"
    return rows

def yoy_js():
    parts=[]
    for yr in sorted(yoy_series.keys()):
        col=yr_colors.get(yr,'#999')
        parts.append(f'{{label:"{yr}",data:{js_list(yoy_series[yr])},borderColor:"{col}",backgroundColor:"transparent",pointRadius:4}}')
    return '['+','.join(parts)+']'

def t1_table():
    rows=''
    for _,r in df1.iterrows():
        def f(col): return str(round(float(r[col]),2)) if col in r and pd.notna(r[col]) else '—'
        rows+=f"<tr><td>{r['txn_month']}</td><td>{int(r['mth_offset'])}</td><td>{f('dispute_rate_7d')}</td><td>{f('dispute_rate_14d')}</td><td>{f('dispute_rate_30d')}</td><td>{f('dispute_rate_45d')}</td><td>{f('dispute_rate_60d')}</td><td>{f('dispute_rate_90d')}</td><td>{f('dispute_rate_120d')}</td><td>{f('dispute_rate_150d')}</td><td>{f('dispute_rate_180d')}</td></tr>"
    return rows

run_date = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')

# ── HTML ──────────────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>T&S Metrics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
body{{background:#f5f6fa}}
header{{background:#2d1b69;color:white;padding:14px 28px;display:flex;align-items:center;gap:12px}}
header .logo{{font-size:20px;font-weight:700}} header .title{{font-size:15px;color:#c9b8f5}}
header .rd{{margin-left:auto;font-size:11px;color:#a78bfa}}
.tabs{{background:white;border-bottom:1px solid #e2e5f0;padding:0 24px;display:flex;gap:2px;overflow-x:auto}}
.tab{{padding:12px 16px;font-size:12.5px;font-weight:500;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}}
.tab.active{{color:#2d1b69;border-bottom-color:#7c3aed;font-weight:600}}
.tab:hover:not(.active){{color:#374151;background:#f9fafb}}
.content{{display:none;padding:24px;max-width:1300px;margin:0 auto}}.content.active{{display:block}}
.stit{{font-size:14px;font-weight:600;color:#374151;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #e5e7eb}}
.card{{background:white;border-radius:8px;border:1px solid #e5e7eb;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.cw{{position:relative;height:300px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{background:#f8f9fc;color:#6b7280;font-weight:600;text-align:right;padding:7px 12px;border-bottom:1px solid #e5e7eb;font-size:11px;text-transform:uppercase}}
th:first-child{{text-align:left}} td{{padding:6px 12px;border-bottom:1px solid #f3f4f6;text-align:right;color:#374151}}
td:first-child{{text-align:left;font-weight:500}} tr:hover td{{background:#f9fafb}}
tr.tr td{{font-weight:700;background:#f8f9fc;border-top:1px solid #d1d5db}}
.sr{{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}}
.stat{{background:white;border-radius:8px;border:1px solid #e5e7eb;padding:14px 18px;flex:1;min-width:130px}}
.stat-label{{font-size:10.5px;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
.smtab th{{background:#1e293b;color:white;font-size:12px;padding:10px 14px;text-align:center}}
.smtab th:first-child{{text-align:left}} .smtab td{{text-align:center;padding:9px 14px}}
.smtab td:first-child{{text-align:left;font-weight:600}} .smtab tr:nth-child(even) td{{background:#f8fafc}}
.mg{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:20px}}
.mc{{background:white;border-radius:6px;border:1px solid #e5e7eb;padding:14px}}
.mt{{font-size:11px;font-weight:600;color:#6b7280;margin-bottom:8px}} .mw{{position:relative;height:160px}}
</style></head><body>
<header><div class="logo">⬡ hex</div><div class="title">T&S Metrics</div><div class="rd">Generated: {run_date}</div></header>
<div class="tabs">
  <div class="tab active" onclick="showTab(0)">1. 7d Dispute Rate $</div>
  <div class="tab" onclick="showTab(1)">2. Rate by Reason Type</div>
  <div class="tab" onclick="showTab(2)">3. Approve / Deny</div>
  <div class="tab" onclick="showTab(3)">4. 7d Unit Rate</div>
  <div class="tab" onclick="showTab(4)">5. YoY Trend</div>
  <div class="tab" onclick="showTab(5)">6. Summary</div>
</div>

<div class="content active" id="tab0">
  <div class="sr">{stat("Current Month",s1_cur)}{stat_c("YoY",s1_yoy,color_std)}{stat_c("MoM",s1_mom,color_std)}{stat("Last Year Same Month",s1_py)}</div>
  <div class="card"><div class="stit">7d Dispute Rate Trend (bps)</div><div class="cw"><canvas id="c1"></canvas></div></div>
  <div class="card"><div class="stit">All Seasoning Windows</div><div style="overflow-x:auto"><table>
    <tr><th>Month</th><th>Offset</th><th>7d</th><th>14d</th><th>30d</th><th>45d</th><th>60d</th><th>90d</th><th>120d</th><th>150d</th><th>180d</th></tr>
    {t1_table()}
  </table></div></div>
</div>

<div class="content" id="tab1">
  <div class="sr">{stat("% UT — Current",s2_ut_cur)}{stat("% UT — Last Month",s2_ut_lm)}{stat("% UT — Last Year Same Month",s2_ut_py)}</div>
  <div class="sr">{stat("% EA/NonReg — Current",s2_ea_cur)}{stat("% EA/NonReg — Last Month",s2_ea_lm)}{stat("% EA/NonReg — Last Year Same Month",s2_ea_py)}</div>
  <div class="card"><div class="stit">7d Dispute Rate by Reason Type — Last 13 Mature Months</div><div style="overflow-x:auto"><table>
    <tr><th>Month</th><th>EA</th><th>Non-reg</th><th>not_disputed</th><th>UT</th><th>Grand Total</th><th>% UT</th><th>% EA/NonReg</th></tr>
    {pivot2_html()}
  </table></div></div>
  <div class="two">
    <div class="card"><div class="stit">UT 7d Dispute Rate</div><div class="cw"><canvas id="c2a"></canvas></div></div>
    <div class="card"><div class="stit">EA + Non-reg 7d Dispute Rate</div><div class="cw"><canvas id="c2b"></canvas></div></div>
  </div>
</div>

<div class="content" id="tab2">
  <div class="sr">{stat_c("$ Approval — YoY",s3_dlr_yoy,color_approv)}{stat_c("$ Approval — MoM",s3_dlr_mom,color_approv)}{stat("$ Approval — Current",s3_dlr_cur)}{stat("$ Approval — Last Year",s3_dlr_py)}</div>
  <div class="sr">{stat_c("Unit Approval — YoY",s3_cnt_yoy,color_approv)}{stat_c("Unit Approval — MoM",s3_cnt_mom,color_approv)}{stat("Unit Approval — Current",s3_cnt_cur)}{stat("Unit Approval — Last Year",s3_cnt_py)}</div>
  <div class="two">
    <div class="card"><div class="stit">Dispute Amount — Approve / Deny ($)</div><table>
      <tr><th>Resolution Month</th><th>Approve</th><th>Deny</th><th>Grand Total</th><th>Approval Rate</th></tr>{pivot3_html('$')}</table></div>
    <div class="card"><div class="stit">Dispute Count — Approve / Deny (Units)</div><table>
      <tr><th>Resolution Month</th><th>Approve</th><th>Deny</th><th>Grand Total</th><th>Approval Rate</th></tr>{pivot3_html('#')}</table></div>
  </div>
  <div class="two">
    <div class="card"><div class="stit">$ Approval Rate Trend</div><div class="cw"><canvas id="c3a"></canvas></div></div>
    <div class="card"><div class="stit">Unit Approval Rate Trend</div><div class="cw"><canvas id="c3b"></canvas></div></div>
  </div>
</div>

<div class="content" id="tab3">
  <div class="sr">{stat("Current Month",s4_cur)}{stat_c("MoM",s4_mom,color_std)}{stat_c("YoY",s4_yoy,color_std)}{stat("Last Year Same Month",s4_py)}</div>
  <div class="card"><div class="stit">7d Dispute Unit Rate Trend (bps)</div><div class="cw"><canvas id="c4"></canvas></div></div>
  <div class="card"><div class="stit">Unit Rate Data</div><table>
    <tr><th>Month</th><th>Dispute 7d #</th><th>Trxn #</th><th>Disputed $</th><th>Trxn $</th><th>Rate ($)</th><th>Rate (bps)</th></tr>
    {pivot4_html()}
  </table></div>
</div>

<div class="content" id="tab4">
  <div class="card"><div class="stit">Year-wise Monthly Trend of 7d $ Dispute Rate (bps)</div>
    <div class="cw" style="height:380px"><canvas id="c5"></canvas></div></div>
</div>

<div class="content" id="tab5">
  <div class="card"><div class="stit">Metrics Summary</div>
    <table class="smtab"><tr><th>Metric</th><th>Current Month</th><th>MoM</th><th>YoY</th><th>Same Month, Last Year</th></tr>
    {summary_html()}</table></div>
  <div class="stit" style="margin-top:4px">Trend Charts</div>
  <div class="mg">
    <div class="mc"><div class="mt">7d Dispute $ Rate (bps)</div><div class="mw"><canvas id="m1"></canvas></div></div>
    <div class="mc"><div class="mt">UT Rate</div><div class="mw"><canvas id="m2"></canvas></div></div>
    <div class="mc"><div class="mt">EA + Non-reg Rate</div><div class="mw"><canvas id="m3"></canvas></div></div>
    <div class="mc"><div class="mt">$ Approval Rate</div><div class="mw"><canvas id="m4"></canvas></div></div>
    <div class="mc"><div class="mt">Unit Approval Rate</div><div class="mw"><canvas id="m5"></canvas></div></div>
    <div class="mc"><div class="mt">7d Unit Rate (bps)</div><div class="mw"><canvas id="m6"></canvas></div></div>
  </div>
</div>

<script>
const P='#7c3aed',B='#60a5fa';
const m14={js_sl(months14)},m13={js_sl(months13)},m13r={js_sl(months13r)},m14u={js_sl(months14u)};
const d7={js_list(d_7d)},du={js_list(d_ut)},de={js_list(d_ea)},dad={js_list(d_adlr)},dac={js_list(d_acnt)},dub={js_list(d_unit_bps)};
function lo(leg,yf){{return{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:leg,labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:24,font:{{size:12}}}}}},tooltip:{{mode:'index'}}}},scales:{{x:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}}}},y:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}},callback:yf||(v=>v)}}}}}},elements:{{point:{{radius:4}},line:{{tension:0}}}}}};}}
function mo(){{return{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}},scales:{{x:{{display:false}},y:{{display:false}}}},elements:{{point:{{radius:0}},line:{{tension:0,borderWidth:1.8}}}}}};}}
function ds(d,c,f){{return{{data:d,borderColor:c,backgroundColor:f?c+'22':'transparent',fill:!!f,pointBackgroundColor:c}};}}
function md(d){{return{{data:d,borderColor:B,backgroundColor:'transparent',pointBackgroundColor:B}};}}
new Chart('c1',{{type:'line',data:{{labels:m14,datasets:[{{...ds(d7,P,true),label:'7d Rate (bps)'}}]}},options:lo(true)}});
new Chart('c2a',{{type:'line',data:{{labels:m13,datasets:[{{...ds(du,'#dc2626',true),label:'UT Rate'}}]}},options:lo(true,v=>v.toFixed(4)+'%')}});
new Chart('c2b',{{type:'line',data:{{labels:m13,datasets:[{{...ds(de,'#2563eb',true),label:'EA+NonReg'}}]}},options:lo(true,v=>v.toFixed(4)+'%')}});
new Chart('c3a',{{type:'line',data:{{labels:m13r,datasets:[{{...ds(dad,'#059669',true),label:'$ Approval'}}]}},options:lo(true,v=>(v*100).toFixed(0)+'%')}});
new Chart('c3b',{{type:'line',data:{{labels:m13r,datasets:[{{...ds(dac,'#d97706',true),label:'Unit Approval'}}]}},options:lo(true,v=>(v*100).toFixed(0)+'%')}});
new Chart('c4',{{type:'line',data:{{labels:m14u,datasets:[{{...ds(dub,P,true),label:'Unit Rate (bps)'}}]}},options:lo(true,v=>v.toFixed(2)+' bps')}});
new Chart('c5',{{type:'line',data:{{labels:['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],datasets:{yoy_js()}}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:24,font:{{size:12}}}}}}}},scales:{{x:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}}}},y:{{grid:{{color:'#f3f4f6'}},ticks:{{font:{{size:11}}}},title:{{display:true,text:'7d $ Dispute Rate (bps)',font:{{size:11}}}}}}}},elements:{{point:{{radius:4}},line:{{tension:0}}}}}}}});
['m1','m2','m3','m4','m5','m6'].forEach((id,i)=>{{
  const data=[dub,du,de,dad,dac,dub][i],labs=[m14u,m13,m13,m13r,m13r,m14u][i];
  new Chart(id,{{type:'line',data:{{labels:labs,datasets:[md(data)]}},options:mo()}});
}});
// fix: m1 uses d7/m14
new Chart('m1',{{type:'line',data:{{labels:m14,datasets:[md(d7)]}},options:mo()}});
function showTab(i){{document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('active',i===j));document.querySelectorAll('.content').forEach((c,j)=>c.classList.toggle('active',i===j));}}
</script></body></html>"""

with open(OUT,'w') as f: f.write(html)
print(f"Saved: {OUT}")
subprocess.run(['open', OUT])
print("Done.")
