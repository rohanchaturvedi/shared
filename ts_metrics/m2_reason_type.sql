-- Metric 2: Dispute Rate by Reason Type — pivoted, last 13 mature months
-- Powers: pivot table (EA/Non-reg/UT/Grand Total/%UT/%EA+NonReg)
--         + line charts for UT and EA+NonReg trends
-- "Mature months" = excludes current in-progress month (mth_offset -13 to -1)

WITH deduped_disputes AS (
    SELECT user_dispute_claim_id, unique_transaction_id, dispute_created_at, reason
    FROM risk.prod.disputed_transactions
    QUALIFY ROW_NUMBER() OVER (PARTITION BY user_dispute_claim_id, unique_transaction_id
                               ORDER BY dispute_created_at) = 1
),
base AS (
    SELECT
        DATE_TRUNC('month', adt.transaction_timestamp::date)                AS trxn_month,
        CASE
            WHEN d.reason IS NULL                                           THEN 'not_disputed'
            WHEN d.reason IN ('unauthorized_external_transfer','unauthorized_transaction',
                              'unauthorized_advance','unauthorized_transfer') THEN 'UT'
            WHEN d.reason IN ('non_receipt_of_goods_or_services',
                              'goods_services_not_as_described',
                              'merchandise_was_returned')                   THEN 'Non-reg'
            ELSE 'EA'
        END                                                                 AS dispute_reason_type,
        SUM(CASE WHEN d.unique_transaction_id IS NOT NULL
                      AND DATEDIFF('day', adt.transaction_timestamp, d.dispute_created_at) <= 7
                      AND DATEDIFF('day', adt.transaction_timestamp, CURRENT_DATE()) >= 7
                 THEN -adt.transaction_amount ELSE 0 END)                   AS dispute_amt_7d,
        SUM(CASE WHEN DATEDIFF('day', adt.transaction_timestamp, CURRENT_DATE()) >= 7
                 THEN -adt.transaction_amount ELSE 0 END)                   AS seasoned_amt_7d
    FROM risk.prod.all_disputable_transactions adt
    LEFT JOIN deduped_disputes d
        ON d.unique_transaction_id = adt.unique_transaction_id
       AND d.dispute_created_at::date = adt.dispute_created_at::date
    WHERE DATE_TRUNC('month', adt.transaction_timestamp::date)
              >= DATEADD('month', -13, DATE_TRUNC('month', CURRENT_DATE()))
      AND DATE_TRUNC('month', adt.transaction_timestamp::date)
              <  DATE_TRUNC('month', CURRENT_DATE())
      AND CASE
            WHEN adt.transaction_code IN ('ISA','ISC','ISJ','ISL','ISM','ISR','ISZ','VSA','VSC','VSJ',
                                          'VSL','VSM','VSR','VSZ','SDA','SDC','SDL','SDM','SDR','SDV',
                                          'SDZ','PLM','PLA','PRA','SSA','SSC','SSZ','SSL','SSM')
                 AND adt.program_type = 'credit'                            THEN 'Credit Purchase'
            WHEN adt.transaction_code IN ('ISA','ISC','ISJ','ISL','ISM','ISR','ISZ','VSA','VSC','VSJ',
                                          'VSL','VSM','VSR','VSZ','SDA','SDC','SDL','SDM','SDR','SDV',
                                          'SDZ','PLM','PLA','PRA','SSA','SSC','SSZ','SSL','SSM')
                                                                            THEN 'Debit Purchase'
            WHEN adt.transaction_code = 'ADS'                               THEN 'ACH Transfer'
            WHEN adt.transaction_code = 'ADbz'                              THEN 'Instant Transfer'
            WHEN adt.transaction_code IN ('ADbc','ADcn')                    THEN 'ACH Debit'
            WHEN adt.transaction_code IN ('ADM','ADPF','ADTS','ADTU','ADpb') THEN 'PF Outgoing'
            WHEN adt.transaction_code IN ('VSW','MPW','MPM','MPR','PLW','PLJ',
                                          'PLR','PRW','SDW','FE0012','FE0013','FE0014')
                                                                            THEN 'ATM Withdrawals'
            ELSE 'Other'
          END != 'Other'
    GROUP BY 1, 2
),
totals AS (
    SELECT trxn_month, SUM(seasoned_amt_7d) AS total_seas_7d
    FROM base GROUP BY 1
),
rates AS (
    SELECT
        b.trxn_month,
        b.dispute_reason_type,
        b.dispute_amt_7d / NULLIF(t.total_seas_7d, 0) AS dispute_rate_7d
    FROM base b JOIN totals t USING (trxn_month)
)
SELECT
    TO_CHAR(trxn_month, 'YYYY-MM-DD')                                       AS trxn_month,
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'EA'           THEN dispute_rate_7d END), 0) AS ea,
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'Non-reg'      THEN dispute_rate_7d END), 0) AS non_reg,
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'not_disputed' THEN dispute_rate_7d END), 0) AS not_disputed,
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'UT'           THEN dispute_rate_7d END), 0) AS ut,
    SUM(COALESCE(dispute_rate_7d, 0))                                       AS grand_total,
    -- % UT and % EA+NonReg
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'UT' THEN dispute_rate_7d END), 0)
        / NULLIF(SUM(COALESCE(dispute_rate_7d, 0)), 0)                     AS pct_ut,
    (COALESCE(MAX(CASE WHEN dispute_reason_type = 'EA'      THEN dispute_rate_7d END), 0)
     + COALESCE(MAX(CASE WHEN dispute_reason_type = 'Non-reg' THEN dispute_rate_7d END), 0))
        / NULLIF(SUM(COALESCE(dispute_rate_7d, 0)), 0)                     AS pct_ea_nonreg,
    -- EA + NonReg combined (for chart)
    COALESCE(MAX(CASE WHEN dispute_reason_type = 'EA'      THEN dispute_rate_7d END), 0)
     + COALESCE(MAX(CASE WHEN dispute_reason_type = 'Non-reg' THEN dispute_rate_7d END), 0)
                                                                            AS ea_plus_nonreg
FROM rates
GROUP BY trxn_month
ORDER BY trxn_month
