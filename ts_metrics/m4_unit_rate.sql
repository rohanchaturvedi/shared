-- Metric 4: 7d Dispute Unit Rate
-- Powers: full data table + line chart (trxn_month vs dispute_rate_cnt)

WITH base AS (
    SELECT
        DATE_TRUNC('month', transaction_timestamp::date)                    AS trxn_month,
        COUNT(DISTINCT CASE
            WHEN dispute_created_at IS NOT NULL
                 AND DATEDIFF('day', transaction_timestamp, dispute_created_at) <= 7
                 AND DATEDIFF('day', transaction_timestamp, CURRENT_DATE()) >= 7
            THEN user_dispute_claim_id
        END)                                                                AS dispute_7d_cnt,
        COUNT(DISTINCT CASE
            WHEN DATEDIFF('day', transaction_timestamp, CURRENT_DATE()) >= 7
            THEN unique_transaction_id
        END)                                                                AS trxn_cnt,
        SUM(CASE
            WHEN dispute_created_at IS NOT NULL
                 AND DATEDIFF('day', transaction_timestamp, dispute_created_at) <= 7
                 AND DATEDIFF('day', transaction_timestamp, CURRENT_DATE()) >= 7
            THEN -transaction_amount ELSE 0
        END)                                                                AS disputed_amt,
        SUM(CASE
            WHEN DATEDIFF('day', transaction_timestamp, CURRENT_DATE()) >= 7
            THEN -transaction_amount ELSE 0
        END)                                                                AS trxn_amt
    FROM risk.prod.all_disputable_transactions
    WHERE transaction_timestamp::date >= DATEADD('month', -14, CURRENT_DATE())
      AND transaction_code IN (
          'ISA','ISC','ISJ','ISL','ISM','ISR','ISZ','VSA','VSC','VSJ','VSL','VSM','VSR','VSZ',
          'SDA','SDC','SDL','SDM','SDR','SDV','SDZ','PLM','PLA','PRA','SSA','SSC','SSZ','SSL','SSM',
          'ADS','ADbz','ADbc','ADcn','ADM','ADPF','ADTS','ADTU','ADpb','VSW','MPW','MPM','MPR',
          'PLW','PLJ','PLR','PRW','SDW','FE0012','FE0013','FE0014'
      )
    GROUP BY 1
)
SELECT
    TO_CHAR(trxn_month, 'YYYY-MM-DD')                                       AS trxn_month,
    dispute_7d_cnt,
    trxn_cnt,
    disputed_amt,
    trxn_amt,
    disputed_amt   / NULLIF(trxn_amt, 0)                AS dispute_rate_dlr,
    dispute_7d_cnt / NULLIF(trxn_cnt::float, 0)         AS dispute_rate_cnt,
    dispute_7d_cnt / NULLIF(trxn_cnt::float, 0) * 10000 AS dispute_rate_cnt_bps
FROM base
ORDER BY trxn_month DESC
