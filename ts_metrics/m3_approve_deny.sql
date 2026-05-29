-- Metric 3: Approve/Deny by Resolution Month — ex. de minimis, last 13 mature months
-- Powers: $ pivot table + unit pivot table + approval rate MoM/YoY scalars
--         + line charts for $ and unit approval rate

WITH base AS (
    SELECT
        DATE_TRUNC('month', closed_at::date)                                AS resolution_month,
        DATEDIFF('month', DATE_TRUNC('month', CURRENT_DATE()),
                          DATE_TRUNC('month', closed_at::date))             AS mth_offset,
        -- Dollar amounts
        SUM(CASE WHEN investigation_resolution = 'approve'
                 THEN ABS(transaction_amount) ELSE 0 END)                   AS approve_amt,
        SUM(CASE WHEN investigation_resolution = 'deny'
                 THEN ABS(transaction_amount) ELSE 0 END)                   AS deny_amt,
        SUM(ABS(transaction_amount))                                        AS total_amt,
        -- Unit counts
        COUNT(DISTINCT CASE WHEN investigation_resolution = 'approve'
                            THEN dispute_id END)                            AS approve_cnt,
        COUNT(DISTINCT CASE WHEN investigation_resolution = 'deny'
                            THEN dispute_id END)                            AS deny_cnt,
        COUNT(DISTINCT dispute_id)                                          AS total_cnt
    FROM rest.test.ub_dispute_exception_reporting_base
    WHERE deminimus_policy_outcome = 'none'
      AND investigation_resolution IN ('approve', 'deny')
      AND DATE_TRUNC('month', closed_at::date)
              >= DATEADD('month', -13, DATE_TRUNC('month', CURRENT_DATE()))
      AND DATE_TRUNC('month', closed_at::date)
              <  DATE_TRUNC('month', CURRENT_DATE())
    GROUP BY 1, 2
),
with_rates AS (
    SELECT
        *,
        approve_amt / NULLIF(total_amt, 0)          AS approval_rate_dlr,
        approve_cnt / NULLIF(total_cnt::float, 0)   AS approval_rate_cnt
    FROM base
)
SELECT
    TO_CHAR(resolution_month, 'YYYY-MM-DD')                                 AS resolution_month,
    approve_amt,
    deny_amt,
    total_amt,
    approval_rate_dlr,
    approve_cnt,
    deny_cnt,
    total_cnt,
    approval_rate_cnt,
    -- MoM and YoY for dollar approval rate
    (MAX(CASE WHEN mth_offset = -1  THEN approval_rate_dlr END) OVER ()
     - MAX(CASE WHEN mth_offset = -2  THEN approval_rate_dlr END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN approval_rate_dlr END) OVER (), 0)
                                                                            AS mom_dlr,
    (MAX(CASE WHEN mth_offset = -1  THEN approval_rate_dlr END) OVER ()
     - MAX(CASE WHEN mth_offset = -13 THEN approval_rate_dlr END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN approval_rate_dlr END) OVER (), 0)
                                                                            AS yoy_dlr,
    -- MoM and YoY for unit approval rate
    (MAX(CASE WHEN mth_offset = -1  THEN approval_rate_cnt END) OVER ()
     - MAX(CASE WHEN mth_offset = -2  THEN approval_rate_cnt END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN approval_rate_cnt END) OVER (), 0)
                                                                            AS mom_cnt,
    (MAX(CASE WHEN mth_offset = -1  THEN approval_rate_cnt END) OVER ()
     - MAX(CASE WHEN mth_offset = -13 THEN approval_rate_cnt END) OVER ())
    / NULLIF(MAX(CASE WHEN mth_offset = -1 THEN approval_rate_cnt END) OVER (), 0)
                                                                            AS yoy_cnt
FROM with_rates
ORDER BY resolution_month
