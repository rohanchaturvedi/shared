-- Metric 1: 7d Dispute Rate by $ — all seasoning windows
-- Powers: table (all columns) + line chart (txn_month vs dispute_rate_7d)

WITH base AS (
    SELECT
        DATE_TRUNC('month', txn_mth)                                        AS txn_month,
        DATEDIFF('month', DATE_TRUNC('month', CURRENT_DATE()),
                          DATE_TRUNC('month', txn_mth))                     AS mth_offset,
        SUM(total_dispute_dollars_filed_07d)                                AS disp_7d,
        SUM(seasoned_txn_amt_7d)                                            AS seas_7d,
        SUM(total_dispute_dollars_filed_14d)                                AS disp_14d,
        SUM(seasoned_txn_amt_14d)                                           AS seas_14d,
        SUM(total_dispute_dollars_filed_30d)                                AS disp_30d,
        SUM(seasoned_txn_amt_30d)                                           AS seas_30d,
        SUM(total_dispute_dollars_filed_45d)                                AS disp_45d,
        SUM(seasoned_txn_amt_45d)                                           AS seas_45d,
        SUM(total_dispute_dollars_filed_60d)                                AS disp_60d,
        SUM(seasoned_txn_amt_60d)                                           AS seas_60d,
        SUM(total_dispute_dollars_filed_90d)                                AS disp_90d,
        SUM(seasoned_txn_amt_90d)                                           AS seas_90d,
        SUM(total_dispute_dollars_filed_120d)                               AS disp_120d,
        SUM(seasoned_txn_amt_120d)                                          AS seas_120d,
        SUM(total_dispute_dollars_filed_150d)                               AS disp_150d,
        SUM(seasoned_txn_amt_150d)                                          AS seas_150d,
        SUM(total_dispute_dollars_filed_180d)                               AS disp_180d,
        SUM(seasoned_txn_amt_180d)                                          AS seas_180d
    FROM risk.prod.all_disputable_transactions_summary_monthly
    WHERE txn_mth >= DATEADD('month', -13, DATE_TRUNC('month', CURRENT_DATE()))
    GROUP BY 1, 2
)
SELECT
    TO_CHAR(txn_month, 'YYYY-MM')                                           AS txn_month,
    mth_offset,
    disp_7d   / NULLIF(seas_7d,   0) * 10000                               AS dispute_rate_7d,
    disp_14d  / NULLIF(seas_14d,  0) * 10000                               AS dispute_rate_14d,
    CASE WHEN mth_offset < -1 THEN disp_30d  / NULLIF(seas_30d,  0) * 10000 END AS dispute_rate_30d,
    CASE WHEN mth_offset < -2 THEN disp_45d  / NULLIF(seas_45d,  0) * 10000 END AS dispute_rate_45d,
    CASE WHEN mth_offset < -2 THEN disp_60d  / NULLIF(seas_60d,  0) * 10000 END AS dispute_rate_60d,
    CASE WHEN mth_offset < -3 THEN disp_90d  / NULLIF(seas_90d,  0) * 10000 END AS dispute_rate_90d,
    CASE WHEN mth_offset < -4 THEN disp_120d / NULLIF(seas_120d, 0) * 10000 END AS dispute_rate_120d,
    CASE WHEN mth_offset < -5 THEN disp_150d / NULLIF(seas_150d, 0) * 10000 END AS dispute_rate_150d,
    CASE WHEN mth_offset < -5 THEN disp_180d / NULLIF(seas_180d, 0) * 10000 END AS dispute_rate_180d,
    -- YoY and MoM scalars (same value on every row for easy display in Hex)
    (MAX(CASE WHEN mth_offset = -1  THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER ()
     - MAX(CASE WHEN mth_offset = -13 THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER (), 0)
                                                                            AS yoy_7d,
    (MAX(CASE WHEN mth_offset = -1  THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER ()
     - MAX(CASE WHEN mth_offset = -2  THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN disp_7d / NULLIF(seas_7d, 0) * 10000 END) OVER (), 0)
                                                                            AS mom_7d
FROM base
ORDER BY txn_month
