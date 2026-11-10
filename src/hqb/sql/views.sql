-- Reporting views. The Power BI model and the automated digest both read from these,
-- so the business logic lives in one place.

DROP VIEW IF EXISTS vw_latest_release;
CREATE VIEW vw_latest_release AS
SELECT release_id FROM dim_release WHERE dq_pass = 1 ORDER BY release_id DESC LIMIT 1;

-- Market peer-group values for the latest good release, with each hospital's rank.
-- Rank 1 = best, respecting measure direction (lower readmission rate is better).
DROP VIEW IF EXISTS vw_market_rank;
CREATE VIEW vw_market_rank AS
WITH latest AS (
    SELECT f.facility_id, f.measure_id, f.value, f.denominator, f.low_volume_flag,
           h.facility_name, h.health_system, h.is_focus_system,
           m.measure_label, m.unit, m.higher_is_better, m.domain
    FROM fact_measure f
    JOIN dim_hospital h ON h.facility_id = f.facility_id
    JOIN dim_measure  m ON m.measure_id  = f.measure_id
    WHERE f.release_id = (SELECT release_id FROM vw_latest_release)
      AND h.in_market = 1
      AND f.value IS NOT NULL
)
SELECT *,
       RANK() OVER (PARTITION BY measure_id
                    ORDER BY CASE WHEN higher_is_better = 1 THEN -value ELSE value END) AS market_rank,
       COUNT(*) OVER (PARTITION BY measure_id)                                         AS market_n,
       ROUND(AVG(value) OVER (PARTITION BY measure_id), 2)                             AS market_avg
FROM latest;

-- System scorecard: average of each system's reporting hospitals vs the market.
DROP VIEW IF EXISTS vw_system_scorecard;
CREATE VIEW vw_system_scorecard AS
SELECT health_system,
       measure_id,
       measure_label,
       unit,
       higher_is_better,
       COUNT(*)                 AS hospitals_reporting,
       ROUND(AVG(value), 2)     AS system_avg,
       MAX(market_avg)          AS market_avg,
       ROUND(AVG(value) - MAX(market_avg), 2) AS gap_vs_market
FROM vw_market_rank
GROUP BY health_system, measure_id, measure_label, unit, higher_is_better;

-- Release-over-release change, for automated alerting.
DROP VIEW IF EXISTS vw_release_change;
CREATE VIEW vw_release_change AS
WITH ordered AS (
    SELECT f.*, LAG(f.value) OVER (PARTITION BY f.facility_id, f.measure_id ORDER BY f.release_id) AS prior_value
    FROM fact_measure f
    JOIN dim_release r ON r.release_id = f.release_id AND r.dq_pass = 1
)
SELECT o.release_id, o.facility_id, h.facility_name, h.health_system, o.measure_id,
       o.prior_value, o.value, ROUND(o.value - o.prior_value, 2) AS change
FROM ordered o
JOIN dim_hospital h ON h.facility_id = o.facility_id
WHERE o.release_id = (SELECT release_id FROM vw_latest_release)
  AND o.prior_value IS NOT NULL AND o.value IS NOT NULL AND o.value <> o.prior_value;
