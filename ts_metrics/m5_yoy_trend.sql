-- Metric 5: Year-over-Year Monthly Trend of 7d Dispute Rate
-- Powers: multi-line chart (x = txn_month_label, y = dispute_rate_7d, series = txn_year)
-- Includes all months for current year even if not fully matured

WITH base AS (
    SELECT
        DATE_TRUNC('month', txn_mth)                                        AS txn_month,
        EXTRACT(YEAR  FROM DATE_TRUNC('month', txn_mth))                    AS txn_year,
        EXTRACT(MONTH FROM DATE_TRUNC('month', txn_mth))                    AS txn_month_num,
        TO_CHAR(DATE_TRUNC('month', txn_mth), 'Mon')                        AS txn_month_label,
        SUM(total_dispute_dollars_filed_07d)                                AS disp_7d,
        SUM(seasoned_txn_amt_7d)                                            AS seas_7d
    FROM risk.prod.all_disputable_transactions_summary_monthly
    WHERE txn_mth >= DATE_TRUNC('year', DATEADD('year', -4, CURRENT_DATE()))
      AND txn_mth <  DATEADD('year', 1, DATE_TRUNC('year', CURRENT_DATE()))
    GROUP BY 1, 2, 3, 4
)
SELECT
    txn_year,
    txn_month_num,
    txn_month_label,
    disp_7d / NULLIF(seas_7d, 0) * 10000                                   AS dispute_rate_7d
FROM base
ORDER BY txn_year, txn_month_num
