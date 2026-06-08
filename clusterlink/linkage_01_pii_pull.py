import sys, os
sys.stdout.reconfigure(line_buffering=True)

import pandas as pd
from sqlalchemy import create_engine, text
from snowflake.sqlalchemy import URL

# ── CONFIG ────────────────────────────────────────────────────────────────
# Set your Chime Snowflake email before the first run.
SNOWFLAKE_USER = 'YOUR.EMAIL@CHIME.COM'

# Outputs land next to this script in clusterlink/outputs/ (auto-created).
BASE       = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE, 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

engine = create_engine(URL(
    user=SNOWFLAKE_USER,
    authenticator='externalbrowser',
    account='CHIME',
    warehouse='ANALYTICS_WH',
    role='SNOWFLAKE_PROD_ANALYTICS_PII_ROLE_OKTA'
))

print("Connecting to Snowflake...")
conn = engine.connect()
print("Connected.\n")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0: Schema discovery — confirms column names before we query
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("SCHEMA: edw_pii_db.core.dim_user_pii")
print("=" * 60)
pii_schema = pd.read_sql(text("""
    select column_name, data_type, is_nullable
    from edw_pii_db.information_schema.columns
    where table_schema = 'CORE' and table_name = 'DIM_USER_PII'
    order by ordinal_position
"""), conn)
print(pii_schema.to_string(index=False))
pii_cols = set(pii_schema['column_name'].str.upper().tolist())

print()
print("=" * 60)
print("SCHEMA: analytics.looker.device_sessions")
print("=" * 60)
device_schema = pd.read_sql(text("""
    select column_name, data_type, is_nullable
    from analytics.information_schema.columns
    where table_schema = 'LOOKER' and table_name = 'DEVICE_SESSIONS'
    order by ordinal_position
"""), conn)
print(device_schema.to_string(index=False))
device_cols = set(device_schema['column_name'].str.upper().tolist())
print()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: Seed population
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 1: Seed population (Jan–Mar 2026, dispute_amount >= $500)")
print("=" * 60)
seed_df = pd.read_sql(text("""
    select distinct use_id as user_id
    from rest.test.ub_dispute_exception_reporting_base
    where claim_created_at::date >= '2025-10-01'
      and claim_created_at::date <= '2026-03-31'
      and dispute_amount >= 500
"""), conn)
print(f"Seed population: {len(seed_df):,} unique users")
seed_df.to_csv(f'{OUTPUT_DIR}/01_seed_users.csv', index=False)
print(f"Saved → {OUTPUT_DIR}/01_seed_users.csv\n")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: PII attributes — normalized
# Address column name may vary; try ADDRESS_LINE_1 first, fall back to ADDRESS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 2: PII attributes (normalized)")
print("=" * 60)

if 'ADDRESS_LINE_1' in pii_cols:
    addr_raw = "coalesce(p.address_line_1, '') || case when p.address_line_2 is not null and trim(p.address_line_2) != '' then ' ' || trim(p.address_line_2) else '' end"
    addr_null_check = "p.address_line_1"
elif 'ADDRESS' in pii_cols:
    addr_raw = "coalesce(p.address, '')"
    addr_null_check = "p.address"
else:
    addr_raw = "''"
    addr_null_check = "null"
    print("WARNING: No address column found in dim_user_pii — address_normalized will be null")

addr_expr = f"""
        case when {addr_null_check} is null or trim({addr_null_check}) = '' then null
             else trim(regexp_replace(upper(trim({addr_raw})), '[^A-Z0-9 ]', ''))
        end"""

pii_df = pd.read_sql(text(f"""
    with disputers as (
        select distinct use_id as user_id
        from rest.test.ub_dispute_exception_reporting_base
        where claim_created_at::date >= '2025-10-01'
          and claim_created_at::date <= '2026-03-31'
          and dispute_amount >= 500
    )
    select
        d.user_id,

        -- Email: lowercase, remove +alias (user+tag@gmail.com → user@gmail.com)
        case when p.email is null or trim(p.email) = '' then null
             else lower(trim(
                 regexp_replace(split_part(p.email, '@', 1), '\\\\+.*$', '')
                 || '@' || split_part(lower(trim(p.email)), '@', 2)
             ))
        end as email_normalized,

        -- Phone: digits only; if 11 digits starting with 1, strip country code
        case when p.phone is null then null
             when length(regexp_replace(p.phone, '[^0-9]', '')) = 11
                  and left(regexp_replace(p.phone, '[^0-9]', ''), 1) = '1'
             then right(regexp_replace(p.phone, '[^0-9]', ''), 10)
             when length(regexp_replace(p.phone, '[^0-9]', '')) = 10
             then regexp_replace(p.phone, '[^0-9]', '')
             else null
        end as phone_normalized,

        -- SSN digest: already hashed, use as-is
        p.ssn_digest,

        -- Address: uppercase, strip punctuation/special chars
        {addr_expr} as address_normalized

    from disputers d
    left join edw_pii_db.core.dim_user_pii p on d.user_id = p.user_id
"""), conn)

print(f"PII rows pulled: {len(pii_df):,}")
pii_df.to_csv(f'{OUTPUT_DIR}/02_pii_attributes.csv', index=False)
print(f"Saved → {OUTPUT_DIR}/02_pii_attributes.csv\n")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Device IDs
# device_id column name may vary across the table
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 3: Device IDs")
print("=" * 60)

# Detect the right device identifier column
device_id_col = None
for candidate in ['DEVICE_ID', 'DSML_DEVICE_ID', 'SARDINE_DEVICE_ID', 'ID']:
    if candidate in device_cols:
        device_id_col = candidate.lower()
        break

# Detect user identifier column
user_id_col = 'user_id' if 'USER_ID' in device_cols else None

if device_id_col and user_id_col:
    print(f"Using device column: {device_id_col}")
    device_df = pd.read_sql(text(f"""
        with disputers as (
            select distinct use_id as user_id
            from rest.test.ub_dispute_exception_reporting_base
            where claim_created_at::date >= '2025-10-01'
              and claim_created_at::date <= '2026-03-31'
              and dispute_amount >= 500
        )
        select distinct
            d.user_id,
            ds.{device_id_col} as device_id
        from disputers d
        join analytics.looker.device_sessions ds on d.user_id = ds.{user_id_col}
        where ds.{device_id_col} is not null
    """), conn)
    print(f"Device rows: {len(device_df):,} (user × device pairs)")
    print(f"Distinct users with ≥1 device: {device_df['user_id'].nunique():,}")
    device_df.to_csv(f'{OUTPUT_DIR}/03_device_ids.csv', index=False)
    print(f"Saved → {OUTPUT_DIR}/03_device_ids.csv\n")
else:
    print("WARNING: Could not auto-detect device_id or user_id column in device_sessions.")
    print(f"Columns found: {sorted(device_cols)}")
    print("Update device_id_col / user_id_col manually and re-run this step.\n")
    device_df = pd.DataFrame(columns=['user_id', 'device_id'])

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Basic analytics — null rates, cardinality, sharing distribution
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 4: Analytics — null rates & cardinality")
print("=" * 60)

total = len(pii_df)
fields = {
    'email_normalized': pii_df['email_normalized'],
    'phone_normalized': pii_df['phone_normalized'],
    'ssn_digest': pii_df['ssn_digest'],
    'address_normalized': pii_df['address_normalized'],
}
if not device_df.empty:
    # devices: count distinct per user
    dev_per_user = device_df.groupby('user_id')['device_id'].nunique().reset_index(name='device_cnt')
    has_device = pii_df['user_id'].isin(dev_per_user['user_id'])
    fields['device_id'] = pii_df['user_id'].map(has_device.astype(object).where(has_device, None))

print(f"\n{'Field':<22} {'Non-null':>10} {'Null':>8} {'% populated':>13} {'Distinct values':>16}")
print("-" * 73)
analytics_rows = []
for field, series in fields.items():
    if field == 'device_id':
        non_null = int(has_device.sum())
        null = total - non_null
        distinct = int(device_df['device_id'].nunique()) if not device_df.empty else 0
    else:
        non_null = int(series.notna().sum())
        null = total - non_null
        distinct = int(series.nunique())
    pct = non_null / total * 100 if total > 0 else 0
    print(f"{field:<22} {non_null:>10,} {null:>8,} {pct:>12.1f}% {distinct:>16,}")
    analytics_rows.append({'field': field, 'non_null': non_null, 'null': null, 'pct_populated': round(pct, 2), 'distinct_values': distinct})

analytics_df = pd.DataFrame(analytics_rows)
analytics_df.to_csv(f'{OUTPUT_DIR}/04_analytics_summary.csv', index=False)
print(f"\nSaved → {OUTPUT_DIR}/04_analytics_summary.csv\n")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: Sharing distribution — how many users share each value?
# This is the key noise-detection step. Values shared by 100+ users are likely
# institutional addresses / test phones / etc. and should be excluded.
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 5: Sharing distribution (linkage signal strength)")
print("=" * 60)

def sharing_distribution(series, field_name, top_n=20, show_values=True):
    """For each unique value, count how many users share it. Then bucket."""
    s = series.dropna()
    counts = s.value_counts()

    buckets = pd.cut(counts, bins=[0,1,2,5,10,20,50,100,500,float('inf')],
                     labels=['exactly 1','2','3–5','6–10','11–20','21–50','51–100','101–500','500+'])
    dist = counts.groupby(buckets).agg(
        unique_values='count',
        total_users='sum'
    ).reset_index()
    dist.columns = ['users_sharing_value', 'unique_values', 'total_users_linked']

    print(f"\n── {field_name} sharing distribution ──")
    print(dist.to_string(index=False))

    # Top shared values (potential noise — skip SSN for privacy)
    if show_values and len(counts) > 0:
        top = counts.head(top_n).reset_index()
        top.columns = ['value', 'users_sharing']
        print(f"\n  Top {top_n} most-shared {field_name} values (noise candidates):")
        print(top.to_string(index=False))

    return dist

dists = []
for field in ['email_normalized', 'phone_normalized', 'address_normalized']:
    d = sharing_distribution(pii_df[field], field, top_n=20, show_values=True)
    d['field'] = field
    dists.append(d)

# SSN: show distribution but NOT values
if pii_df['ssn_digest'].notna().any():
    d = sharing_distribution(pii_df['ssn_digest'], 'ssn_digest', top_n=0, show_values=False)
    d['field'] = 'ssn_digest'
    dists.append(d)

# Devices
if not device_df.empty:
    dev_counts = device_df.groupby('device_id')['user_id'].nunique()
    dev_buckets = pd.cut(dev_counts, bins=[0,1,2,5,10,20,50,100,500,float('inf')],
                         labels=['exactly 1','2','3–5','6–10','11–20','21–50','51–100','101–500','500+'])
    dev_dist = dev_counts.groupby(dev_buckets).agg(
        unique_values='count',
        total_users='sum'
    ).reset_index()
    dev_dist.columns = ['users_sharing_value', 'unique_values', 'total_users_linked']
    dev_dist['field'] = 'device_id'
    print(f"\n── device_id sharing distribution ──")
    print(dev_dist.to_string(index=False))

    top_devices = dev_counts.sort_values(ascending=False).head(20).reset_index()
    top_devices.columns = ['device_id', 'users_sharing']
    print(f"\n  Top 20 most-shared device IDs (noise candidates):")
    print(top_devices.to_string(index=False))

    dists.append(dev_dist)

sharing_df = pd.concat(dists, ignore_index=True)
sharing_df.to_csv(f'{OUTPUT_DIR}/05_sharing_distribution.csv', index=False)
print(f"\nSaved → {OUTPUT_DIR}/05_sharing_distribution.csv")

print("\n" + "=" * 60)
print("Done. All outputs in:", OUTPUT_DIR)
print("=" * 60)
conn.close()
