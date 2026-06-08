import sys, os, re
sys.stdout.reconfigure(line_buffering=True)

import pandas as pd
import numpy as np

# Outputs from linkage_01 are read from clusterlink/outputs/.
BASE       = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE, 'outputs')

# ─────────────────────────────────────────────────────────────────────────────
# Load Step 1 outputs
# ─────────────────────────────────────────────────────────────────────────────
print("Loading Step 1 outputs...")
pii = pd.read_csv(f'{OUTPUT_DIR}/02_pii_attributes.csv', dtype={
    'user_id': 'int64',
    'email_normalized': 'string',
    'phone_normalized': 'string',
    'ssn_digest': 'string',
    'address_normalized': 'string',
})
devices = pd.read_csv(f'{OUTPUT_DIR}/03_device_ids.csv', dtype={
    'user_id': 'int64',
    'device_id': 'string',
})
print(f"  PII rows:    {len(pii):,} ({pii['user_id'].nunique():,} users)")
print(f"  Device rows: {len(devices):,} ({devices['user_id'].nunique():,} users)\n")

# ─────────────────────────────────────────────────────────────────────────────
# EDA + Python-level cleaning (on top of SQL normalization)
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP A: EDA + cleaning")
print("=" * 60)

def report(field_name, before_series, after_series):
    before_nn = before_series.notna().sum()
    after_nn  = after_series.notna().sum()
    dropped   = before_nn - after_nn
    print(f"  {field_name:<20} kept={after_nn:>7,}  dropped={dropped:>6,}  ({dropped/max(before_nn,1)*100:.2f}% of populated)")

# ── EMAIL: strip dots from gmail local part; validate basic shape ─────────────
def clean_email(e):
    if pd.isna(e) or not isinstance(e, str) or '@' not in e:
        return pd.NA
    local, _, domain = e.partition('@')
    if not local or not domain or '.' not in domain:
        return pd.NA
    # Gmail dot-stripping: john.smith@gmail.com == johnsmith@gmail.com
    if domain in ('gmail.com', 'googlemail.com'):
        local = local.replace('.', '')
    return f"{local}@{domain}"

email_before = pii['email_normalized'].copy()
pii['email_clean'] = pii['email_normalized'].apply(clean_email).astype('string')
report('email', email_before, pii['email_clean'])

# ── PHONE: drop 555-prefix (reserved fictional / masked) ──────────────────────
phone_before = pii['phone_normalized'].copy()
def clean_phone(p):
    if pd.isna(p) or not isinstance(p, str):
        return pd.NA
    p = p.strip()
    if not p.isdigit() or len(p) != 10:
        return pd.NA
    # 555 prefix = reserved fictional / masked at Chime
    if p.startswith('555'):
        return pd.NA
    # Obvious junk: all-same digit, sequential
    if len(set(p)) == 1:
        return pd.NA
    if p in ('0123456789', '1234567890', '9876543210'):
        return pd.NA
    return p

pii['phone_clean'] = pii['phone_normalized'].apply(clean_phone).astype('string')
report('phone', phone_before, pii['phone_clean'])

# ── SSN: keep non-null, non-blank ─────────────────────────────────────────────
ssn_before = pii['ssn_digest'].copy()
pii['ssn_clean'] = pii['ssn_digest'].where(
    pii['ssn_digest'].notna() & (pii['ssn_digest'].astype(str).str.strip() != ''),
    other=pd.NA
).astype('string')
report('ssn_digest', ssn_before, pii['ssn_clean'])

# ── ADDRESS: strip residual chars, min length, drop junk ──────────────────────
JUNK_ADDR = {'', 'NA', 'N/A', 'NONE', 'NULL', 'UNKNOWN', 'TEST', 'NO ADDRESS', 'ADDRESS'}
def clean_address(a):
    if pd.isna(a) or not isinstance(a, str):
        return pd.NA
    # Strip any remaining special chars and collapse whitespace
    s = re.sub(r'[^A-Z0-9 ]', '', a.upper())
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) < 5 or s in JUNK_ADDR:
        return pd.NA
    return s

addr_before = pii['address_normalized'].copy()
pii['address_clean'] = pii['address_normalized'].apply(clean_address).astype('string')
report('address', addr_before, pii['address_clean'])

# ── DEVICE: drop 'NA', nulls, very short ──────────────────────────────────────
devices_before_n = len(devices)
devices['device_clean'] = devices['device_id'].astype('string').str.strip()
mask = (
    devices['device_clean'].notna()
    & (devices['device_clean'] != '')
    & (devices['device_clean'].str.upper() != 'NA')
    & (devices['device_clean'].str.len() >= 8)
)
devices_clean = devices[mask][['user_id', 'device_clean']].drop_duplicates()
devices_clean = devices_clean.rename(columns={'device_clean': 'device_id'})
print(f"  device_id            kept={len(devices_clean):>7,}  dropped={devices_before_n-len(devices_clean):>6,}  ({(devices_before_n-len(devices_clean))/devices_before_n*100:.2f}% of pairs)")
print(f"                       users with ≥1 device after clean: {devices_clean['user_id'].nunique():,}")

# Persist the cleaned PII for transparency
clean_cols = ['user_id', 'email_clean', 'phone_clean', 'ssn_clean', 'address_clean']
pii_out = pii[clean_cols].copy()
pii_out.to_csv(f'{OUTPUT_DIR}/06_pii_cleaned.csv', index=False)
print(f"\nSaved → {OUTPUT_DIR}/06_pii_cleaned.csv\n")

# ─────────────────────────────────────────────────────────────────────────────
# STEP B: Post-clean sharing distribution
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP B: Post-clean sharing distribution")
print("=" * 60)

def sharing_summary(series_or_pairs, field_name, value_col=None):
    """Show how many users share each value, bucketed."""
    if value_col is None:
        # Series of per-user values, one row per user
        counts = series_or_pairs.dropna().value_counts()
    else:
        # DataFrame of (user_id, value) pairs (e.g. devices)
        counts = series_or_pairs.groupby(value_col)['user_id'].nunique()
    buckets = pd.cut(counts, bins=[0,1,2,5,10,20,50,100,500,float('inf')],
                     labels=['1','2','3–5','6–10','11–20','21–50','51–100','101–500','500+'])
    d = counts.groupby(buckets, observed=False).agg(unique_values='count', total_users='sum').reset_index()
    print(f"\n  {field_name}:")
    print(d.to_string(index=False))
    return d, counts

share_summaries = {}
for field in ['email_clean', 'phone_clean', 'ssn_clean', 'address_clean']:
    d, counts = sharing_summary(pii[field], field)
    share_summaries[field] = (d, counts)
d, counts = sharing_summary(devices_clean, 'device_id', value_col='device_id')
share_summaries['device_id'] = (d, counts)

# ─────────────────────────────────────────────────────────────────────────────
# STEP C: Union-Find clustering
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP C: Union-Find clustering")
print("=" * 60)

class UnionFind:
    def __init__(self):
        self.parent = {}
        self.rank = {}
    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
            return x
        # path compression
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

def build_clusters(seed_users, attr_dfs):
    """
    seed_users: iterable of all user_ids in the population
    attr_dfs:   list of (label, dataframe with columns ['user_id', 'value'])
                Each (value, [users sharing it]) becomes a union group.
    Returns: dict user_id -> cluster_label (string)
    """
    uf = UnionFind()
    for u in seed_users:
        uf.find(int(u))
    for label, df in attr_dfs:
        # Group users by attribute value; union all users sharing each value
        grouped = df.dropna().groupby('value')['user_id'].apply(list)
        for value, users in grouped.items():
            if len(users) < 2:
                continue
            anchor = int(users[0])
            for u in users[1:]:
                uf.union(anchor, int(u))
    # Build user → root mapping
    return {int(u): uf.find(int(u)) for u in seed_users}

# Build per-attribute (user_id, value) tables
all_users = pii['user_id'].unique()

def attr_df(series_field):
    df = pii[['user_id', series_field]].rename(columns={series_field: 'value'})
    return df.dropna(subset=['value'])

email_df   = attr_df('email_clean')
phone_df   = attr_df('phone_clean')
ssn_df     = attr_df('ssn_clean')
addr_df    = attr_df('address_clean')
device_df  = devices_clean.rename(columns={'device_id': 'value'})

# cluster_1: ALL attributes (transitive)
print("\n  Building cluster_1 (all 5 attributes, transitive)...")
c1 = build_clusters(all_users, [
    ('email', email_df),
    ('phone', phone_df),
    ('ssn', ssn_df),
    ('address', addr_df),
    ('device', device_df),
])

# cluster_2: email only
print("  Building cluster_2 (email)...")
c2 = build_clusters(all_users, [('email', email_df)])

# cluster_3: address only
print("  Building cluster_3 (address)...")
c3 = build_clusters(all_users, [('address', addr_df)])

# cluster_4: ssn only
print("  Building cluster_4 (ssn_digest)...")
c4 = build_clusters(all_users, [('ssn', ssn_df)])

# cluster_5: phone only
print("  Building cluster_5 (phone)...")
c5 = build_clusters(all_users, [('phone', phone_df)])

# cluster_6: device only
print("  Building cluster_6 (device_id)...")
c6 = build_clusters(all_users, [('device', device_df)])

# Re-label roots → compact integer cluster IDs per column
def compact_labels(mapping):
    """Singleton users (root == self & cluster size 1) → cluster_id = 0.
       Multi-user components → sequential ints from 1."""
    # Count component sizes
    size = {}
    for u, root in mapping.items():
        size[root] = size.get(root, 0) + 1
    # Assign new IDs only to multi-user roots
    new_id = {}
    counter = 1
    for root in size:
        if size[root] >= 2:
            new_id[root] = counter
            counter += 1
    out = {}
    for u, root in mapping.items():
        out[u] = new_id.get(root, 0)  # 0 = singleton
    return out, counter - 1  # counter-1 = number of non-singleton clusters

c1_clean, n1 = compact_labels(c1)
c2_clean, n2 = compact_labels(c2)
c3_clean, n3 = compact_labels(c3)
c4_clean, n4 = compact_labels(c4)
c5_clean, n5 = compact_labels(c5)
c6_clean, n6 = compact_labels(c6)

print(f"\n  Non-singleton clusters formed:")
print(f"    cluster_1 (all attributes, transitive): {n1:,}")
print(f"    cluster_2 (email):                       {n2:,}")
print(f"    cluster_3 (address):                     {n3:,}")
print(f"    cluster_4 (ssn_digest):                  {n4:,}")
print(f"    cluster_5 (phone):                       {n5:,}")
print(f"    cluster_6 (device_id):                   {n6:,}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP D: Build member × cluster table
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP D: Member × cluster table")
print("=" * 60)

result = pd.DataFrame({'user_id': sorted(all_users)})
result['cluster_1'] = result['user_id'].map(c1_clean)
result['cluster_2'] = result['user_id'].map(c2_clean)
result['cluster_3'] = result['user_id'].map(c3_clean)
result['cluster_4'] = result['user_id'].map(c4_clean)
result['cluster_5'] = result['user_id'].map(c5_clean)
result['cluster_6'] = result['user_id'].map(c6_clean)

result.to_csv(f'{OUTPUT_DIR}/07_member_clusters.csv', index=False)
print(f"  Saved → {OUTPUT_DIR}/07_member_clusters.csv  ({len(result):,} members)")

# ─────────────────────────────────────────────────────────────────────────────
# STEP E: Cluster size distribution per cluster column
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP E: Cluster size distribution")
print("=" * 60)

def cluster_size_dist(series, name):
    sizes = series[series != 0].value_counts()  # exclude singletons (cluster=0)
    if sizes.empty:
        print(f"\n  {name}: no multi-member clusters")
        return pd.DataFrame()
    buckets = pd.cut(sizes, bins=[0,1,2,5,10,20,50,100,500,float('inf')],
                     labels=['1','2','3–5','6–10','11–20','21–50','51–100','101–500','500+'])
    d = sizes.groupby(buckets, observed=False).agg(num_clusters='count', total_members='sum').reset_index()
    d.columns = ['cluster_size', 'num_clusters', 'total_members']
    print(f"\n  {name}:")
    print(d.to_string(index=False))
    print(f"    largest cluster: {sizes.max():,} members  (cluster_id={sizes.idxmax()})")
    print(f"    members in non-singleton clusters: {sizes.sum():,} / {len(series):,}  ({sizes.sum()/len(series)*100:.1f}%)")
    return d

dist_rows = []
for col in ['cluster_1', 'cluster_2', 'cluster_3', 'cluster_4', 'cluster_5', 'cluster_6']:
    d = cluster_size_dist(result[col], col)
    if not d.empty:
        d['cluster_col'] = col
        dist_rows.append(d)
size_dist_df = pd.concat(dist_rows, ignore_index=True) if dist_rows else pd.DataFrame()
size_dist_df.to_csv(f'{OUTPUT_DIR}/08_cluster_size_distribution.csv', index=False)
print(f"\nSaved → {OUTPUT_DIR}/08_cluster_size_distribution.csv")

# ─────────────────────────────────────────────────────────────────────────────
# STEP F: Top-N largest clusters per cluster column (for sanity check)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP F: Top 10 largest clusters per cluster column")
print("=" * 60)

top_rows = []
for col in ['cluster_1', 'cluster_2', 'cluster_3', 'cluster_4', 'cluster_5', 'cluster_6']:
    sizes = result[result[col] != 0][col].value_counts().head(10)
    print(f"\n  {col} — top 10:")
    for cid, sz in sizes.items():
        print(f"    cluster_id={cid:>6}  members={sz:>5,}")
        top_rows.append({'cluster_col': col, 'cluster_id': cid, 'members': int(sz)})
pd.DataFrame(top_rows).to_csv(f'{OUTPUT_DIR}/09_top_clusters.csv', index=False)
print(f"\nSaved → {OUTPUT_DIR}/09_top_clusters.csv")

print("\n" + "=" * 60)
print("Done. Outputs:")
print(f"  {OUTPUT_DIR}/06_pii_cleaned.csv")
print(f"  {OUTPUT_DIR}/07_member_clusters.csv")
print(f"  {OUTPUT_DIR}/08_cluster_size_distribution.csv")
print(f"  {OUTPUT_DIR}/09_top_clusters.csv")
print("=" * 60)
