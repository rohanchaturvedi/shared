"""
generate_clusterlink_html.py — Cluster Link Analysis dashboard v3

Storytelling-first dashboard with rich infographics, scatter plots, and a proper
network diagram of how clusters form.

Trigger: "run clusterlink analysis"
Output:  clusterlink/clusterlink_live.html
"""
import sys, os, re, subprocess, warnings, json
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(line_buffering=True)

import pandas as pd
import numpy as np
from collections import Counter
from sqlalchemy import create_engine, text
from snowflake.sqlalchemy import URL

# ── CONFIG ────────────────────────────────────────────────────────────────
# Set your Chime Snowflake email before the first run.
SNOWFLAKE_USER  = 'YOUR.EMAIL@CHIME.COM'

# Seed window — change here if you want a different disputer cohort.
# Re-run linkage_01_pii_pull.py + linkage_02_cluster.py first if you change this.
SEED_START      = '2025-10-01'
SEED_END        = '2026-03-31'
GIANT_THRESHOLD = 1000   # clusters >= this size flagged as device-chain noise

BASE        = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(BASE, 'outputs')
HTML_OUT    = os.path.join(BASE, 'clusterlink_live.html')

# ── Connect ─────────────────────────────────────────────────────────────────
print("Connecting to Snowflake...")
conn = create_engine(URL(
    user=SNOWFLAKE_USER, authenticator='externalbrowser',
    account='CHIME', warehouse='ANALYTICS_WH',
    role='SNOWFLAKE_PROD_ANALYTICS_PII_ROLE_OKTA'
)).connect()
print("Connected.\n")

clusters = pd.read_csv(f'{OUTPUT_DIR}/07_member_clusters.csv')
pii      = pd.read_csv(f'{OUTPUT_DIR}/06_pii_cleaned.csv')
print(f"Cluster mapping: {len(clusters):,} members, {(clusters['cluster_1']!=0).sum():,} in non-singleton clusters\n")

SEED_CTE = f"""
with disputers as (
    select distinct use_id as user_id
    from rest.test.ub_dispute_exception_reporting_base
    where claim_created_at::date >= '{SEED_START}'
      and claim_created_at::date <= '{SEED_END}'
      and dispute_amount >= 500
)
"""

# ── Pull all blocks (same as v2) ────────────────────────────────────────────
print("Block 1: enrollment + lifecycle...")
enroll = pd.read_sql(text(f"""
{SEED_CTE}
select d.user_id, m.first_account_created_ts::date as enrollment_date,
       datediff('day', m.first_account_created_ts, current_date()) as account_age_days,
       m.user_status, p.state_cd as enrollment_state, coalesce(p.is_funded, false) as is_funded
from disputers d
left join edw_db.core.dim_member_v10 m on d.user_id = m.user_id
left join edw_pii_db.core.dim_user_pii p on d.user_id = p.user_id
"""), conn)

print("Block 2: disputes...")
disputes = pd.read_sql(text(f"""
{SEED_CTE}
select d.user_id,
    count(distinct b.dispute_id) as dispute_count,
    sum(b.transaction_amt) as total_dispute_amt,
    sum(coalesce(b.final_credits, 0)) as total_fc,
    sum(coalesce(-b.negative_balance_amount, 0)) as total_nb,
    count(distinct case when b.reason_code in
        ('unauthorized_external_transfer','unauthorized_transaction','unauthorized_advance','unauthorized_transfer')
        then b.dispute_id end) as ut_dispute_count,
    count(distinct case when datediff('day', b.transaction_timestamp::date, b.claim_created_at::date) > 60 then b.dispute_id end) as late_notification_count,
    max(b.fpf_score) as max_fpf_score_dispute,
    max(b.mfpf_score) as max_mfpf_score,
    max(b.pvc_score) as max_pvc_score,
    avg(b.pvc_score) as avg_pvc_score,
    max(coalesce(b.is_hr, 0)) as is_hr_ever
from disputers d
left join rest.test.ub_dispute_exception_reporting_base b
       on d.user_id = b.use_id
      and b.claim_created_at::date >= '{SEED_START}'
      and b.claim_created_at::date <= '{SEED_END}'
group by d.user_id
"""), conn)

print("Block 3: auth / logins...")
auth = pd.read_sql(text(f"""
{SEED_CTE}
, aw as (
    select * from analytics.test.login_requests
    where login_started_at::date between '{SEED_START}' and '{SEED_END}'
      and user_id in (select user_id from disputers)
)
select d.user_id,
    count(l.account_access_attempt_id) as login_attempts,
    sum(coalesce(l.pw_fail, 0))        as pw_fail_count,
    sum(coalesce(l.scanid_required, 0)) as scanid_required_count,
    sum(coalesce(l.scanid_success, 0))  as scanid_success_count,
    sum(coalesce(l.mfa_required, 0))    as mfa_required_count,
    sum(coalesce(l.mfa_auth_success, 0)) as mfa_success_count
from disputers d
left join aw l on d.user_id = l.user_id
group by d.user_id
"""), conn)

print("Block 4: transactions + declines...")
txn = pd.read_sql(text(f"""
{SEED_CTE}
select d.user_id,
    count(*) as total_auth_attempts,
    count(case when a.response_cd in ('00','10') then 1 end) as approved_count,
    count(case when a.response_cd not in ('00','10') then 1 end) as declined_count,
    count(case when a.response_cd = '51' then 1 end) as nsf_decline_count,
    count(case when a.response_cd = '59' then 1 end) as fraud_decline_count,
    count(case when a.response_cd in ('14','54','82','N7') then 1 end) as cardtest_decline_count,
    count(case when a.response_cd in ('78','LK','5C','9G') then 1 end) as frozen_decline_count,
    sum(case when a.response_cd in ('00','10') then abs(a.req_amt) else 0 end) as approved_amt,
    sum(case when a.response_cd not in ('00','10') then abs(a.req_amt) else 0 end) as declined_amt
from disputers d
left join edw_db.core.fct_realtime_auth_event a
       on d.user_id = a.user_id
      and a.auth_event_created_ts::date between '{SEED_START}' and '{SEED_END}'
      and a.req_amt < 0 and a.original_auth_id = '0'
      and a.transaction_type = 'TRANSACTION_TYPE_PURCHASE'
group by d.user_id
"""), conn)

print("Block 5: direct deposits...")
dd = pd.read_sql(text(f"""
{SEED_CTE}
select d.user_id,
    coalesce(sum(dd.transaction_amount), 0) as total_dd_amt,
    count(dd.transaction_amount) as total_dd_count
from disputers d
left join risk.prod.spotme_eligible_direct_deposits dd
       on d.user_id = dd.user_id
      and dd.transaction_timestamp::date between '{SEED_START}' and '{SEED_END}'
group by d.user_id
"""), conn)

print("Block 6: FPF v2 per-member peak (max in window)...")
fpf = pd.read_sql(text(f"""
{SEED_CTE}
, fpf_peak as (
    select user_id, max(score) as fpf_v2_score
    from ml.model_inference.member_level_fpf_model_v2_score
    where predict_created_ts::date between '{SEED_START}' and '{SEED_END}'
      and user_id in (select user_id from disputers)
    group by user_id
)
select d.user_id, f.fpf_v2_score
from disputers d
left join fpf_peak f on d.user_id = f.user_id
"""), conn)

print("Block 7: funding + P2P...")
funding = pd.read_sql(text(f"""
{SEED_CTE}
select d.user_id,
    count(case when t.transaction_type='TRANSACTION_TYPE_DEPOSIT' then 1 end) as deposit_in_count,
    sum(case when t.transaction_type='TRANSACTION_TYPE_DEPOSIT' then abs(t.settled_amt) else 0 end) as deposit_in_amt,
    count(case when t.transaction_type='TRANSACTION_TYPE_TRANSFER' and t.acct_in_out='In' then 1 end) as transfer_in_count,
    sum(case when t.transaction_type='TRANSACTION_TYPE_TRANSFER' and t.acct_in_out='In' then abs(t.settled_amt) else 0 end) as transfer_in_amt,
    count(case when t.transaction_type='TRANSACTION_TYPE_TRANSFER' and t.acct_in_out='Out' then 1 end) as transfer_out_count,
    sum(case when t.transaction_type='TRANSACTION_TYPE_TRANSFER' and t.acct_in_out='Out' then abs(t.settled_amt) else 0 end) as transfer_out_amt,
    count(case when t.transaction_type='TRANSACTION_TYPE_WITHDRAWAL' then 1 end) as withdrawal_count,
    sum(case when t.transaction_type='TRANSACTION_TYPE_WITHDRAWAL' then abs(t.settled_amt) else 0 end) as withdrawal_amt,
    sum(case when t.acct_in_out='In'  then abs(t.settled_amt) else 0 end) as total_money_in,
    sum(case when t.acct_in_out='Out' then abs(t.settled_amt) else 0 end) as total_money_out
from disputers d
left join edw_db.core.ftr_transaction t
       on d.user_id = t.user_id
      and t.transaction_timestamp::date between '{SEED_START}' and '{SEED_END}'
group by d.user_id
"""), conn)

# ── Join + aggregate ────────────────────────────────────────────────────────
print("\nJoining + aggregating...")
for _df in (clusters, pii, enroll, disputes, auth, txn, dd, fpf, funding):
    _df['user_id'] = pd.to_numeric(_df['user_id'], errors='coerce').astype('Int64')

mem = (clusters
       .merge(pii, on='user_id', how='left')
       .merge(enroll, on='user_id', how='left')
       .merge(disputes, on='user_id', how='left')
       .merge(auth, on='user_id', how='left')
       .merge(txn, on='user_id', how='left')
       .merge(dd, on='user_id', how='left')
       .merge(fpf, on='user_id', how='left')
       .merge(funding, on='user_id', how='left'))

num_cols = mem.select_dtypes(include='number').columns
mem[num_cols] = mem[num_cols].fillna(0)
mem['is_cancelled'] = mem['user_status'].astype(str).str.lower().str.contains('cancel', na=False).astype(int)
mem['is_active']    = (mem['user_status'].astype(str).str.lower()=='active').astype(int)

m = mem[mem['cluster_1'] != 0].copy()
g = m.groupby('cluster_1').agg(
    cluster_size               = ('user_id', 'count'),
    n_active                   = ('is_active', 'sum'),
    n_cancelled                = ('is_cancelled', 'sum'),
    n_funded                   = ('is_funded', 'sum'),
    dispute_count              = ('dispute_count', 'sum'),
    total_dispute_amt          = ('total_dispute_amt', 'sum'),
    total_fc                   = ('total_fc', 'sum'),
    total_nb                   = ('total_nb', 'sum'),
    ut_dispute_count           = ('ut_dispute_count', 'sum'),
    late_notification_count    = ('late_notification_count', 'sum'),
    n_hr_flagged               = ('is_hr_ever', 'sum'),
    login_attempts             = ('login_attempts', 'sum'),
    pw_fail_count              = ('pw_fail_count', 'sum'),
    scanid_required_count      = ('scanid_required_count', 'sum'),
    scanid_success_count       = ('scanid_success_count', 'sum'),
    mfa_required_count         = ('mfa_required_count', 'sum'),
    mfa_success_count          = ('mfa_success_count', 'sum'),
    total_auth_attempts        = ('total_auth_attempts', 'sum'),
    approved_count             = ('approved_count', 'sum'),
    declined_count             = ('declined_count', 'sum'),
    approved_amt               = ('approved_amt', 'sum'),
    declined_amt               = ('declined_amt', 'sum'),
    nsf_decline_count          = ('nsf_decline_count', 'sum'),
    fraud_decline_count        = ('fraud_decline_count', 'sum'),
    cardtest_decline_count     = ('cardtest_decline_count', 'sum'),
    frozen_decline_count       = ('frozen_decline_count', 'sum'),
    total_dd_amt               = ('total_dd_amt', 'sum'),
    total_dd_count             = ('total_dd_count', 'sum'),
    # Score aggregation = AVG of each member's peak/max score.
    # Per-member values are already max-per-member from the SQL above.
    cluster_avg_peak_fpf_dispute = ('max_fpf_score_dispute', 'mean'),
    cluster_avg_peak_mfpf        = ('max_mfpf_score', 'mean'),
    cluster_avg_peak_pvc         = ('max_pvc_score', 'mean'),
    cluster_avg_peak_fpf_v2      = ('fpf_v2_score', 'mean'),
    deposit_in_count           = ('deposit_in_count', 'sum'),
    deposit_in_amt             = ('deposit_in_amt', 'sum'),
    transfer_in_count          = ('transfer_in_count', 'sum'),
    transfer_in_amt            = ('transfer_in_amt', 'sum'),
    transfer_out_count         = ('transfer_out_count', 'sum'),
    transfer_out_amt           = ('transfer_out_amt', 'sum'),
    withdrawal_count           = ('withdrawal_count', 'sum'),
    withdrawal_amt             = ('withdrawal_amt', 'sum'),
    total_money_in             = ('total_money_in', 'sum'),
    total_money_out            = ('total_money_out', 'sum'),
).reset_index()

def safe_div(a, b):
    return np.where(np.array(b) > 0, np.array(a) / np.array(b), 0.0)

g['loss_amt']             = g['total_fc'] + g['total_nb']
g['loss_rate']            = safe_div(g['loss_amt'], g['total_dispute_amt'])
g['dispute_$_per_member'] = safe_div(g['total_dispute_amt'], g['cluster_size'])
g['loss_$_per_member']    = safe_div(g['loss_amt'], g['cluster_size'])
g['decline_rate']         = safe_div(g['declined_amt'], g['approved_amt'] + g['declined_amt'])
g['fraud_decline_rate']   = safe_div(g['fraud_decline_count'], g['total_auth_attempts'])
g['cardtest_rate']        = safe_div(g['cardtest_decline_count'], g['total_auth_attempts'])
g['nsf_decline_rate']     = safe_div(g['nsf_decline_count'], g['total_auth_attempts'])
g['pw_fail_rate']         = safe_div(g['pw_fail_count'], g['login_attempts'])
g['scanid_required_rate'] = safe_div(g['scanid_required_count'], g['login_attempts'])
g['scanid_success_rate']  = safe_div(g['scanid_success_count'], g['scanid_required_count'])
g['cancelled_pct']        = safe_div(g['n_cancelled'], g['cluster_size'])
g['ut_share']             = safe_div(g['ut_dispute_count'], g['dispute_count'])
g['p2p_in_per_member']    = safe_div(g['transfer_in_amt'], g['cluster_size'])
g['p2p_in_share_of_inflow'] = safe_div(g['transfer_in_amt'], g['total_money_in'])
g['dd_share_of_inflow']   = safe_div(g['total_dd_amt'], g['total_money_in'])
g['dispute_to_inflow_ratio'] = safe_div(g['total_dispute_amt'], g['total_money_in'])

# Structure
struct = (m.groupby('cluster_1').agg(
    n_distinct_emails  = ('email_clean',  lambda s: s.dropna().nunique()),
    n_distinct_phones  = ('phone_clean',  lambda s: s.dropna().nunique()),
    n_distinct_ssn     = ('ssn_clean',    lambda s: s.dropna().nunique()),
    n_distinct_address = ('address_clean',lambda s: s.dropna().nunique()),
    n_distinct_states  = ('enrollment_state', lambda s: s.dropna().nunique()),
    top_state          = ('enrollment_state', lambda s: s.value_counts().index[0] if len(s.dropna()) else None),
    top_state_share    = ('enrollment_state', lambda s: s.value_counts(normalize=True).iloc[0] if len(s.dropna()) else 0),
).reset_index())
g = g.merge(struct, on='cluster_1', how='left')

# Email analytics
print("Email text analytics...")
DISPOSABLE = {'mailinator.com','guerrillamail.com','10minutemail.com','tempmail.com',
              'throwaway.email','yopmail.com','trashmail.com','sharklasers.com',
              'getairmail.com','maildrop.cc','fakeinbox.com','spam4.me','grr.la'}
def detect_seq_suffix(emails):
    pat = re.compile(r'^(.*?)(\d+)(@.*)$')
    groups = {}
    for e in emails:
        if not isinstance(e, str): continue
        mt = pat.match(e)
        if not mt: continue
        key = (mt.group(1), mt.group(3))
        groups.setdefault(key, set()).add(int(mt.group(2)))
    return any(len(v) >= 3 for v in groups.values())

text_rows = []
for cid, sub in m.groupby('cluster_1'):
    emails = sub['email_clean'].dropna().tolist()
    if not emails:
        text_rows.append({'cluster_1': cid, 'top_domain': None, 'top_domain_share': 0,
                          'sequential_suffix_flag': False, 'email_digit_ratio': 0, 'disposable_email_count': 0})
        continue
    domains = [e.split('@')[1] for e in emails if '@' in e]
    dom_counts = Counter(domains)
    top_dom, top_n = dom_counts.most_common(1)[0]
    locals_ = [e.split('@')[0] for e in emails if '@' in e]
    digit_ratio = np.mean([sum(c.isdigit() for c in x)/max(len(x),1) for x in locals_]) if locals_ else 0
    text_rows.append({
        'cluster_1': cid, 'top_domain': top_dom, 'top_domain_share': top_n / len(emails),
        'sequential_suffix_flag': detect_seq_suffix(emails),
        'email_digit_ratio': digit_ratio,
        'disposable_email_count': sum(1 for d in domains if d.lower() in DISPOSABLE),
    })
g = g.merge(pd.DataFrame(text_rows), on='cluster_1', how='left')

# ── Composite risk + signal definitions ─────────────────────────────────────
print("Composite risk + savings projections...")
actionable = g[(g['cluster_size'] >= 6) & (g['cluster_size'] < GIANT_THRESHOLD)].copy()
giants    = g[g['cluster_size'] >= GIANT_THRESHOLD].copy()

SIGNAL_DEFS = [
    ('Total Loss $',           'loss_amt',              None),
    ('Loss Rate %',            'loss_rate',             'total_dispute_amt>10000'),
    ('Dispute $/Member',       'dispute_$_per_member',  None),
    ('Decline Rate %',         'decline_rate',          'total_auth_attempts>100'),
    ('Fraud-Flagged Decline %','fraud_decline_rate',    'total_auth_attempts>50'),
    ('Cancelled %',            'cancelled_pct',         None),
    ('Avg Peak FPF v2 Score',       'cluster_avg_peak_fpf_v2',    None),
    ('Top-State Share %',      'top_state_share',       None),
]
SIGNAL_THRESHOLD_TEXT = {
    'Total Loss $':           'Top 50 clusters ranked by total FC+NB loss',
    'Loss Rate %':            'Top 50 by loss/dispute$, only clusters with dispute $ > $10,000',
    'Dispute $/Member':       'Top 50 by avg dispute $ per cluster member',
    'Decline Rate %':         'Top 50 by declines/(approved+declined), only clusters with > 100 auth attempts',
    'Fraud-Flagged Decline %':'Top 50 by RC=59 declines / total auths, only clusters with > 50 auths',
    'Cancelled %':            'Top 50 by % of cluster members already cancelled by Chime',
    'Avg Peak FPF v2 Score':       'Top 50 by avg-of-per-member-peak FPF v2 score in the cluster',
    'Top-State Share %':      'Top 50 by what % of members are in the same single state',
}

def signal_subset(actionable_df, filt):
    if filt == 'total_dispute_amt>10000':
        return actionable_df[actionable_df['total_dispute_amt'] > 10000]
    if filt == 'total_auth_attempts>100':
        return actionable_df[actionable_df['total_auth_attempts'] > 100]
    if filt == 'total_auth_attempts>50':
        return actionable_df[actionable_df['total_auth_attempts'] > 50]
    return actionable_df

composite = pd.DataFrame({'cluster_1': actionable['cluster_1'].values}).set_index('cluster_1')
single_signal_savings = []
for label, col, filt in SIGNAL_DEFS:
    sub = signal_subset(actionable, filt)
    top50 = sub.sort_values(col, ascending=False).head(50)
    composite[label] = composite.index.isin(top50['cluster_1'].tolist()).astype(int)
    single_signal_savings.append({
        'signal': label,
        'col': col,
        'addressable_loss': float(top50['loss_amt'].sum()),
        'n_clusters': len(top50),
        'n_members': int(top50['cluster_size'].sum()),
    })
single_signal_df = pd.DataFrame(single_signal_savings).sort_values('addressable_loss', ascending=False)

composite['signals_hit'] = composite.iloc[:, :8].sum(axis=1)
actionable = actionable.merge(composite[['signals_hit']].reset_index(), on='cluster_1', how='left')

# Per-attribute breakdown (cluster_2..6 — only attribute clusters of size ≥6)
attr_breakdown = []
mem_with_loss = mem.copy()
mem_with_loss['member_loss'] = mem_with_loss['total_fc'].fillna(0) + mem_with_loss['total_nb'].fillna(0)
for attr_col, attr_label in [('cluster_2','Email-only'),('cluster_3','Address-only'),
                              ('cluster_4','SSN-only'),('cluster_5','Phone-only'),('cluster_6','Device-only')]:
    sub_mem = mem_with_loss[mem_with_loss[attr_col] != 0]
    sizes = sub_mem.groupby(attr_col).size()
    big_cluster_ids = sizes[sizes >= 6].index
    big = sub_mem[sub_mem[attr_col].isin(big_cluster_ids)]
    attr_breakdown.append({
        'attribute': attr_label,
        'n_clusters_ge6': int((sizes >= 6).sum()),
        'n_members_in_ge6': int(big['user_id'].count()),
        'total_loss_ge6': float(big['member_loss'].sum()),
        'largest_cluster_size': int(sizes.max()) if len(sizes) else 0,
    })
attr_df = pd.DataFrame(attr_breakdown)

# Savings by threshold
savings = []
for n in range(1, 9):
    sub = actionable[actionable['signals_hit'] >= n]
    savings.append({
        'threshold': n, 'n_clusters': len(sub), 'n_members': int(sub['cluster_size'].sum()),
        'loss_amt': float(sub['loss_amt'].sum()), 'dispute_amt': float(sub['total_dispute_amt'].sum()),
    })
savings_df = pd.DataFrame(savings)

# ── Aggregate stats ─────────────────────────────────────────────────────────
total_members      = len(mem)
total_in_clusters  = int((mem['cluster_1'] != 0).sum())
total_clusters     = int((g['cluster_size'] >= 2).sum())
actionable_count   = len(actionable)
composite_3plus    = int((actionable['signals_hit'] >= 3).sum())
total_cluster_disp = int(g['total_dispute_amt'].sum())
total_cluster_loss = int(g['loss_amt'].sum())
giant_count        = len(giants)

loss_sorted = actionable.sort_values('loss_amt', ascending=False).reset_index(drop=True)
loss_sorted['cum_loss']     = loss_sorted['loss_amt'].cumsum()
loss_sorted['cum_loss_pct'] = loss_sorted['cum_loss'] / max(loss_sorted['loss_amt'].sum(), 1)
loss_sorted['rank'] = range(1, len(loss_sorted)+1)
top10_loss_share = float(loss_sorted.head(10)['loss_amt'].sum() / max(actionable['loss_amt'].sum(), 1))
top25_loss_share = float(loss_sorted.head(25)['loss_amt'].sum() / max(actionable['loss_amt'].sum(), 1))

# Tables — ALL TOP 10
high_conv = actionable[actionable['signals_hit'] >= 3].sort_values(['signals_hit','loss_amt'], ascending=[False, False]).head(10)
top_loss = actionable.sort_values('loss_amt', ascending=False).head(10)
top_loss_rate = actionable[actionable['total_dispute_amt']>10000].sort_values('loss_rate', ascending=False).head(10)
top_per_member = actionable.sort_values('dispute_$_per_member', ascending=False).head(10)
top_decline_rate = actionable[actionable['total_auth_attempts']>100].sort_values('decline_rate', ascending=False).head(10)
top_fraud_decline = actionable[actionable['total_auth_attempts']>50].sort_values('fraud_decline_rate', ascending=False).head(10)
top_scanid = actionable[actionable['scanid_required_count']>5].sort_values('scanid_required_rate', ascending=False).head(10)
p2p_heavy = actionable[actionable['transfer_in_amt'] > 5000].sort_values('p2p_in_share_of_inflow', ascending=False).head(10)
no_dd = actionable[actionable['total_dd_amt'] < 100].sort_values('loss_amt', ascending=False).head(10)
high_dispute_to_inflow = actionable[actionable['total_money_in'] > 5000].sort_values('dispute_to_inflow_ratio', ascending=False).head(10)
actionable['addr_density'] = safe_div(actionable['cluster_size'], actionable['n_distinct_address'])
addr_bound = actionable[actionable['n_distinct_address']>0].sort_values('addr_density', ascending=False).head(10)
all_cancelled = actionable[actionable['cancelled_pct'] >= 0.85].sort_values('loss_amt', ascending=False).head(10)
email_flags = actionable[
    (actionable['top_domain_share'] >= 0.8) |
    (actionable['sequential_suffix_flag'] == True) |
    (actionable['email_digit_ratio'] >= 0.3) |
    (actionable['disposable_email_count'] > 0)
].sort_values('loss_amt', ascending=False).head(10)
single_state = actionable[actionable['top_state_share'] >= 1.0].sort_values('total_dispute_amt', ascending=False).head(10)
state_summary = actionable.groupby('top_state').agg(
    n_clusters=('cluster_1','count'), n_members=('cluster_size','sum'),
    total_dispute=('total_dispute_amt','sum'), total_loss=('loss_amt','sum'),
    high_conv_clusters=('signals_hit', lambda s: int((s>=3).sum())),
).reset_index().sort_values('total_dispute', ascending=False).head(10)

# Funding breakdown
funding_breakdown = {
    'Direct Deposit':       float(actionable['total_dd_amt'].sum()),
    'P2P / Transfers In':   float(actionable['transfer_in_amt'].sum()),
    'Deposits (cash/check)':float(actionable['deposit_in_amt'].sum()),
}
funding_breakdown['Other'] = max(float(actionable['total_money_in'].sum() - sum(funding_breakdown.values())), 0)

# Cluster size distribution
size_buckets = pd.cut(g['cluster_size'],
                      bins=[0,1,2,5,10,20,50,100,500,float('inf')],
                      labels=['1','2','3–5','6–10','11–20','21–50','51–100','101–500','500+'])
size_dist = g.groupby(size_buckets, observed=False).agg(
    n_clusters=('cluster_size','count'), n_members=('cluster_size','sum'),
    total_loss=('loss_amt','sum')).reset_index()
size_dist.columns = ['cluster_size','n_clusters','n_members','total_loss']

# Top example for tab 6 — MUST have material loss (≥$5K) to qualify as smoking gun
SG_MIN_LOSS = 5000
sg_addr = actionable[(actionable['n_distinct_address']>0) & (actionable['loss_amt'] >= SG_MIN_LOSS)].copy()
sg_addr = sg_addr.sort_values('addr_density', ascending=False)
sg_cancelled = actionable[(actionable['cancelled_pct'] >= 0.85) & (actionable['loss_amt'] >= SG_MIN_LOSS)].sort_values('loss_amt', ascending=False)
sg_email = actionable[
    ((actionable['sequential_suffix_flag']==True) | (actionable['top_domain_share']>=0.9)) &
    (actionable['loss_amt'] >= SG_MIN_LOSS)
].sort_values('loss_amt', ascending=False)
ex0 = sg_addr.iloc[0] if len(sg_addr) > 0 else None
ex1 = sg_cancelled.iloc[0] if len(sg_cancelled) > 0 else None
ex2 = sg_email.iloc[0] if len(sg_email) > 0 else None

# Funding stats
total_inflow = float(actionable['total_money_in'].sum())
p2p_inflow   = float(actionable['transfer_in_amt'].sum())
dd_inflow    = float(actionable['total_dd_amt'].sum())
p2p_share    = p2p_inflow / max(total_inflow, 1)
dd_share     = dd_inflow / max(total_inflow, 1)
no_dd_clusters = int((actionable['total_dd_amt'] < 100).sum())
disp_inflow_med = float(actionable['dispute_to_inflow_ratio'].median())
p2p_only_clusters = int(((actionable['transfer_in_amt'] > 5000) & (actionable['total_dd_amt'] < 100)).sum())
p2p_only_loss = float(actionable[(actionable['transfer_in_amt'] > 5000) & (actionable['total_dd_amt'] < 100)]['loss_amt'].sum())

# Top-state high-conviction count
top_state_hc = actionable[actionable['signals_hit'] >= 3].groupby('top_state').size().reset_index(name='n').sort_values('n', ascending=False).head(10)

# ── Render helpers ──────────────────────────────────────────────────────────
def fmt_int(v):
    if pd.isna(v): return '—'
    return f"{int(v):,}"
def fmt_amt(v):
    if pd.isna(v) or v == 0: return '$0'
    return f"${int(v):,}"
def fmt_amt_short(v):
    if pd.isna(v) or v == 0: return '$0'
    v = float(v)
    if abs(v) >= 1e6: return f"${v/1e6:.2f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.0f}"
def fmt_pct(v, dec=1):
    if pd.isna(v): return '—'
    return f"{v*100:.{dec}f}%"
def fmt_score(v, dec=3):
    if pd.isna(v) or v == 0: return '—'
    return f"{v:.{dec}f}"
def render_rows(df, cols_fmt):
    rows = []
    for _, r in df.iterrows():
        cells = [f"<td>{fn(r.get(col))}</td>" for col, fn in cols_fmt]
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return ''.join(rows)

def row_high_conv(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int), ('signals_hit', fmt_int),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt), ('loss_rate', fmt_pct),
        ('top_state', lambda v: v if isinstance(v, str) else '—'),
        ('cancelled_pct', fmt_pct), ('cluster_avg_peak_fpf_v2', fmt_score),
        ('n_distinct_address', fmt_int),
    ])
def row_loss(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('total_dispute_amt', fmt_amt), ('total_fc', fmt_amt), ('total_nb', fmt_amt),
        ('loss_amt', fmt_amt), ('loss_rate', fmt_pct), ('dispute_$_per_member', fmt_amt),
    ])
def row_decline(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('total_auth_attempts', fmt_int), ('declined_count', fmt_int),
        ('decline_rate', fmt_pct), ('fraud_decline_count', fmt_int),
        ('cardtest_decline_count', fmt_int), ('nsf_decline_count', fmt_int),
        ('loss_amt', fmt_amt),
    ])
def row_scanid(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('login_attempts', fmt_int), ('scanid_required_count', fmt_int),
        ('scanid_required_rate', lambda v: fmt_pct(v,2)),
        ('scanid_success_rate', fmt_pct), ('loss_amt', fmt_amt),
    ])
def row_p2p(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('transfer_in_amt', fmt_amt), ('transfer_out_amt', fmt_amt),
        ('total_money_in', fmt_amt),
        ('p2p_in_share_of_inflow', fmt_pct), ('dd_share_of_inflow', fmt_pct),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt),
    ])
def row_no_dd(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('total_dd_amt', fmt_amt), ('transfer_in_amt', fmt_amt),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt),
        ('cluster_avg_peak_fpf_v2', fmt_score),
    ])
def row_dispute_inflow(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('total_money_in', fmt_amt), ('total_dispute_amt', fmt_amt),
        ('dispute_to_inflow_ratio', fmt_pct), ('loss_amt', fmt_amt),
    ])
def row_smoking(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('n_distinct_address', fmt_int), ('n_distinct_emails', fmt_int),
        ('n_distinct_phones', fmt_int), ('n_distinct_ssn', fmt_int),
        ('addr_density', lambda v: f"{v:.1f}" if pd.notna(v) else '—'),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt),
    ])
def row_cancelled(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('n_cancelled', fmt_int), ('cancelled_pct', fmt_pct),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt),
        ('cluster_avg_peak_fpf_v2', fmt_score),
    ])
def row_email(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('top_domain', lambda v: v if isinstance(v, str) else '—'),
        ('top_domain_share', fmt_pct),
        ('sequential_suffix_flag', lambda v: 'YES' if v else '—'),
        ('email_digit_ratio', fmt_pct),
        ('loss_amt', fmt_amt), ('loss_rate', fmt_pct),
    ])
def row_state(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('top_state', lambda v: v if isinstance(v, str) else '—'),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt),
        ('cancelled_pct', fmt_pct), ('cluster_avg_peak_fpf_v2', fmt_score),
    ])
def row_state_summary(df):
    return render_rows(df, [
        ('top_state', lambda v: v if isinstance(v, str) else '—'),
        ('n_clusters', fmt_int), ('n_members', fmt_int),
        ('high_conv_clusters', fmt_int),
        ('total_dispute', fmt_amt), ('total_loss', fmt_amt),
    ])
def row_size_dist(df):
    descriptions = {
        '1': '(singletons — no shared attributes, excluded from analysis)',
        '2': 'pairs — could be coincidence (family, roommates)',
        '3–5': 'small groups — possible family or weak signal',
        '6–10': 'small ring — interesting if other signals fire',
        '11–20': 'medium ring — investigate',
        '21–50': 'large ring — high-conviction territory',
        '51–100': 'very large ring — rare, usually real fraud',
        '101–500': 'mega-ring — almost always organized fraud',
        '500+': 'GIANT — device-chain noise, excluded from actionable',
    }
    rows = []
    for _, r in df.iterrows():
        desc = descriptions.get(str(r['cluster_size']), '')
        rows.append(f"<tr><td>{r['cluster_size']}</td><td style='font-size:11px;color:#64748b;font-weight:400;text-align:left'>{desc}</td>"
                    f"<td>{fmt_int(r['n_clusters'])}</td><td>{fmt_int(r['n_members'])}</td><td>{fmt_amt(r['total_loss'])}</td></tr>")
    return ''.join(rows)
def row_giants(df):
    return render_rows(df, [
        ('cluster_1', fmt_int), ('cluster_size', fmt_int),
        ('total_dispute_amt', fmt_amt), ('loss_amt', fmt_amt), ('loss_rate', fmt_pct),
        ('n_distinct_address', fmt_int), ('cluster_avg_peak_fpf_v2', fmt_score),
    ])

# ── Pre-compute JS data ─────────────────────────────────────────────────────
js_savings_thresholds = json.dumps([int(r) for r in savings_df['threshold']])
js_savings_loss       = json.dumps([float(round(r/1e3,1)) for r in savings_df['loss_amt']])
js_size_labels        = json.dumps([str(r) for r in size_dist['cluster_size']])
js_size_clusters      = json.dumps([int(r) for r in size_dist['n_clusters']])
js_size_members       = json.dumps([int(r) for r in size_dist['n_members']])
js_pareto_x           = json.dumps([int(r) for r in loss_sorted.head(100)['rank']])
js_pareto_y           = json.dumps([float(round(v*100,1)) for v in loss_sorted.head(100)['cum_loss_pct']])
js_funding_labels     = json.dumps(list(funding_breakdown.keys()))
js_funding_values     = json.dumps([float(round(v/1e6, 2)) for v in funding_breakdown.values()])
js_state_labels       = json.dumps(state_summary['top_state'].fillna('?').tolist())
js_state_dispute      = json.dumps([float(round(v/1e3,1)) for v in state_summary['total_dispute']])
js_state_loss         = json.dumps([float(round(v/1e3,1)) for v in state_summary['total_loss']])
js_state_hc_labels    = json.dumps(top_state_hc['top_state'].fillna('?').tolist())
js_state_hc_n         = json.dumps([int(r) for r in top_state_hc['n']])
js_signal_dist        = json.dumps([int((actionable['signals_hit']==n).sum()) for n in range(0,9)])
js_top10_clusters     = json.dumps([f"#{int(r['cluster_1'])}" for _, r in loss_sorted.head(10).iterrows()])
js_top10_losses       = json.dumps([float(round(r/1e3,1)) for r in loss_sorted.head(10)['loss_amt']])

# Scatter helpers
def scatter_data(df_, x_col, y_col, x_mul=100, max_n=200):
    return [{'x': float(round(r[x_col]*x_mul, 2)), 'y': float(round(r[y_col], 0))} for _, r in df_.head(max_n).iterrows()]

js_scatter_decline  = json.dumps(scatter_data(actionable[actionable['total_auth_attempts']>100], 'decline_rate', 'loss_amt'))
js_scatter_fraud    = json.dumps(scatter_data(actionable[actionable['total_auth_attempts']>50], 'fraud_decline_rate', 'loss_amt'))
js_scatter_scanid   = json.dumps(scatter_data(actionable[actionable['scanid_required_count']>5], 'scanid_required_rate', 'loss_amt'))
js_scatter_pwfail   = json.dumps(scatter_data(actionable[actionable['login_attempts']>50], 'pw_fail_rate', 'loss_amt'))
js_scatter_p2p_dd   = json.dumps([
    {'x': float(round(r['p2p_in_share_of_inflow']*100, 2)),
     'y': float(round(r['dd_share_of_inflow']*100, 2)),
     'r': max(min(np.log1p(r['loss_amt'])*0.6, 18), 3)}
    for _, r in actionable[actionable['total_money_in']>1000].head(200).iterrows()
])
js_scatter_ssn_addr = json.dumps([
    {'x': int(r['n_distinct_address']), 'y': int(r['n_distinct_ssn']), 'r': max(min(np.log1p(r['cluster_size'])*2, 18), 4)}
    for _, r in actionable.head(300).iterrows()
])

# Single-signal & per-attribute data
js_single_signals     = json.dumps([r['signal'] for _, r in single_signal_df.iterrows()])
js_single_savings     = json.dumps([float(round(r['addressable_loss']/1e3, 1)) for _, r in single_signal_df.iterrows()])
js_attr_labels        = json.dumps(attr_df['attribute'].tolist())
js_attr_loss          = json.dumps([float(round(r/1e3, 1)) for r in attr_df['total_loss_ge6']])
js_attr_n             = json.dumps([int(r) for r in attr_df['n_clusters_ge6']])

# Density distribution
js_density_dist = json.dumps([float(round(v,2)) for v in actionable['addr_density'].fillna(0).clip(0,15).tolist()])

# NEW: Tab 2 combo data (cumulative # clusters at each threshold)
js_savings_clusters_cum = json.dumps([int(r) for r in savings_df['n_clusters']])

# NEW: Tab 3 stacked FC/NB for top 10 clusters
top10 = loss_sorted.head(10)
js_top10_labels = json.dumps([f"#{int(r['cluster_1'])}" for _, r in top10.iterrows()])
js_top10_fc = json.dumps([float(round(r/1e3,1)) for r in top10['total_fc']])
js_top10_nb = json.dumps([float(round(r/1e3,1)) for r in top10['total_nb']])

# NEW: Tab 4 stacked decline-code mix for top 10 decline-rate clusters
top10_decline = top_decline_rate
js_decline_labels = json.dumps([f"#{int(r['cluster_1'])}" for _, r in top10_decline.iterrows()])
js_decline_nsf      = json.dumps([int(r['nsf_decline_count']) for _, r in top10_decline.iterrows()])
js_decline_fraud    = json.dumps([int(r['fraud_decline_count']) for _, r in top10_decline.iterrows()])
js_decline_cardtest = json.dumps([int(r['cardtest_decline_count']) for _, r in top10_decline.iterrows()])
js_decline_frozen   = json.dumps([int(r['frozen_decline_count']) for _, r in top10_decline.iterrows()])
js_decline_other    = json.dumps([int(max(r['declined_count'] - r['nsf_decline_count'] - r['fraud_decline_count'] - r['cardtest_decline_count'] - r['frozen_decline_count'], 0)) for _, r in top10_decline.iterrows()])

# NEW: Tab 5 stacked funding mix for top 10 P2P-heavy clusters
top10_p2p = p2p_heavy
js_funding_top_labels = json.dumps([f"#{int(r['cluster_1'])}" for _, r in top10_p2p.iterrows()])
js_funding_top_dd     = json.dumps([float(round(r['total_dd_amt']/1e3, 1)) for _, r in top10_p2p.iterrows()])
js_funding_top_p2p    = json.dumps([float(round(r['transfer_in_amt']/1e3, 1)) for _, r in top10_p2p.iterrows()])
js_funding_top_dep    = json.dumps([float(round(r['deposit_in_amt']/1e3, 1)) for _, r in top10_p2p.iterrows()])
js_funding_top_other  = json.dumps([float(round(max(r['total_money_in'] - r['total_dd_amt'] - r['transfer_in_amt'] - r['deposit_in_amt'], 0)/1e3, 1)) for _, r in top10_p2p.iterrows()])

# NEW: Tab 6 radar — anatomy of top 3 smoking-gun examples
def radar_data(row):
    if row is None: return [0,0,0,0,0]
    sz = max(int(row['cluster_size']), 1)
    return [
        round(int(row['n_distinct_emails']) / sz, 3),
        round(int(row['n_distinct_phones']) / sz, 3),
        round(int(row['n_distinct_address']) / sz, 3),
        round(int(row['n_distinct_ssn']) / sz, 3),
        round(int(row['n_distinct_states']) / sz, 3) if 'n_distinct_states' in row.index else 0,
    ]
js_radar_labels = json.dumps(['Emails / member', 'Phones / member', 'Addresses / member', 'SSNs / member', 'States / member'])
js_radar_ex0 = json.dumps(radar_data(ex0))
js_radar_ex1 = json.dumps(radar_data(ex1))
js_radar_ex2 = json.dumps(radar_data(ex2))
js_radar_ex0_name = json.dumps(f"#{int(ex0['cluster_1'])} (synth-ID)" if ex0 is not None else "—")
js_radar_ex1_name = json.dumps(f"#{int(ex1['cluster_1'])} (cancelled ring)" if ex1 is not None else "—")
js_radar_ex2_name = json.dumps(f"#{int(ex2['cluster_1'])} (email batch)" if ex2 is not None else "—")
# Dispute-to-inflow histogram buckets
inflow_buckets = pd.cut(actionable['dispute_to_inflow_ratio'].clip(0,5),
                        bins=[0,0.25,0.5,1.0,2.0,3.0,5.0,np.inf],
                        labels=['<25%','25–50%','50–100%','100–200%','200–300%','300–500%','>500%'])
inflow_dist = actionable.groupby(inflow_buckets, observed=False).size().reset_index(name='n')
js_inflow_labels = json.dumps([str(r) for r in inflow_dist[inflow_dist.columns[0]]])
js_inflow_n      = json.dumps([int(r) for r in inflow_dist['n']])

# Signal threshold table HTML
signal_rows_html = ''.join([
    f'<tr><td><b>{label}</b></td><td>{SIGNAL_THRESHOLD_TEXT[label]}</td></tr>'
    for (label, col, filt) in SIGNAL_DEFS
])

# Per-attribute breakdown HTML cards
attr_card_html = ''
for _, r in attr_df.iterrows():
    attr_card_html += f'''
    <div class="attr-card">
      <div class="attr-label">{r['attribute']}</div>
      <div class="attr-loss">{fmt_amt_short(r['total_loss_ge6'])}</div>
      <div class="attr-sub">{fmt_int(r['n_clusters_ge6'])} clusters (≥6 members) · {fmt_int(r['n_members_in_ge6'])} members<br/>Largest: {fmt_int(r['largest_cluster_size'])} members</div>
    </div>'''

# Headlines
sav3 = int(savings_df[savings_df['threshold']==3]['loss_amt'].iloc[0])
sav4 = int(savings_df[savings_df['threshold']==4]['loss_amt'].iloc[0])
sav5 = int(savings_df[savings_df['threshold']==5]['loss_amt'].iloc[0])
sav6 = int(savings_df[savings_df['threshold']==6]['loss_amt'].iloc[0])
sav8 = int(savings_df[savings_df['threshold']==8]['loss_amt'].iloc[0])
n3 = int(savings_df[savings_df['threshold']==3]['n_clusters'].iloc[0])
n4 = int(savings_df[savings_df['threshold']==4]['n_clusters'].iloc[0])
n5 = int(savings_df[savings_df['threshold']==5]['n_clusters'].iloc[0])
strongest_single = single_signal_df.iloc[0]

# ── Dynamic "Insight from this run" data points ──────────────────────────
# Tab 1: which size bucket holds most loss
size_dist_nonsingle = size_dist[~size_dist['cluster_size'].isin(['1','500+'])].copy()
top_size_row = size_dist_nonsingle.loc[size_dist_nonsingle['total_loss'].idxmax()] if len(size_dist_nonsingle) else None

# Tab 4: worst card-testing cluster + worst fraud-flagged decline cluster
cardtest_worst = actionable.sort_values('cardtest_decline_count', ascending=False).head(1)
cardtest_top = cardtest_worst.iloc[0] if len(cardtest_worst) else None
fraud_decline_worst = actionable[actionable['total_auth_attempts']>50].sort_values('fraud_decline_count', ascending=False).head(1)
fraud_decline_top = fraud_decline_worst.iloc[0] if len(fraud_decline_worst) else None

# Tab 5: P2P-only state concentration
p2p_only_sub = actionable[(actionable['transfer_in_amt'] > 5000) & (actionable['total_dd_amt'] < 100)]
p2p_only_state_top = p2p_only_sub.groupby('top_state').agg(n=('cluster_1','count'), loss=('loss_amt','sum')).reset_index().sort_values('loss', ascending=False).head(1)
p2p_only_state_row = p2p_only_state_top.iloc[0] if len(p2p_only_state_top) else None

# Tab 7: top high-conviction state
top_hc_state_row = top_state_hc.iloc[0] if len(top_state_hc) else None

# Tab 3 dynamic — biggest single cluster
top1_loss_row = loss_sorted.iloc[0] if len(loss_sorted) else None

run_date = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')

# ── Consolidated data tables (moved from tabs 1–7 to Tab 8) ─────────────
data_tables_html = f"""
  <div class="card">
    <div class="section-title">📋 Full Data Tables — leaderboards by signal</div>
    <div style="font-size:12.5px;color:#475569;margin-bottom:14px;line-height:1.55">
      All leaderboards from the visual tabs are consolidated here. Use this as the reference companion when reviewing
      a specific narrative tab. Each table is grouped by its source tab below.
    </div>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 2 — Loss Savings</h3>
    <div class="section-title">High-Conviction Clusters (≥3 of 8 signals, top 10)</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Signals Hit</th><th>Dispute $</th><th>Loss $</th><th>Loss Rate</th><th>Top State</th><th>% Cancelled</th><th>Avg Peak FPF v2</th><th># Addresses</th></tr>
      {row_high_conv(high_conv)}
    </table>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 3 — Loss Concentration</h3>
    <div class="section-title" style="margin-top:14px">Top 10 by Total Loss ($)</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Dispute $</th><th>FC</th><th>NB</th><th>Loss</th><th>Loss Rate</th><th>$/Member</th></tr>
      {row_loss(top_loss)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 by Loss Rate (dispute $ &gt; $10K only)</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Dispute $</th><th>FC</th><th>NB</th><th>Loss</th><th>Loss Rate</th><th>$/Member</th></tr>
      {row_loss(top_loss_rate)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 by Dispute $ per Member</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Dispute $</th><th>FC</th><th>NB</th><th>Loss</th><th>Loss Rate</th><th>$/Member</th></tr>
      {row_loss(top_per_member)}
    </table>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 4 — Behavioral Patterns</h3>
    <div class="section-title" style="margin-top:14px">Top 10 by Decline Rate</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Auths</th><th>Declined</th><th>Decline Rate</th><th>Fraud-flagged</th><th>Card-test</th><th>NSF</th><th>Loss $</th></tr>
      {row_decline(top_decline_rate)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 by Fraud-Flagged Decline Rate</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Auths</th><th>Declined</th><th>Decline Rate</th><th>Fraud-flagged</th><th>Card-test</th><th>NSF</th><th>Loss $</th></tr>
      {row_decline(top_fraud_decline)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 by ScanID-Required Rate</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Logins</th><th>ScanID Req</th><th>Req Rate</th><th>Success Rate</th><th>Loss $</th></tr>
      {row_scanid(top_scanid)}
    </table>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 5 — Funding &amp; P2P</h3>
    <div class="section-title" style="margin-top:14px">Top 10 P2P-Heavy Clusters</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>P2P In $</th><th>P2P Out $</th><th>Total Inflow</th><th>P2P Share</th><th>DD Share</th><th>Dispute $</th><th>Loss $</th></tr>
      {row_p2p(p2p_heavy)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 No-DD Clusters (&lt;$100 DD over 6 months)</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Total DD $</th><th>P2P In $</th><th>Dispute $</th><th>Loss $</th><th>Avg Peak FPF v2</th></tr>
      {row_no_dd(no_dd)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 by Dispute-to-Inflow Ratio</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Total Inflow</th><th>Dispute $</th><th>Ratio</th><th>Loss $</th></tr>
      {row_dispute_inflow(high_dispute_to_inflow)}
    </table>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 6 — Smoking-Gun Rings</h3>
    <div class="section-title" style="margin-top:14px">Top 10 Address-Density Rings</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th># Addresses</th><th># Emails</th><th># Phones</th><th># SSNs</th><th>Density</th><th>Dispute $</th><th>Loss $</th></tr>
      {row_smoking(addr_bound)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 100% Cancelled Clusters</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th># Cancelled</th><th>% Cancelled</th><th>Dispute $</th><th>Loss $</th><th>Avg Peak FPF v2</th></tr>
      {row_cancelled(all_cancelled)}
    </table>
    <div class="section-title" style="margin-top:18px">Top 10 Email Anomaly Clusters</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Top Domain</th><th>Domain Share</th><th>Seq Suffix?</th><th>Digit Ratio</th><th>Loss $</th><th>Loss Rate</th></tr>
      {row_email(email_flags)}
    </table>
  </div>

  <div class="card">
    <h3 style="font-size:14px;color:#1e3a8a;margin-bottom:10px;font-weight:700">↳ From Tab 7 — Geographic</h3>
    <div class="section-title" style="margin-top:14px">Top 10 100% Single-State Clusters</div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Top State</th><th>Dispute $</th><th>Loss $</th><th>% Cancelled</th><th>Avg Peak FPF v2</th></tr>
      {row_state(single_state)}
    </table>
    <div class="section-title" style="margin-top:18px">State-Level Summary (top 10)</div>
    <table>
      <tr><th>State</th><th>Clusters</th><th>Members</th><th>High-Conv</th><th>Dispute $</th><th>Loss $</th></tr>
      {row_state_summary(state_summary)}
    </table>
  </div>
"""

# ── HTML ────────────────────────────────────────────────────────────────────
print("\nBuilding HTML...\n")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Cluster Link Analysis — Live</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
  body{{background:#f5f6fa;color:#1a1a2e}}
  header{{background:linear-gradient(135deg,#1e3a8a,#3b82f6);color:white;padding:18px 28px;display:flex;align-items:center;gap:12px}}
  header .logo{{font-size:22px;font-weight:700}}
  header .title{{font-size:13.5px;color:#dbeafe;font-weight:500}}
  header .meta{{margin-left:auto;font-size:11px;color:#bfdbfe;text-align:right}}
  .tabs{{background:white;border-bottom:1px solid #e2e5f0;padding:0 24px;display:flex;gap:2px;overflow-x:auto;position:sticky;top:0;z-index:10;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
  .tab{{padding:13px 16px;font-size:12.5px;font-weight:500;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}}
  .tab.active{{color:#1e3a8a;border-bottom-color:#3b82f6;font-weight:600;background:#f8fafc}}
  .tab:hover:not(.active){{color:#374151;background:#f9fafb}}
  .content{{display:none;padding:24px;max-width:1400px;margin:0 auto}} .content.active{{display:block}}

  .story{{background:white;border-radius:10px;padding:24px 28px;margin-bottom:20px;border-left:5px solid #3b82f6;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
  .story h2{{font-size:20px;color:#1e3a8a;margin-bottom:10px;font-weight:700}}
  .story p{{font-size:13.5px;color:#374151;line-height:1.65;margin-bottom:8px}}
  .story p:last-child{{margin-bottom:0}} .story b{{color:#1e3a8a}}
  .story .takeaway{{background:#eff6ff;border-radius:6px;padding:12px 16px;margin-top:14px;font-size:13px;color:#1e40af}}
  .story .takeaway b{{color:#1e3a8a}}

  .stat-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}}
  .stat{{background:white;border-radius:10px;border:1px solid #e5e7eb;padding:16px 18px;position:relative;overflow:hidden}}
  .stat::before{{content:'';position:absolute;top:0;left:0;width:4px;height:100%;background:#3b82f6}}
  .stat.red::before{{background:#dc2626}} .stat.amber::before{{background:#f59e0b}}
  .stat.green::before{{background:#059669}} .stat.purple::before{{background:#7c3aed}}
  .stat-label{{font-size:10.5px;color:#9ca3af;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;font-weight:600}}
  .stat-value{{font-size:24px;font-weight:700;color:#111827;line-height:1}}
  .stat-value.red{{color:#dc2626}} .stat-value.amber{{color:#d97706}} .stat-value.green{{color:#059669}} .stat-value.blue{{color:#1e3a8a}}
  .stat-sub{{font-size:10.5px;color:#9ca3af;margin-top:6px;line-height:1.3}}

  .callout{{background:#fef3c7;border-left:4px solid #f59e0b;border-radius:6px;padding:14px 18px;margin-bottom:14px}}
  .callout .head{{font-size:12px;font-weight:700;color:#92400e;letter-spacing:.4px;text-transform:uppercase;margin-bottom:6px}}
  .callout p{{font-size:13px;color:#451a03;line-height:1.55}}
  .callout.red{{background:#fee2e2;border-left-color:#dc2626}}
  .callout.red .head{{color:#991b1b}} .callout.red p{{color:#7f1d1d}}
  .callout.green{{background:#d1fae5;border-left-color:#059669}}
  .callout.green .head{{color:#065f46}} .callout.green p{{color:#065f46}}
  .callout.blue{{background:#dbeafe;border-left-color:#3b82f6}}
  .callout.blue .head{{color:#1e40af}} .callout.blue p{{color:#1e3a8a}}

  .card{{background:white;border-radius:10px;border:1px solid #e5e7eb;padding:18px 22px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
  .section-title{{font-size:14px;font-weight:600;color:#1e3a8a;margin-bottom:6px;padding-bottom:8px;border-bottom:2px solid #eff6ff}}
  .axis-hint{{font-size:11px;color:#64748b;font-style:italic;margin-bottom:10px;margin-top:-2px}}
  .chart-wrap{{position:relative;height:300px}}
  .chart-wrap.tall{{height:380px}} .chart-wrap.short{{height:220px}}
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .three-col{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}

  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{background:#f8f9fc;color:#475569;font-weight:600;text-align:right;padding:8px 10px;border-bottom:2px solid #e2e8f0;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap}}
  th:first-child{{text-align:left}} td{{padding:7px 10px;border-bottom:1px solid #f1f5f9;text-align:right;color:#1f2937;font-size:12px}}
  td:first-child{{text-align:left;font-weight:600;color:#1e3a8a}}
  tr:hover td{{background:#fef9c3}}

  /* Per-attribute cards */
  .attr-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}}
  .attr-card{{background:linear-gradient(180deg,#f8fafc,#eef2ff);border-radius:10px;border:1px solid #e2e8f0;padding:14px;text-align:center}}
  .attr-label{{font-size:11px;font-weight:600;color:#475569;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px}}
  .attr-loss{{font-size:18px;font-weight:700;color:#dc2626;margin-bottom:6px}}
  .attr-sub{{font-size:10.5px;color:#64748b;line-height:1.4}}

  /* Example callout cards (smoking gun) */
  .ex-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-bottom:16px}}
  .ex-card{{background:linear-gradient(180deg,#fee2e2,#fef3c7);border-radius:10px;border:1px solid #f59e0b;padding:16px}}
  .ex-card .ex-head{{font-size:11px;text-transform:uppercase;color:#92400e;font-weight:700;letter-spacing:.4px;margin-bottom:6px}}
  .ex-card .ex-cluster{{font-size:20px;font-weight:700;color:#1e3a8a;margin-bottom:6px}}
  .ex-card .ex-detail{{font-size:12.5px;color:#451a03;line-height:1.5}}
  .ex-card .ex-loss{{font-size:16px;font-weight:700;color:#dc2626;margin-top:8px}}

  /* Glossary */
  .glossary-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
  .gloss-card{{background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;padding:14px 16px}}
  .gloss-card h4{{font-size:13px;color:#1e3a8a;margin-bottom:6px}}
  .gloss-card p{{font-size:12.5px;color:#475569;line-height:1.55}}
  .gloss-card code{{background:#e0e7ff;color:#3730a3;padding:1px 6px;border-radius:3px;font-size:11.5px}}

  /* Pipeline infographic */
  .pipeline{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:8px 0 4px;flex-wrap:wrap}}
  .step{{flex:1;min-width:140px;background:white;border:2px solid #3b82f6;border-radius:10px;padding:12px;text-align:center}}
  .step .step-n{{display:inline-block;width:24px;height:24px;line-height:24px;border-radius:50%;background:#3b82f6;color:white;font-weight:700;font-size:12px;margin-bottom:6px}}
  .step .step-t{{font-size:12px;font-weight:600;color:#1e3a8a;margin-bottom:3px}}
  .step .step-d{{font-size:10.5px;color:#64748b;line-height:1.4}}
  .arrow{{font-size:18px;color:#94a3b8;font-weight:700}}

  /* Network */
  .network-wrap{{background:#f8fafc;border-radius:10px;padding:24px 18px;text-align:center;margin-bottom:18px}}
  .network-caption{{font-size:12.5px;color:#475569;margin-top:12px;font-style:italic;line-height:1.55}}

  table.signal-table th{{background:#1e3a8a;color:white;text-transform:none;font-size:11.5px;letter-spacing:.2px}}
  table.signal-table td{{font-size:12.5px}}
  table.signal-table code{{background:#eef2ff;color:#3730a3;padding:1px 6px;border-radius:3px;font-size:11.5px}}

  /* EDA */
  .eda-row{{display:grid;grid-template-columns:1fr 2fr;gap:16px;padding:10px 0;border-bottom:1px dashed #e2e8f0}}
  .eda-row:last-child{{border-bottom:none}}
  .eda-label{{font-weight:600;color:#1e3a8a;font-size:13px}}
  .eda-val{{font-size:12.5px;color:#475569;line-height:1.55}}
  .eda-val code{{background:#eef2ff;color:#3730a3;padding:1px 6px;border-radius:3px;font-size:11.5px}}
</style>
</head>
<body>
<header>
  <div class="logo">◉ clusterlink</div>
  <div class="title">PII Linkage Cluster Analysis · disputers Oct 2025 – Mar 2026, dispute $ ≥ $500</div>
  <div class="meta">Generated: {run_date}<br/>Data through {SEED_END}</div>
</header>

<div class="tabs">
  <div class="tab active" onclick="showTab(0)">1. Overview</div>
  <div class="tab"        onclick="showTab(1)">2. Loss Savings</div>
  <div class="tab"        onclick="showTab(2)">3. Loss Concentration</div>
  <div class="tab"        onclick="showTab(3)">4. Behavioral Patterns</div>
  <div class="tab"        onclick="showTab(4)">5. Funding &amp; P2P</div>
  <div class="tab"        onclick="showTab(5)">6. Smoking-Gun Rings</div>
  <div class="tab"        onclick="showTab(6)">7. Geographic</div>
  <div class="tab"        onclick="showTab(7)">8. Glossary, Methodology &amp; Data</div>
</div>

<!-- TAB 1: OVERVIEW -->
<div class="content active" id="tab0">
  <div class="story">
    <h2>What this dashboard is</h2>
    <p>This is a <b>fraud linkage analysis</b> over Chime members who filed ≥$500 disputes between Oct 2025 and Mar 2026.
    Instead of evaluating members individually, we link members who share PII attributes — <b>email, phone, SSN digest, address, or device</b> —
    into <b>clusters</b>, then analyze the cluster as a single unit.</p>
    <p>Why? Because individual-level fraud signals miss <b>coordinated rings</b>. A member who looks borderline alone often
    looks unmistakably fraudulent when you discover they share a device with 30 other accounts, 80% of whom already have disputes.</p>
    <div class="takeaway"><b>How to read this:</b> 8 tabs in a story arc. Tab 1 sets up the problem and the headline.
    Tab 2 is the business case. Tabs 3–7 walk through the evidence. Tab 8 has the methodology, glossary, and all data tables.</div>
  </div>

  <div class="card" style="border-left:5px solid #dc2626">
    <div class="section-title" style="border-bottom-color:#fee2e2">The bottom line — what this analysis found</div>
    <div style="font-size:13.5px;color:#374151;line-height:1.75">
      <p style="margin-bottom:10px"><b>1. Cluster-based detection finds rings that individual-level scoring misses.</b>
      We linked <b>{fmt_int(total_in_clusters)} of {fmt_int(total_members)} disputers</b> ({total_in_clusters/total_members*100:.1f}%)
      into <b>{fmt_int(total_clusters)} clusters</b> via shared email, phone, SSN, address, or device — clusters that fraud rules don't
      see when they only look at one member at a time.</p>

      <p style="margin-bottom:10px"><b>2. The opportunity is concrete.</b> Across {fmt_int(actionable_count)} actionable clusters
      (6–{GIANT_THRESHOLD-1} members), total losses sum to <b>{fmt_amt_short(actionable['loss_amt'].sum())}</b>.
      Of these, <b>{fmt_int(composite_3plus)} clusters fire 3+ of our 8 risk signals</b> — the high-conviction rings — and
      account for <b>{fmt_amt(sav3)}</b> in addressable losses.</p>

      <p style="margin-bottom:10px"><b>3. Even a simple rule would capture most of the value.</b> The single strongest signal
      ({strongest_single['signal']}) addresses <b>{fmt_amt(strongest_single['addressable_loss'])}</b> by itself —
      essentially matching the multi-signal composite. A "top-50 clusters by loss" policy could be deployed today as a first pass.</p>

      <p style="margin-bottom:10px"><b>4. Concrete rings are sitting in the data.</b> Examples (see Tab 6): a synthetic-identity
      factory with <b>{int(ex0['cluster_size']) if ex0 is not None else 0} members at {int(ex0['n_distinct_address']) if ex0 is not None else 0} address(es) using {int(ex0['n_distinct_ssn']) if ex0 is not None else 0} different SSNs</b>;
      a fully-cancelled ring of {int(ex1['cluster_size']) if ex1 is not None else 0} members ({fmt_amt(ex1['loss_amt']) if ex1 is not None else '$0'} loss) Chime caught individually but never linked;
      bot-generated email batches with sequential numeric suffixes.</p>

      <p style="margin:0"><b>5. Geographic concentration tells the rest.</b>
      {f"<b>{top_hc_state_row['top_state']}</b> dominates high-conviction rings ({fmt_int(top_hc_state_row['n'])} clusters with ≥3 signals)." if top_hc_state_row is not None else ''}
      P2P-funded clusters with no payroll concentrate in
      {f"<b>{p2p_only_state_row['top_state']}</b> ({fmt_amt(p2p_only_state_row['loss'])} loss)" if p2p_only_state_row is not None else 'specific regions'}.
      These aren't random members — they're locally-coordinated rings worth regional review.</p>
    </div>
  </div>

  <div class="card">
    <div class="section-title">The pipeline that produced these clusters</div>
    <div class="pipeline">
      <div class="step"><div class="step-n">1</div><div class="step-t">Seed Population</div><div class="step-d">{fmt_int(total_members)} disputers Oct–Mar, dispute $ ≥ $500</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="step-n">2</div><div class="step-t">Pull 5 PII Attributes</div><div class="step-d">email, phone, SSN digest, address, device</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="step-n">3</div><div class="step-t">Normalize &amp; De-Noise</div><div class="step-d">strip 555 phones, 'NA' devices, junk addresses</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="step-n">4</div><div class="step-t">Union-Find</div><div class="step-d">connect members sharing any attribute → clusters</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="step-n">5</div><div class="step-t">Characterize</div><div class="step-d">8 risk signals, ranked + scored</div></div>
    </div>
  </div>

  <div class="stat-row">
    <div class="stat blue"><div class="stat-label">Total Disputers</div><div class="stat-value blue">{fmt_int(total_members)}</div><div class="stat-sub">Oct 2025 – Mar 2026, dispute $ ≥ $500</div></div>
    <div class="stat"><div class="stat-label">In Clusters (≥2 mem)</div><div class="stat-value">{fmt_int(total_in_clusters)}</div><div class="stat-sub">{total_in_clusters/total_members*100:.1f}% of population</div></div>
    <div class="stat amber"><div class="stat-label">Actionable Clusters</div><div class="stat-value amber">{fmt_int(actionable_count)}</div><div class="stat-sub">6 to {GIANT_THRESHOLD-1} members</div></div>
    <div class="stat red"><div class="stat-label">High-Conviction Rings</div><div class="stat-value red">{fmt_int(composite_3plus)}</div><div class="stat-sub">≥3 of 8 risk signals fire</div></div>
    <div class="stat red"><div class="stat-label">Total Cluster Loss</div><div class="stat-value red">{fmt_amt_short(total_cluster_loss)}</div><div class="stat-sub">FC + NB in clustered members</div></div>
    <div class="stat"><div class="stat-label">Total Cluster Dispute $</div><div class="stat-value">{fmt_amt_short(total_cluster_disp)}</div><div class="stat-sub">{total_cluster_loss/max(total_cluster_disp,1)*100:.1f}% loss rate</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Cluster size distribution</div>
      <div class="axis-hint">X-axis: # members in a cluster · Y (left): how many such clusters · Y (right): how many members total in that bucket</div>
      <div class="chart-wrap"><canvas id="c_size"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Size bucket → what it means + loss totals</div>
      <table>
        <tr><th>Size</th><th>Meaning</th><th>Clusters</th><th>Members</th><th>Total Loss</th></tr>
        {row_size_dist(size_dist)}
      </table>
    </div>
  </div>
</div>

<!-- TAB 2: HIGH-CONVICTION + SAVINGS -->
<div class="content" id="tab1">
  <div class="story">
    <h2>The savings opportunity</h2>
    <p>Each cluster is scored against <b>8 risk signals</b> (full thresholds in Tab 8). If a cluster appears in the top 50 of a signal's
    ranking, it "hits" that signal. We sum hits to get a <b>composite risk score</b> from 0 to 8.</p>
    <p>The chart below shows: <b>if we acted on clusters at each signal threshold</b> — denying disputes, closing accounts, or
    flagging for review — how much loss would we have prevented in this 6-month window? Lower thresholds = more savings + more
    false positives. Higher thresholds = surgical strikes on confirmed rings.</p>
    <div class="takeaway"><b>Headline:</b> at <b>≥3 signals</b> we address {fmt_amt(sav3)} across {n3} clusters.
    Moving to <b>≥5 signals</b> still captures {fmt_amt(sav5)} in only {n5} clusters — that's the precision sweet spot.
    All 8 firing = lay-up rings at {fmt_amt(sav8)}.</div>
  </div>

  <div class="stat-row">
    <div class="stat red"><div class="stat-label">If apply ≥3 signals</div><div class="stat-value red">{fmt_amt_short(sav3)}</div><div class="stat-sub">addressable · {n3} clusters · {fmt_int(savings_df[savings_df['threshold']==3]['n_members'].iloc[0])} members</div></div>
    <div class="stat amber"><div class="stat-label">If apply ≥4</div><div class="stat-value amber">{fmt_amt_short(sav4)}</div><div class="stat-sub">{n4} clusters</div></div>
    <div class="stat amber"><div class="stat-label">If apply ≥5 (surgical)</div><div class="stat-value amber">{fmt_amt_short(sav5)}</div><div class="stat-sub">{n5} clusters</div></div>
    <div class="stat green"><div class="stat-label">If apply ≥6</div><div class="stat-value green">{fmt_amt_short(sav6)}</div><div class="stat-sub">{int(savings_df[savings_df['threshold']==6]['n_clusters'].iloc[0])} clusters</div></div>
    <div class="stat green"><div class="stat-label">All 8 fire</div><div class="stat-value green">{fmt_amt_short(sav8)}</div><div class="stat-sub">{int(savings_df[savings_df['threshold']==8]['n_clusters'].iloc[0])} lay-up rings</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Addressable loss by signal-hit threshold</div>
      <div class="axis-hint">Each bar = "if we acted on clusters with at least N signals firing, how much loss could we prevent". Red = broad, amber = balanced, green = surgical.</div>
      <div class="chart-wrap"><canvas id="c_savings"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">How many clusters fire at each signal count</div>
      <div class="axis-hint">Distribution of clusters by signals_hit. Bigger right tail = more high-conviction rings.</div>
      <div class="chart-wrap"><canvas id="c_signal_dist"></canvas></div>
    </div>
  </div>

  <div class="story">
    <h2>What if we only used ONE signal?</h2>
    <p>Composite scoring is powerful but complex to operationalize. As a baseline, what if we just used a <b>single signal</b>
    to flag clusters? The chart below shows addressable loss in the top 50 clusters of each individual signal.
    The strongest single signal is <b>{strongest_single['signal']}</b> at {fmt_amt(strongest_single['addressable_loss'])} —
    that's the single dimension that catches the most loss if used in isolation.</p>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Addressable loss from each individual signal</div>
      <div class="axis-hint">Top 50 clusters by each signal alone. Helps pick the strongest single rule.</div>
      <div class="chart-wrap tall"><canvas id="c_single_signal"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Loss savings vs cluster count — precision tradeoff</div>
      <div class="axis-hint">Bar = addressable loss ($K). Line = # clusters affected. As threshold rises, savings shrink slowly but cluster count drops sharply — finds the sweet spot.</div>
      <div class="chart-wrap tall"><canvas id="c_savings_combo"></canvas></div>
    </div>
  </div>

  <div class="story">
    <h2>Loss by linkage attribute</h2>
    <p>Beyond the signal-hit composite, we can ask: <b>which attribute drives the most cluster loss?</b> Below, each card shows
    total loss in clusters formed by that single attribute alone (≥6 members). This tells us where to focus prevention controls —
    address-bound rings vs device-bound rings vs email-bound rings each have different policy responses.</p>
  </div>

  <div class="attr-grid">
    {attr_card_html}
  </div>

  <div class="callout blue">
    <div class="head">🔄 Insight from this run</div>
    <p>The strongest single signal this run is <b>{strongest_single['signal']}</b> at
    <b>{fmt_amt(strongest_single['addressable_loss'])}</b> across {fmt_int(strongest_single['n_clusters'])} clusters —
    roughly matching the ≥3-signal composite ({fmt_amt(sav3)}). A simple top-50 policy on this single signal would
    capture the vast majority of the value without composite scoring complexity.</p>
    <p style="margin-top:6px">At the ≥5-signal surgical threshold, we keep <b>{fmt_amt(sav5)}</b> ({sav5/sav3*100:.0f}% of the broader ≥3 capture)
    while reducing cluster count to {n5} — that's the precision sweet spot for a Phase-1 rollout.</p>
  </div>
  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 3: LOSS CONCENTRATION -->
<div class="content" id="tab2">
  <div class="story">
    <h2>How concentrated is the loss?</h2>
    <p>If cluster loss spreads evenly across thousands of small clusters, the policy lift is limited. But if loss
    <b>concentrates in a small number of clusters</b>, policy actions become high-value. The Pareto chart below walks from
    the single worst cluster to the least bad, showing what % of total cluster loss they cumulatively cover.</p>
    <div class="takeaway"><b>What we see:</b> top <b>10 clusters</b> hold {fmt_pct(top10_loss_share)} of all cluster loss.
    Top <b>25</b> hold {fmt_pct(top25_loss_share)}. Highly concentrated — a small number of rings are doing outsized damage.</div>
  </div>


  <div class="stat-row">
    <div class="stat red"><div class="stat-label">Top 10 share of loss</div><div class="stat-value red">{fmt_pct(top10_loss_share)}</div></div>
    <div class="stat amber"><div class="stat-label">Top 25 share of loss</div><div class="stat-value amber">{fmt_pct(top25_loss_share)}</div></div>
    <div class="stat"><div class="stat-label">Largest single-cluster loss</div><div class="stat-value">{fmt_amt_short(loss_sorted.iloc[0]['loss_amt'])}</div><div class="stat-sub">cluster {fmt_int(loss_sorted.iloc[0]['cluster_1'])}, {fmt_int(loss_sorted.iloc[0]['cluster_size'])} members</div></div>
    <div class="stat"><div class="stat-label">Median loss (≥6 members)</div><div class="stat-value">{fmt_amt_short(actionable['loss_amt'].median())}</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Pareto — cumulative loss by cluster rank (top 100)</div>
      <div class="axis-hint">X-axis: cluster rank, where 1 = highest-loss cluster, 100 = the 100th highest. Walking right = adding successively smaller losers. Y = % of total cluster loss covered.</div>
      <div class="chart-wrap tall"><canvas id="c_pareto"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Top 10 clusters by loss ($K)</div>
      <div class="axis-hint">The 10 worst clusters concentrate {fmt_pct(top10_loss_share)} of total loss.</div>
      <div class="chart-wrap tall"><canvas id="c_top10_bar"></canvas></div>
    </div>
  </div>
  <div class="card">
    <div class="section-title">Top 10 clusters — Final Credit vs Negative Balance composition ($K)</div>
    <div class="axis-hint">FC = Chime issued provisional credit that was finalized. NB = balance left negative after PVC reversal. Different remediation paths — FC clusters need dispute denial; NB clusters need account closure.</div>
    <div class="chart-wrap"><canvas id="c_fc_nb_stack"></canvas></div>
  </div>

  {f'''<div class="callout blue">
    <div class="head">🔄 Insight from this run</div>
    <p>The single largest-loss cluster is <b>#{int(top1_loss_row['cluster_1'])}</b> with <b>{int(top1_loss_row['cluster_size'])} members</b> —
    {fmt_amt(top1_loss_row['loss_amt'])} loss on {fmt_amt(top1_loss_row['total_dispute_amt'])} disputed
    ({fmt_pct(top1_loss_row['loss_rate'])} loss rate). Top state: {top1_loss_row['top_state'] if isinstance(top1_loss_row['top_state'], str) else '—'}.
    A single ring this size is the kind of finding that justifies the whole linkage exercise.</p>
  </div>''' if top1_loss_row is not None else ''}
  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 4: BEHAVIORAL PATTERNS -->
<div class="content" id="tab3">
  <div class="story">
    <h2>Behavioral red flags — declines and authentication</h2>
    <p>Three behavior-based fraud signatures sit inside a cluster's transaction history:</p>
    <p><b>1. High decline rate</b> — fraud rings stress-test stolen cards or burn through balances. <b>≥50% decline rate
    over 100+ auths</b> is far outside normal member behavior.<br/>
    <b>2. Card-testing pattern</b> — declines specifically with codes <code>14</code>/<code>54</code>/<code>82</code>/<code>N7</code>
    (invalid card, expired, bad CVV) signal <b>card enumeration</b> — bots trying which stolen cards still work.<br/>
    <b>3. Authentication bypass</b> — when ScanID is rarely required for risky users, the cluster is <b>evading ATOM scoring</b>
    (clean device profiles, trusted IPs).</p>
    <div class="takeaway"><b>The 4 scatters below</b> plot cluster loss vs each behavioral signal. Clusters in the top-right of
    any chart (high signal + high loss) are the rings actively executing fraud.</div>
  </div>

  <div class="stat-row">
    <div class="stat red"><div class="stat-label">Clusters &gt;60% decline rate</div><div class="stat-value red">{fmt_int((actionable[actionable['total_auth_attempts']>100]['decline_rate']>0.6).sum())}</div><div class="stat-sub">&gt;100 auth attempts</div></div>
    <div class="stat amber"><div class="stat-label">Clusters with card-test pattern</div><div class="stat-value amber">{fmt_int((actionable['cardtest_decline_count']>5).sum())}</div><div class="stat-sub">≥5 RC 14/54/82/N7</div></div>
    <div class="stat"><div class="stat-label">Total fraud-flagged declines</div><div class="stat-value">{fmt_int(actionable['fraud_decline_count'].sum())}</div><div class="stat-sub">response code 59</div></div>
    <div class="stat"><div class="stat-label">Total ScanID challenges</div><div class="stat-value">{fmt_int(actionable['scanid_required_count'].sum())}</div><div class="stat-sub">last 6 months</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Decline rate vs cluster loss</div>
      <div class="axis-hint">Each dot = one cluster (filtered &gt;100 auths). Top-right = high decline + high loss = active fraud.</div>
      <div class="chart-wrap"><canvas id="c_scatter_decline"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Fraud-flagged decline rate vs loss</div>
      <div class="axis-hint">X = % of auths that processor flagged as suspected fraud (RC 59). High = card known compromised.</div>
      <div class="chart-wrap"><canvas id="c_scatter_fraud"></canvas></div>
    </div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">ScanID required rate vs loss</div>
      <div class="axis-hint">X = % of logins that triggered ScanID step-up. Low rate + high loss = ATOM bypass (clean device fingerprints used by fraudsters).</div>
      <div class="chart-wrap"><canvas id="c_scatter_scanid"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Password-fail rate vs loss</div>
      <div class="axis-hint">X = pw_fail / login_attempts. High pw_fail + high loss = ATO attempts via credential stuffing.</div>
      <div class="chart-wrap"><canvas id="c_scatter_pwfail"></canvas></div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Top 10 decline-rate clusters — what kinds of declines are they?</div>
    <div class="axis-hint">For each top decline-rate cluster, the stack shows mix of decline reasons. Heavy fraud/card-test = active enumeration. Heavy NSF = balance stress-testing. Heavy frozen = recidivist on blocked cards.</div>
    <div class="chart-wrap tall"><canvas id="c_decline_stack"></canvas></div>
  </div>

  <div class="card">
    <div class="section-title">Decline code legend</div>
    <table class="signal-table">
      <tr><th>Code(s)</th><th>Meaning</th><th>Signal</th></tr>
      <tr><td><code>00</code>, <code>10</code></td><td>Approved</td><td>baseline</td></tr>
      <tr><td><code>51</code></td><td>Insufficient funds (NSF)</td><td>Stress-testing balances</td></tr>
      <tr><td><code>59</code></td><td>Suspected fraud</td><td>Direct fraud signal</td></tr>
      <tr><td><code>14</code>, <code>54</code>, <code>82</code>, <code>N7</code></td><td>Invalid card / expired / bad CVV</td><td>Card enumeration</td></tr>
      <tr><td><code>78</code>, <code>LK</code>, <code>5C</code>, <code>9G</code></td><td>Frozen / blocked</td><td>Pre-detected fraud</td></tr>
      <tr><td><code>46</code></td><td>Closed account</td><td>Mule on dead account</td></tr>
    </table>
  </div>

  {f'''<div class="callout blue">
    <div class="head">🔄 Insight from this run</div>
    <p>The worst card-testing cluster this run is <b>#{int(cardtest_top['cluster_1'])}</b> ({int(cardtest_top['cluster_size'])} members) with
    <b>{fmt_int(cardtest_top['cardtest_decline_count'])} card-test declines</b> (RC 14/54/82/N7) and {fmt_int(cardtest_top['fraud_decline_count'])} fraud-flagged declines.
    Loss: {fmt_amt(cardtest_top['loss_amt'])}, dispute volume: {fmt_amt(cardtest_top['total_dispute_amt'])}. This is consistent with active card enumeration.</p>
    {f"<p style='margin-top:6px'>Separately, the cluster with the most fraud-flagged declines (RC 59) is <b>#{int(fraud_decline_top['cluster_1'])}</b> with {fmt_int(fraud_decline_top['fraud_decline_count'])} fraud-flagged declines across {fmt_int(fraud_decline_top['total_auth_attempts'])} attempts — likely card known compromised at processor level.</p>" if fraud_decline_top is not None else ''}
  </div>''' if cardtest_top is not None else ''}
  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 5: FUNDING & P2P -->
<div class="content" id="tab4">
  <div class="story">
    <h2>Where does the money come from in fraud clusters?</h2>
    <p>Legitimate members are funded by <b>direct deposit (payroll)</b>. Fraud clusters often aren't — they're funded by
    <b>P2P transfers from other accounts</b> (mule chains), <b>cash/check deposits</b>, or sudden one-off inflows.
    A cluster where 60%+ of inflow is P2P with zero DD is a strong indicator of money laundering or recruited-account fraud.</p>
    <p>Even more telling: clusters where <b>dispute $ exceeds total inflow</b>. That means members are filing disputes for
    transactions that exceed what was ever deposited into their accounts — a structurally impossible pattern for honest members.</p>
    <div class="takeaway"><b>What we see across actionable clusters:</b>
    {fmt_pct(p2p_share)} of total inflow is P2P/transfers, only {fmt_pct(dd_share)} is DD.
    <b>{fmt_int(p2p_only_clusters)} clusters</b> have &gt;$5K P2P inflow AND &lt;$100 DD over 6 months — they account for
    <b>{fmt_amt(p2p_only_loss)}</b> in losses. These are not working members.</div>
  </div>


  <div class="stat-row">
    <div class="stat red"><div class="stat-label">P2P-only clusters</div><div class="stat-value red">{fmt_int(p2p_only_clusters)}</div><div class="stat-sub">&gt;$5K P2P AND &lt;$100 DD</div></div>
    <div class="stat red"><div class="stat-label">P2P-only cluster loss</div><div class="stat-value red">{fmt_amt_short(p2p_only_loss)}</div><div class="stat-sub">addressable via DD policy</div></div>
    <div class="stat amber"><div class="stat-label">% inflow from P2P</div><div class="stat-value amber">{fmt_pct(p2p_share)}</div><div class="stat-sub">across all actionable clusters</div></div>
    <div class="stat"><div class="stat-label">% inflow from DD</div><div class="stat-value">{fmt_pct(dd_share)}</div><div class="stat-sub">payroll</div></div>
    <div class="stat red"><div class="stat-label">Median dispute-to-inflow</div><div class="stat-value red">{fmt_pct(disp_inflow_med)}</div><div class="stat-sub">100%+ = disputing more than received</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Funding source mix ($M, all actionable clusters)</div>
      <div class="axis-hint">DD = legitimate payroll. P2P share above DD share is a strong fraud signal.</div>
      <div class="chart-wrap"><canvas id="c_funding_pie"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Dispute-to-inflow ratio distribution</div>
      <div class="axis-hint">% of cluster's total inflow that ends up disputed. Above 100% = mechanically suspicious.</div>
      <div class="chart-wrap"><canvas id="c_inflow_hist"></canvas></div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">P2P share vs DD share (each bubble = cluster, bubble size = loss)</div>
    <div class="axis-hint">Top-left (high P2P, low DD) = mule-funded clusters. Bottom-right (low P2P, high DD) = healthy members. Bubble size grows with cluster loss.</div>
    <div class="chart-wrap tall"><canvas id="c_p2p_dd_scatter"></canvas></div>
  </div>

  <div class="card">
    <div class="section-title">Top 10 P2P-heavy clusters — funding source breakdown ($K)</div>
    <div class="axis-hint">For each high-P2P cluster, the stack shows where money actually came from. Heavy P2P bars with thin DD = mule chain. The "DD" segment should be nearly absent in fraud clusters.</div>
    <div class="chart-wrap tall"><canvas id="c_funding_stack"></canvas></div>
  </div>

  <div class="two-col">
    <div class="callout red">
      <div class="head">Pattern A — P2P-funded ring</div>
      <p>A cluster member receives $500 via P2P from another account, transacts $480 on a stolen card, then disputes it. Money flows out as withdrawal/P2P send. <b>P2P-in % &gt; DD %</b> is the fingerprint.</p>
    </div>
    <div class="callout red">
      <div class="head">Pattern B — Dispute &gt; Inflow</div>
      <p>Member disputes $3,200 but only $1,200 was ever deposited over 6 months. Mechanically impossible for honest member — the dispute is on money they never legitimately had.</p>
    </div>
  </div>
  <div class="callout green">
    <div class="head">Pattern C — Healthy member (control)</div>
    <p>Regular bi-weekly DD, modest P2P, dispute-to-inflow &lt; 5%. The cluster is most likely benign — these often appear as 2-member family clusters.</p>
  </div>

  {f'''<div class="callout blue">
    <div class="head">🔄 Insight from this run</div>
    <p>P2P-only clusters this run concentrate in <b>{p2p_only_state_row['top_state']}</b> with
    <b>{fmt_int(p2p_only_state_row['n'])} clusters</b> and <b>{fmt_amt(p2p_only_state_row['loss'])}</b> in losses.
    These are members receiving thousands via P2P with essentially zero payroll — a mule-chain fingerprint worth a regional review.</p>
  </div>''' if p2p_only_state_row is not None else ''}
  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 6: SMOKING-GUN RINGS -->
<div class="content" id="tab5">
  <div class="story">
    <h2>What makes these clusters "smoking guns"?</h2>
    <p>Three patterns almost never occur in legitimate clusters:</p>
    <p><b>1. Address-bound rings.</b> 10+ members at the SAME physical address but with DIFFERENT SSNs/phones/emails = synthetic-identity factory.
    One operator creating multiple fictitious identities at one mailing address. Real households share LAST names; these rings don't.<br/>
    <b>2. 100%-cancelled clusters.</b> Chime has already individually flagged every member for fraud — but never <i>linked them</i>.
    Cluster discovery makes the coordinated nature visible after the fact and trains the next policy.<br/>
    <b>3. Email anomalies.</b> Sequential numeric suffixes, 100% gmail dominance, high digit-density local parts — these
    are bot-generated identity batches.</p>
  </div>

  <div class="ex-grid">
    {f'''<div class="ex-card">
      <div class="ex-head">⚠️ Synthetic-Identity Factory</div>
      <div class="ex-cluster">Cluster #{int(ex0['cluster_1'])}</div>
      <div class="ex-detail"><b>{int(ex0['cluster_size'])} different people</b> at <b>{int(ex0['n_distinct_address'])} address(es)</b> with <b>{int(ex0['n_distinct_ssn'])} different SSNs</b>. {int(ex0['n_distinct_phones'])} phones, {int(ex0['n_distinct_emails'])} emails — all unique. One operator, multiple fake identities.</div>
      <div class="ex-loss">Loss: {fmt_amt(ex0['loss_amt'])} · Dispute $: {fmt_amt(ex0['total_dispute_amt'])}</div>
    </div>''' if ex0 is not None else ''}
    {f'''<div class="ex-card">
      <div class="ex-head">⚠️ Cancelled-but-never-linked</div>
      <div class="ex-cluster">Cluster #{int(ex1['cluster_1'])}</div>
      <div class="ex-detail"><b>{int(ex1['n_cancelled'])} of {int(ex1['cluster_size'])} members</b> already cancelled by Chime as individuals — but never linked. Avg Peak FPF v2: {ex1['cluster_avg_peak_fpf_v2']:.2f}. Top state: {ex1['top_state'] if isinstance(ex1['top_state'], str) else '—'}.</div>
      <div class="ex-loss">Loss: {fmt_amt(ex1['loss_amt'])} · Dispute $: {fmt_amt(ex1['total_dispute_amt'])}</div>
    </div>''' if ex1 is not None else ''}
    {f'''<div class="ex-card">
      <div class="ex-head">⚠️ Bot-Generated Email Batch</div>
      <div class="ex-cluster">Cluster #{int(ex2['cluster_1'])}</div>
      <div class="ex-detail">Top domain share <b>{ex2['top_domain_share']*100:.0f}%</b>{' with <b>sequential numeric suffixes</b>' if ex2['sequential_suffix_flag'] else ''}. {int(ex2['cluster_size'])} members on the same email pattern — automated identity generation signature.</div>
      <div class="ex-loss">Loss: {fmt_amt(ex2['loss_amt'])} · Dispute $: {fmt_amt(ex2['total_dispute_amt'])}</div>
    </div>''' if ex2 is not None else ''}
  </div>

  <div class="stat-row">
    <div class="stat red"><div class="stat-label">Clusters with ≥3× members/addresses</div><div class="stat-value red">{fmt_int((actionable[actionable['n_distinct_address']>0]['addr_density']>=3).sum())}</div><div class="stat-sub">synthetic-ID geometry</div></div>
    <div class="stat amber"><div class="stat-label">100% cancelled clusters</div><div class="stat-value amber">{fmt_int((actionable['cancelled_pct']>=1.0).sum())}</div><div class="stat-sub">caught individually, never linked</div></div>
    <div class="stat amber"><div class="stat-label">Sequential email suffix clusters</div><div class="stat-value amber">{fmt_int((actionable['sequential_suffix_flag']==True).sum())}</div><div class="stat-sub">bot-generated identities</div></div>
    <div class="stat"><div class="stat-label">&gt;80% single-domain clusters</div><div class="stat-value">{fmt_int((actionable['top_domain_share']>=0.8).sum())}</div><div class="stat-sub">heavy gmail/outlook concentration</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">SSN diversity vs Address diversity (each bubble = a cluster)</div>
      <div class="axis-hint">Healthy: SSN count ≈ Address count (everyone unique). Bottom-right: many SSNs, few addresses = synthetic factory. Bubble size grows with cluster size.</div>
      <div class="chart-wrap tall"><canvas id="c_ssn_addr"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Address-density distribution</div>
      <div class="axis-hint">members ÷ distinct addresses per cluster. 1.0 = each member unique. ≥3 = ring signal.</div>
      <div class="chart-wrap tall"><canvas id="c_density"></canvas></div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Anatomy of the 3 example clusters — attribute diversity profile</div>
    <div class="axis-hint">Radar shows attributes-per-member ratio. A healthy cluster sits at 1.0 on every axis. Synthetic factories collapse on Address (lots of members per address). Email-batch rings collapse on Email if domain-concentrated.</div>
    <div class="chart-wrap tall"><canvas id="c_radar"></canvas></div>
  </div>

  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 7: GEOGRAPHIC -->
<div class="content" id="tab6">
  <div class="story">
    <h2>Why does geography matter for fraud detection?</h2>
    <p>Legitimate clusters span many states — the only reason for cross-state linkage is shared PII (family, address moves,
    device sharing). When a cluster is <b>100% from one state</b> — particularly Michigan, Georgia, Alabama, or Florida
    (historically high-fraud regions) — it's strong evidence of a <b>locally-coordinated ring</b>: in-person recruiting,
    shared physical infrastructure, common middleman.</p>
    <div class="takeaway"><b>What stands out:</b> Michigan and Georgia repeatedly appear as the top states for high-conviction
    rings. Florida and Texas have higher cluster volume but more diffuse. The chart below shows where high-conviction rings
    cluster geographically.</div>
  </div>


  <div class="stat-row">
    <div class="stat red"><div class="stat-label">100% single-state clusters</div><div class="stat-value red">{fmt_int((actionable['top_state_share']>=1.0).sum())}</div></div>
    <div class="stat"><div class="stat-label">Avg # states per cluster</div><div class="stat-value">{actionable['n_distinct_states'].mean():.1f}</div></div>
    <div class="stat amber"><div class="stat-label">Top state by cluster $</div><div class="stat-value amber">{state_summary.iloc[0]['top_state'] if len(state_summary) else '—'}</div><div class="stat-sub">{fmt_amt_short(state_summary.iloc[0]['total_dispute']) if len(state_summary) else ''}</div></div>
    <div class="stat red"><div class="stat-label">Top state by high-conv rings</div><div class="stat-value red">{top_state_hc.iloc[0]['top_state'] if len(top_state_hc) else '—'}</div><div class="stat-sub">{fmt_int(top_state_hc.iloc[0]['n']) if len(top_state_hc) else ''} clusters with ≥3 signals</div></div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="section-title">Top 10 states — cluster dispute $ vs loss $ ($K)</div>
      <div class="axis-hint">Side-by-side bars compare disputed volume vs losses Chime actually took.</div>
      <div class="chart-wrap tall"><canvas id="c_state"></canvas></div>
    </div>
    <div class="card">
      <div class="section-title">Top states by # of high-conviction rings</div>
      <div class="axis-hint">Where the ≥3-signal-hitting rings concentrate geographically.</div>
      <div class="chart-wrap tall"><canvas id="c_state_hc"></canvas></div>
    </div>
  </div>

  {f'''<div class="callout blue">
    <div class="head">🔄 Insight from this run</div>
    <p>This run, <b>{top_hc_state_row['top_state']}</b> dominates with <b>{fmt_int(top_hc_state_row['n'])} high-conviction rings</b>
    (≥3 of 8 signals firing). Across all clusters, the top-loss state is <b>{state_summary.iloc[0]['top_state'] if len(state_summary) else '—'}</b>
    at {fmt_amt(state_summary.iloc[0]['total_loss']) if len(state_summary) else '$0'} in cluster loss.
    Concentrated activity in these states warrants a regional investigator review — they're not random member geography.</p>
  </div>''' if top_hc_state_row is not None else ''}
  <div style="text-align:center;font-size:11.5px;color:#9ca3af;margin-top:12px">Full data tables for this tab → Tab 8</div>
</div>

<!-- TAB 8: GLOSSARY & METHODOLOGY -->
<div class="content" id="tab7">
  <div class="story">
    <h2>Glossary, Methodology &amp; Data</h2>
    <p>The reference companion to the narrative tabs. Includes the network diagram showing how clusters form,
    all 8 signal thresholds, EDA findings, plain-English definitions of every term, data sources, and the
    full data tables from each visual tab (Tabs 2–7) consolidated below.</p>
  </div>

  <div class="card">
    <div class="section-title">How clusters form — a 7-member chain example</div>
    <div class="network-wrap">
      <svg width="100%" height="520" viewBox="0 0 1000 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
        <defs>
          <!-- Arrow markers per color -->
          <marker id="aEmail" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#d97706"/>
          </marker>
          <marker id="aAddr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#7c3aed"/>
          </marker>
          <marker id="aPhone" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#059669"/>
          </marker>
          <marker id="aDev" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#2563eb"/>
          </marker>
          <marker id="aSSN" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#dc2626"/>
          </marker>
        </defs>

        <!-- Title -->
        <text x="500" y="32" text-anchor="middle" font-size="15" fill="#1e3a8a" font-weight="700">Members A, B, C, D, E, F, G — all in the same cluster_1 via transitive PII linkage</text>

        <!-- Vertical chain B → E → F (upward) -->
        <!-- E to F (top, via SSN) -->
        <line x1="430" y1="170" x2="430" y2="100" stroke="#dc2626" stroke-width="2.5" marker-end="url(#aSSN)"/>
        <text x="445" y="142" font-size="13" fill="#991b1b" font-weight="600">SSN</text>
        <!-- B to E (middle, via device) -->
        <line x1="430" y1="280" x2="430" y2="210" stroke="#2563eb" stroke-width="2.5" marker-end="url(#aDev)"/>
        <text x="445" y="252" font-size="13" fill="#1e40af" font-weight="600">device</text>

        <!-- Horizontal chain A → B → C → G (rightward, all at y=310) -->
        <!-- A to B (via email) -->
        <line x1="200" y1="310" x2="395" y2="310" stroke="#d97706" stroke-width="2.5" marker-end="url(#aEmail)"/>
        <text x="297" y="298" text-anchor="middle" font-size="13" fill="#92400e" font-weight="600">email</text>
        <!-- B to C (via address) -->
        <line x1="465" y1="310" x2="660" y2="310" stroke="#7c3aed" stroke-width="2.5" marker-end="url(#aAddr)"/>
        <text x="562" y="298" text-anchor="middle" font-size="13" fill="#5b21b6" font-weight="600">address</text>
        <!-- C to G (via email) -->
        <line x1="730" y1="310" x2="900" y2="310" stroke="#d97706" stroke-width="2.5" marker-end="url(#aEmail)"/>
        <text x="815" y="298" text-anchor="middle" font-size="13" fill="#92400e" font-weight="600">email</text>

        <!-- A to D (downward, via phone) -->
        <line x1="165" y1="345" x2="165" y2="430" stroke="#059669" stroke-width="2.5" marker-end="url(#aPhone)"/>
        <text x="180" y="392" font-size="13" fill="#065f46" font-weight="600">phone</text>

        <!-- Nodes (drawn on top of lines) -->
        <!-- Top: F -->
        <circle cx="430" cy="80"  r="28" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="430" y="88" text-anchor="middle" fill="white" font-size="20" font-weight="700">F</text>
        <!-- Middle: E -->
        <circle cx="430" cy="190" r="28" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="430" y="198" text-anchor="middle" fill="white" font-size="20" font-weight="700">E</text>
        <!-- Horizontal row -->
        <circle cx="165" cy="310" r="35" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="165" y="319" text-anchor="middle" fill="white" font-size="22" font-weight="700">A</text>
        <circle cx="430" cy="310" r="35" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="430" y="319" text-anchor="middle" fill="white" font-size="22" font-weight="700">B</text>
        <circle cx="695" cy="310" r="35" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="695" y="319" text-anchor="middle" fill="white" font-size="22" font-weight="700">C</text>
        <circle cx="930" cy="310" r="28" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="930" y="318" text-anchor="middle" fill="white" font-size="20" font-weight="700">G</text>
        <!-- Bottom: D -->
        <circle cx="165" cy="445" r="28" fill="#3b82f6" stroke="white" stroke-width="3"/>
        <text x="165" y="453" text-anchor="middle" fill="white" font-size="20" font-weight="700">D</text>

        <!-- Legend (bottom) -->
        <g transform="translate(40, 490)">
          <line x1="0"   y1="0" x2="18" y2="0" stroke="#d97706" stroke-width="2.5"/>
          <text x="24" y="4" font-size="11.5" fill="#475569">email link</text>
          <line x1="115" y1="0" x2="133" y2="0" stroke="#7c3aed" stroke-width="2.5"/>
          <text x="139" y="4" font-size="11.5" fill="#475569">address link</text>
          <line x1="240" y1="0" x2="258" y2="0" stroke="#059669" stroke-width="2.5"/>
          <text x="264" y="4" font-size="11.5" fill="#475569">phone link</text>
          <line x1="358" y1="0" x2="376" y2="0" stroke="#2563eb" stroke-width="2.5"/>
          <text x="382" y="4" font-size="11.5" fill="#475569">device link</text>
          <line x1="478" y1="0" x2="496" y2="0" stroke="#dc2626" stroke-width="2.5"/>
          <text x="502" y="4" font-size="11.5" fill="#475569">SSN link</text>
        </g>
      </svg>
      <div class="network-caption">
        Each line = one PII attribute shared between two members. A→B share an email, B→C share an address, A→D share a phone, B→E share a device, E→F share an SSN, C→G share an email.<br/>
        <b>None of these pairs share with each other directly — but Union-Find walks the transitive closure:</b> any member reachable through any chain of shared attributes ends up in the same connected component. So A, B, C, D, E, F, G all land in <b>one cluster</b>.
      </div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">The 8 Risk Signals — exact thresholds</div>
    <div style="font-size:12.5px;color:#475569;margin-bottom:12px">
      Composite risk = how many of these 8 signal "top-50 lists" a cluster appears in.
    </div>
    <table class="signal-table">
      <tr><th>Signal</th><th>How it's defined</th></tr>
      {signal_rows_html}
    </table>
  </div>

  <div class="card">
    <div class="section-title">EDA findings &amp; cleaning rules applied to the raw PII</div>
    <div class="eda-row"><div class="eda-label">Phone normalization</div>
      <div class="eda-val">Strip non-digits, keep only 10-digit numbers. Drop all phones starting with <code>555</code> — these are reserved/fictional numbers used internally as masked placeholders (~7% of seed phones). Also drop all-same-digit patterns and sequential like <code>1234567890</code>.</div></div>
    <div class="eda-row"><div class="eda-label">Email normalization</div>
      <div class="eda-val">Lowercase, trim, strip <code>+alias</code> from local part. For gmail/googlemail: <b>strip all dots in local part</b> (gmail ignores them — <code>john.smith@gmail.com</code> = <code>johnsmith@gmail.com</code>). Validate basic format (must have @ and . in domain).</div></div>
    <div class="eda-row"><div class="eda-label">Address normalization</div>
      <div class="eda-val">Concatenate <code>address_line_1 + address_line_2</code>, uppercase, strip all non-alphanumeric except spaces, collapse multiple spaces. Drop if length &lt; 5 chars or matches junk list (NA, UNKNOWN, NONE, etc.).</div></div>
    <div class="eda-row"><div class="eda-label">SSN digest</div>
      <div class="eda-val">Used as-is from <code>edw_pii_db.core.dim_user_pii.ssn_digest</code> (already hashed). 99.8% population coverage in the seed.</div></div>
    <div class="eda-row"><div class="eda-label">Device ID</div>
      <div class="eda-val">From <code>analytics.looker.device_sessions</code>. Drop literal <code>'NA'</code> placeholder (~4,400 disputers map to NA). Drop very short IDs (&lt;8 chars). Each user averages ~8 device IDs across years.</div></div>
    <div class="eda-row"><div class="eda-label">Noise findings discovered during EDA</div>
      <div class="eda-val"><b>Email:</b> zero raw sharing — every disputer has a unique email pre-normalization. <b>Phone:</b> only 555-prefix phones were shared. <b>SSN:</b> essentially unique (1 SSN shared by 2 members). <b>Address:</b> top shared = "3117 CAROL AVE" with 46 members (real fraud signal). <b>Device:</b> top non-'NA' device shared by 29 users.</div></div>
    <div class="eda-row"><div class="eda-label">Why we exclude the giant component</div>
      <div class="eda-val">One mega-cluster (5,738 members) forms when long device-ID chains transitively link legitimate members through coincidental shared devices (app reinstalls over years). Loss rate only 14% vs 40–90% in real rings. We exclude clusters ≥1,000 members from actionable views.</div></div>
  </div>

  <div class="card">
    <div class="section-title">Key Terms</div>
    <div class="glossary-grid">
      <div class="gloss-card"><h4>Actionable Cluster</h4><p>A cluster with <b>6 to {GIANT_THRESHOLD-1}</b> members. Big enough to indicate a real ring (≥6), small enough to investigate. Excludes the giant component.</p></div>
      <div class="gloss-card"><h4>Composite Risk Score</h4><p>How many of the 8 risk signals a cluster hits (top-50 in each). <b>≥3 = high conviction</b>, ≥5 = surgical, 8 = lay-up.</p></div>
      <div class="gloss-card"><h4>Signals Hit</h4><p>Count from 0–8. A cluster "hits" a signal if it's in the top 50 clusters ranked by that signal (after the signal's filter is applied).</p></div>
      <div class="gloss-card"><h4>Loss Rate</h4><p>Sum (FC + NB) across all cluster disputes ÷ sum of dispute $. Sum-of-numerator / sum-of-denominator — never an average of per-member rates.</p></div>
      <div class="gloss-card"><h4>FC (Final Credit)</h4><p>Money Chime issued to the member after dispute approval. Direct Chime loss if not recovered via chargeback.</p></div>
      <div class="gloss-card"><h4>NB (Negative Balance)</h4><p>Negative balance left after PVC reversal. Also a direct Chime loss if not recovered.</p></div>
      <div class="gloss-card"><h4>PVC Score</h4><p>Provisional Credit score (0–1) from the dispute pipeline. Predicts how likely a PVC issuance will reverse.</p></div>
      <div class="gloss-card"><h4>FPF v2 Score (cluster level)</h4><p>Member-Level First Party Fraud model score (0–1), computed daily for all transacting members. We first take each member's <b>peak score over the 6-month window</b>, then <b>average those peaks across the cluster</b>. This dampens single-outlier inflation — a cluster of 20 members scoring high vs 1 member scoring high reads very differently with this metric.</p></div>
      <div class="gloss-card"><h4>Avg Peak Score (general)</h4><p>For any model score (FPF v2, FPF dispute, mFPF, PVC): we take each member's peak (max) score, then average those peaks across the cluster. Replaces simple cluster-max, which was sensitive to outliers.</p></div>
      <div class="gloss-card"><h4>mFPF Score</h4><p>Older dispute-level FPF model. We aggregate as max+avg across cluster disputes.</p></div>
      <div class="gloss-card"><h4>ATOM Score</h4><p>Account Takeover Model (0–1) scored at every login. High = ATO suspected. Drives ScanID step-up.</p></div>
      <div class="gloss-card"><h4>ScanID</h4><p>Step-up identity verification (ID + selfie) triggered when ATOM &gt; 0.98. Bypass = clean device profiles used by fraudsters.</p></div>
      <div class="gloss-card"><h4>Req Rate (ScanID Required Rate)</h4><p><code>scanid_required_count / login_attempts</code> for the cluster — what fraction of logins by cluster members triggered a ScanID step-up. Low req rate + high loss = ATOM bypass.</p></div>
      <div class="gloss-card"><h4>Decline Rate</h4><p><code>declined_amt / (approved_amt + declined_amt)</code>. Cluster-level sum-of-sum. Fraud rings often run 50–80% decline rates.</p></div>
      <div class="gloss-card"><h4>Fraud-flagged Decline (RC 59)</h4><p>Auth declined with response code 59 = "suspected fraud" flagged by processor. Direct fraud signal.</p></div>
      <div class="gloss-card"><h4>Card-testing Decline</h4><p>Declines with response codes <code>14</code>/<code>54</code>/<code>82</code>/<code>N7</code> = invalid card / expired / bad CVV. Pattern = bot enumeration of stolen cards.</p></div>
      <div class="gloss-card"><h4>Frozen Decline</h4><p>Declines with codes <code>78</code>/<code>LK</code>/<code>5C</code>/<code>9G</code> = card already blocked. Cluster making lots of these = recidivist fraudster.</p></div>
      <div class="gloss-card"><h4>NSF Decline (RC 51)</h4><p>Insufficient funds. High rate = stress-testing balances.</p></div>
      <div class="gloss-card"><h4>P2P (Pay Friends)</h4><p>Member-to-member transfers within Chime. <b>P2P-in % &gt; DD % = potential mule chain</b>.</p></div>
      <div class="gloss-card"><h4>P2P-only Cluster</h4><p>&gt;$5K P2P inflow AND &lt;$100 DD over the 6-month window. Not real working members.</p></div>
      <div class="gloss-card"><h4>Dispute-to-Inflow Ratio</h4><p>Total dispute $ ÷ total money in (DD + P2P + deposits). Ratios &gt;100% = disputing more than received (impossible for honest members).</p></div>
      <div class="gloss-card"><h4>Late Notification (LN)</h4><p>Disputes filed &gt; 60 days after the transaction. High LN % in a cluster = filed-late ring.</p></div>
      <div class="gloss-card"><h4>UT / Non-Reg / EA</h4><p>Dispute reason buckets. UT = unauthorized transaction (Reg E protected); Non-Reg = goods/services issues; EA = everything else.</p></div>
      <div class="gloss-card"><h4>cluster_1 thru cluster_6</h4><p>cluster_1 = transitive across all 5 attributes. cluster_2 = email-only. cluster_3 = address-only. cluster_4 = SSN-only. cluster_5 = phone-only. cluster_6 = device-only.</p></div>
      <div class="gloss-card"><h4>Giant Component</h4><p>≥{GIANT_THRESHOLD} members. Almost always a device-chain artifact, not a real ring. Excluded from actionable views.</p></div>
      <div class="gloss-card"><h4>Address Density</h4><p>Cluster size ÷ # distinct addresses. 1.0 = each member unique. ≥3 = multiple people per address = ring signal.</p></div>
      <div class="gloss-card"><h4>Sequential Suffix</h4><p>Email anomaly flag: multiple emails in cluster with same prefix + numeric suffixes (<code>user1</code>, <code>user2</code>, <code>user3</code>). Bot-generated identities.</p></div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Data Sources</div>
    <table class="signal-table">
      <tr><th>Table</th><th>What we pull</th></tr>
      <tr><td><code>rest.test.ub_dispute_exception_reporting_base</code></td><td>Dispute count, $, FC, NB, scores (FPF/mFPF/PVC), reason, intake</td></tr>
      <tr><td><code>edw_pii_db.core.dim_user_pii</code></td><td>Email, phone, ssn_digest, address, state — the linkage attributes</td></tr>
      <tr><td><code>edw_db.core.dim_member_v10</code></td><td>User status, account age, program tier</td></tr>
      <tr><td><code>analytics.looker.device_sessions</code></td><td>Device IDs per user</td></tr>
      <tr><td><code>analytics.test.login_requests</code></td><td>Auth events — pw_fail, MFA, ScanID</td></tr>
      <tr><td><code>edw_db.core.fct_realtime_auth_event</code></td><td>Auth attempts + declines (response codes)</td></tr>
      <tr><td><code>edw_db.core.ftr_transaction</code></td><td>Funding, transfers, deposits, P2P, withdrawals</td></tr>
      <tr><td><code>risk.prod.spotme_eligible_direct_deposits</code></td><td>DD inflow</td></tr>
      <tr><td><code>ml.model_inference.member_level_fpf_model_v2_score</code></td><td>FPF v2 member-level score</td></tr>
    </table>
  </div>

  {data_tables_html}

  {f'''<div class="card">
    <div class="section-title">Giant Component (diagnostic — excluded from actionable views)</div>
    <div class="callout">
      <div class="head">Why we exclude it</div>
      <p>One mega-cluster of {fmt_int(giants.iloc[0]['cluster_size'])} members forms via long device-ID chains.
      Its loss rate is only {fmt_pct(giants.iloc[0]['loss_rate'])} — far below true rings (40–90%).
      This is a transitivity artifact, not a coordinated ring.</p>
    </div>
    <table>
      <tr><th>Cluster</th><th>Size</th><th>Dispute $</th><th>Loss $</th><th>Loss Rate</th><th># Addresses</th><th>Avg Peak FPF v2</th></tr>
      {row_giants(giants)}
    </table>
  </div>''' if len(giants) > 0 else ''}
</div>

<script>
const C_BLUE='#3b82f6', C_RED='#dc2626', C_AMBER='#f59e0b', C_GREEN='#059669', C_PURPLE='#7c3aed';

// Data
const savings_thresholds = {js_savings_thresholds};
const savings_loss = {js_savings_loss};
const size_labels = {js_size_labels};
const size_clusters = {js_size_clusters};
const size_members = {js_size_members};
const pareto_x = {js_pareto_x};
const pareto_y = {js_pareto_y};
const funding_labels = {js_funding_labels};
const funding_values = {js_funding_values};
const state_labels = {js_state_labels};
const state_dispute = {js_state_dispute};
const state_loss = {js_state_loss};
const state_hc_labels = {js_state_hc_labels};
const state_hc_n = {js_state_hc_n};
const signal_dist = {js_signal_dist};
const top10_clusters = {js_top10_clusters};
const top10_losses = {js_top10_losses};
const scatter_decline = {js_scatter_decline};
const scatter_fraud = {js_scatter_fraud};
const scatter_scanid = {js_scatter_scanid};
const scatter_pwfail = {js_scatter_pwfail};
const scatter_p2p_dd = {js_scatter_p2p_dd};
const scatter_ssn_addr = {js_scatter_ssn_addr};
const single_signals = {js_single_signals};
const single_savings = {js_single_savings};
const attr_labels = {js_attr_labels};
const attr_loss = {js_attr_loss};
const density_dist = {js_density_dist};
const inflow_labels = {js_inflow_labels};
const inflow_n = {js_inflow_n};

function baseOpts(showLeg, yFmt, xFmt) {{
  return {{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:!!showLeg,labels:{{font:{{size:11}},usePointStyle:true,pointStyle:'line',pointStyleWidth:24}}}},tooltip:{{mode:'index'}}}},
    scales:{{x:{{grid:{{color:'#f1f5f9'}},ticks:{{font:{{size:10.5}},callback:xFmt||(v=>typeof v==='string'?v:v)}}}},
             y:{{grid:{{color:'#f1f5f9'}},ticks:{{font:{{size:10.5}},callback:yFmt||(v=>v)}},beginAtZero:true}}}}}};
}}

// Tab 1
new Chart('c_size', {{type:'bar',
  data:{{labels:size_labels.slice(1), datasets:[
    {{label:'# Clusters', data:size_clusters.slice(1), backgroundColor:'#3b82f6cc', yAxisID:'y'}},
    {{label:'# Members',  data:size_members.slice(1),  backgroundColor:'#fbbf24cc', yAxisID:'y1'}}
  ]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{font:{{size:11}}}}}}}},
    scales:{{y:{{beginAtZero:true,title:{{display:true,text:'# Clusters',font:{{size:11}}}}}},
             y1:{{position:'right',beginAtZero:true,title:{{display:true,text:'# Members',font:{{size:11}}}},grid:{{drawOnChartArea:false}}}}}}}}}});

// Tab 2
new Chart('c_savings', {{type:'bar',
  data:{{labels:savings_thresholds.map(n=>'≥'+n+' signals'),
    datasets:[{{label:'Addressable Loss ($K)', data:savings_loss, backgroundColor:savings_loss.map((v,i)=>i<3?'#dc2626cc':i<5?'#f59e0bcc':'#059669cc')}}]
  }}, options:baseOpts(false, v=>'$'+v+'K')
}});
new Chart('c_signal_dist', {{type:'bar',
  data:{{labels:['0','1','2','3','4','5','6','7','8'].map(n=>n+' sig'),
    datasets:[{{label:'# Clusters', data:signal_dist, backgroundColor:signal_dist.map((v,i)=>i<3?'#94a3b8':i<5?'#f59e0bcc':'#dc2626cc')}}]
  }}, options:baseOpts(false)
}});
new Chart('c_single_signal', {{type:'bar',
  data:{{labels:single_signals,
    datasets:[{{label:'Addressable Loss ($K)', data:single_savings, backgroundColor:'#3b82f6cc'}}]
  }},
  options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{title:{{display:true,text:'Addressable Loss ($K)',font:{{size:11}}}},ticks:{{callback:v=>'$'+v+'K'}}}},
             y:{{ticks:{{font:{{size:11}}}}}}}}}}}});

// Tab 2 — Combo chart (bar = loss, line = # clusters)
const savings_clusters_cum = {js_savings_clusters_cum};
new Chart('c_savings_combo', {{
  data:{{
    labels: savings_thresholds.map(n=>'≥'+n),
    datasets:[
      {{type:'bar', label:'Addressable Loss ($K)', data:savings_loss, backgroundColor:'#dc2626aa', yAxisID:'y'}},
      {{type:'line',label:'# Clusters', data:savings_clusters_cum, borderColor:'#1e3a8a', backgroundColor:'#1e3a8a', tension:0.2, pointRadius:5, yAxisID:'y1'}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:24,font:{{size:11}}}}}}}},
    scales:{{
      x:{{title:{{display:true,text:'Signal threshold (# of 8 signals firing)',font:{{size:11}}}}}},
      y:{{position:'left',  beginAtZero:true, title:{{display:true,text:'$K addressable'}}, ticks:{{callback:v=>'$'+v+'K'}}}},
      y1:{{position:'right',beginAtZero:true, title:{{display:true,text:'# Clusters'}}, grid:{{drawOnChartArea:false}}}}
    }}}}
}});

// Tab 3 — Stacked FC vs NB for top 10
const top10_labels = {js_top10_labels};
const top10_fc = {js_top10_fc};
const top10_nb = {js_top10_nb};
new Chart('c_fc_nb_stack', {{type:'bar',
  data:{{labels:top10_labels,
    datasets:[
      {{label:'Final Credit (FC)', data:top10_fc, backgroundColor:'#3b82f6cc', stack:'a'}},
      {{label:'Negative Balance (NB)', data:top10_nb, backgroundColor:'#dc2626cc', stack:'a'}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{font:{{size:11}}}}}}}},
    scales:{{x:{{stacked:true,title:{{display:true,text:'Top 10 clusters by total loss'}}}},
             y:{{stacked:true,beginAtZero:true,ticks:{{callback:v=>'$'+v+'K'}}}}}}}}}});

// Tab 4 — Stacked decline-code mix for top 10 decline-rate clusters
const decline_labels = {js_decline_labels};
const decline_nsf      = {js_decline_nsf};
const decline_fraud    = {js_decline_fraud};
const decline_cardtest = {js_decline_cardtest};
const decline_frozen   = {js_decline_frozen};
const decline_other    = {js_decline_other};
new Chart('c_decline_stack', {{type:'bar',
  data:{{labels:decline_labels,
    datasets:[
      {{label:'NSF (51)', data:decline_nsf, backgroundColor:'#94a3b8cc', stack:'a'}},
      {{label:'Fraud-flagged (59)', data:decline_fraud, backgroundColor:'#dc2626cc', stack:'a'}},
      {{label:'Card-testing (14/54/82/N7)', data:decline_cardtest, backgroundColor:'#f59e0bcc', stack:'a'}},
      {{label:'Frozen (78/LK/5C/9G)', data:decline_frozen, backgroundColor:'#7c3aedcc', stack:'a'}},
      {{label:'Other', data:decline_other, backgroundColor:'#e5e7eb', stack:'a'}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{font:{{size:11}}}}}}}},
    scales:{{x:{{stacked:true,title:{{display:true,text:'Top 10 decline-rate clusters'}}}},
             y:{{stacked:true,beginAtZero:true,title:{{display:true,text:'# Declines'}}}}}}}}}});

// Tab 5 — Stacked funding mix for top 10 P2P-heavy
const funding_top_labels = {js_funding_top_labels};
const funding_top_dd     = {js_funding_top_dd};
const funding_top_p2p    = {js_funding_top_p2p};
const funding_top_dep    = {js_funding_top_dep};
const funding_top_other  = {js_funding_top_other};
new Chart('c_funding_stack', {{type:'bar',
  data:{{labels:funding_top_labels,
    datasets:[
      {{label:'Direct Deposit', data:funding_top_dd,     backgroundColor:'#3b82f6cc', stack:'a'}},
      {{label:'P2P / Transfers', data:funding_top_p2p,  backgroundColor:'#dc2626cc', stack:'a'}},
      {{label:'Deposits (cash/check)', data:funding_top_dep, backgroundColor:'#f59e0bcc', stack:'a'}},
      {{label:'Other', data:funding_top_other, backgroundColor:'#94a3b8cc', stack:'a'}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{font:{{size:11}}}}}}}},
    scales:{{x:{{stacked:true,title:{{display:true,text:'Top 10 P2P-heavy clusters'}}}},
             y:{{stacked:true,beginAtZero:true,ticks:{{callback:v=>'$'+v+'K'}}, title:{{display:true,text:'Inflow ($K)'}}}}}}}}}});

// Tab 6 — Radar
const radar_labels = {js_radar_labels};
const radar_ex0 = {js_radar_ex0};
const radar_ex1 = {js_radar_ex1};
const radar_ex2 = {js_radar_ex2};
new Chart('c_radar', {{type:'radar',
  data:{{labels:radar_labels,
    datasets:[
      {{label:{js_radar_ex0_name}, data:radar_ex0, borderColor:'#dc2626', backgroundColor:'#dc262622', pointBackgroundColor:'#dc2626'}},
      {{label:{js_radar_ex1_name}, data:radar_ex1, borderColor:'#f59e0b', backgroundColor:'#f59e0b22', pointBackgroundColor:'#f59e0b'}},
      {{label:{js_radar_ex2_name}, data:radar_ex2, borderColor:'#3b82f6', backgroundColor:'#3b82f622', pointBackgroundColor:'#3b82f6'}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{font:{{size:11}}}}}}}},
    scales:{{r:{{beginAtZero:true,suggestedMax:1.05,ticks:{{callback:v=>v.toFixed(2)}},pointLabels:{{font:{{size:11.5}}}}}}}}}}}});

// Tab 3 — Pareto with line legend
new Chart('c_pareto', {{type:'line',
  data:{{labels:pareto_x,
    datasets:[{{label:'Cumulative loss %', data:pareto_y, borderColor:C_BLUE, backgroundColor:C_BLUE+'22', fill:true, tension:0.2, pointRadius:0}}]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:true,labels:{{usePointStyle:true,pointStyle:'line',pointStyleWidth:32,font:{{size:11}}}}}}}},
    scales:{{x:{{title:{{display:true,text:'Cluster rank (1 = worst, 100 = least bad)',font:{{size:11}}}}}},
             y:{{title:{{display:true,text:'Cumulative loss %'}}, ticks:{{callback:v=>v+'%'}}, max:100, beginAtZero:true}}}}}}}});
new Chart('c_top10_bar', {{type:'bar',
  data:{{labels:top10_clusters, datasets:[{{label:'Loss $K', data:top10_losses, backgroundColor:'#dc2626cc'}}]}},
  options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{title:{{display:true,text:'Loss ($K)',font:{{size:11}}}},ticks:{{callback:v=>'$'+v+'K'}}}}}}}}}});

// Tab 4 — 4 scatters
function scatterOpts(xLabel, yLabel) {{
  return {{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{title:{{display:true,text:xLabel,font:{{size:11}}}}, ticks:{{callback:v=>v+'%'}}}},
             y:{{title:{{display:true,text:yLabel,font:{{size:11}}}}, ticks:{{callback:v=>'$'+v.toLocaleString()}}, beginAtZero:true}}}}}};
}}
new Chart('c_scatter_decline', {{type:'scatter',
  data:{{datasets:[{{label:'Cluster', data:scatter_decline, backgroundColor:C_RED+'88', pointRadius:5}}]}},
  options:scatterOpts('Decline rate %', 'Loss $')}});
new Chart('c_scatter_fraud', {{type:'scatter',
  data:{{datasets:[{{label:'Cluster', data:scatter_fraud, backgroundColor:C_AMBER+'aa', pointRadius:5}}]}},
  options:scatterOpts('Fraud-flagged decline rate %', 'Loss $')}});
new Chart('c_scatter_scanid', {{type:'scatter',
  data:{{datasets:[{{label:'Cluster', data:scatter_scanid, backgroundColor:C_PURPLE+'88', pointRadius:5}}]}},
  options:scatterOpts('ScanID required rate %', 'Loss $')}});
new Chart('c_scatter_pwfail', {{type:'scatter',
  data:{{datasets:[{{label:'Cluster', data:scatter_pwfail, backgroundColor:C_BLUE+'88', pointRadius:5}}]}},
  options:scatterOpts('Password-fail rate %', 'Loss $')}});

// Tab 5
new Chart('c_funding_pie', {{type:'doughnut',
  data:{{labels:funding_labels, datasets:[{{data:funding_values, backgroundColor:['#3b82f6','#dc2626','#f59e0b','#94a3b8']}}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'right',labels:{{font:{{size:12}}}}}},tooltip:{{callbacks:{{label:c=>c.label+': $'+c.raw+'M'}}}}}}}}}});
new Chart('c_inflow_hist', {{type:'bar',
  data:{{labels:inflow_labels, datasets:[{{label:'# Clusters', data:inflow_n, backgroundColor:inflow_n.map((v,i)=>i<3?'#94a3b8':i<4?'#f59e0bcc':'#dc2626cc')}}]}},
  options:baseOpts(false)
}});
new Chart('c_p2p_dd_scatter', {{type:'bubble',
  data:{{datasets:[{{label:'Cluster', data:scatter_p2p_dd, backgroundColor:C_RED+'66', borderColor:C_RED}}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'P2P '+c.raw.x+'% / DD '+c.raw.y+'%'}}}}}},
    scales:{{x:{{title:{{display:true,text:'P2P share of inflow (%)',font:{{size:11}}}}, ticks:{{callback:v=>v+'%'}}}},
             y:{{title:{{display:true,text:'DD share of inflow (%)',font:{{size:11}}}}, ticks:{{callback:v=>v+'%'}}}}}}}}}});

// Tab 6
new Chart('c_ssn_addr', {{type:'bubble',
  data:{{datasets:[{{label:'Cluster', data:scatter_ssn_addr, backgroundColor:C_RED+'66', borderColor:C_RED}}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>'# Addr '+c.raw.x+' / # SSN '+c.raw.y}}}}}},
    scales:{{x:{{title:{{display:true,text:'# Distinct addresses in cluster',font:{{size:11}}}}}},
             y:{{title:{{display:true,text:'# Distinct SSNs in cluster',font:{{size:11}}}}}}}}}}}});
new Chart('c_density', {{type:'bar',
  data:{{labels:density_dist.map((_,i)=>''),
    datasets:[{{data:density_dist,backgroundColor:density_dist.map(v=>v>=3?'#dc2626cc':v>=2?'#f59e0bcc':'#3b82f6cc')}}]}},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}},
    scales:{{x:{{display:false}}, y:{{title:{{display:true,text:'Address density',font:{{size:11}}}}}}}}}}}});

// Tab 7
new Chart('c_state', {{type:'bar',
  data:{{labels:state_labels, datasets:[
    {{label:'Dispute $K', data:state_dispute, backgroundColor:'#3b82f6cc'}},
    {{label:'Loss $K',    data:state_loss,    backgroundColor:'#dc2626cc'}}
  ]}}, options:baseOpts(true, v=>'$'+v+'K')
}});
new Chart('c_state_hc', {{type:'bar',
  data:{{labels:state_hc_labels, datasets:[{{label:'# High-Conv Clusters', data:state_hc_n, backgroundColor:'#dc2626cc'}}]}},
  options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{title:{{display:true,text:'# clusters with ≥3 signals',font:{{size:11}}}}}}}}}}}});

function showTab(i) {{
  document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('active', i===j));
  document.querySelectorAll('.content').forEach((c,j)=>c.classList.toggle('active', i===j));
  window.scrollTo(0,0);
}}
</script>
</body>
</html>"""

with open(HTML_OUT, 'w') as f:
    f.write(html)

print(f"Saved: {HTML_OUT}")
print(f"  high-conviction clusters (≥3 signals): {composite_3plus}")
print(f"  addressable loss at ≥3:               ${sav3/1e6:.2f}M")
print(f"  addressable loss at ≥5:               ${sav5/1e6:.2f}M")
print(f"  strongest single signal: {strongest_single['signal']} @ ${strongest_single['addressable_loss']/1e3:.0f}K")

subprocess.run(['open', HTML_OUT])
conn.close()
print("\nDone — dashboard opened in browser.")
