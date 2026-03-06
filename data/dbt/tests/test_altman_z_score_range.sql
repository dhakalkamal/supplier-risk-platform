-- Custom test: Altman Z' scores must fall within [-10, 20].
--
-- Values outside this range indicate a data error in the SEC parser —
-- either an extreme outlier in the raw financials or a calculation bug.
-- The test passes when this query returns zero rows.

select
    cik,
    period_end,
    altman_z_score
from {{ ref('stg_sec_financials') }}
where altman_z_score is not null
  and (altman_z_score < -10 or altman_z_score > 20)
