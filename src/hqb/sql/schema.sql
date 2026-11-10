-- Star schema for the benchmark warehouse (SQLite; ports directly to SQL Server).
-- fact_measure keeps every CMS release so the dashboard can trend and alert on change.

CREATE TABLE IF NOT EXISTS dim_hospital (
    facility_id        TEXT PRIMARY KEY,   -- CMS Certification Number (CCN)
    facility_name      TEXT NOT NULL,
    city               TEXT,
    county             TEXT,
    hospital_type      TEXT,
    ownership          TEXT,
    health_system      TEXT NOT NULL,      -- from config/health_systems.csv, else 'Independent / Other'
    is_focus_system    INTEGER NOT NULL,   -- 1 = HonorHealth
    in_market          INTEGER NOT NULL    -- 1 = Phoenix-metro acute care peer group
);

CREATE TABLE IF NOT EXISTS dim_measure (
    measure_id         TEXT PRIMARY KEY,
    measure_label      TEXT NOT NULL,
    unit               TEXT NOT NULL,      -- stars | pct
    higher_is_better   INTEGER NOT NULL,
    domain             TEXT NOT NULL       -- Overall | Patient Experience | Readmissions
);

CREATE TABLE IF NOT EXISTS dim_release (
    release_id         TEXT PRIMARY KEY,   -- date the pipeline loaded this CMS refresh
    loaded_at          TEXT NOT NULL,
    source             TEXT NOT NULL,      -- cms | snapshot
    dq_pass            INTEGER NOT NULL,
    dq_warnings        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_measure (
    release_id         TEXT NOT NULL REFERENCES dim_release(release_id),
    facility_id        TEXT NOT NULL REFERENCES dim_hospital(facility_id),
    measure_id         TEXT NOT NULL REFERENCES dim_measure(measure_id),
    value              REAL,               -- NULL when CMS suppresses the result
    denominator        REAL,               -- completed surveys or eligible cases
    low_volume_flag    INTEGER NOT NULL DEFAULT 0,
    period_start       TEXT,
    period_end         TEXT,
    PRIMARY KEY (release_id, facility_id, measure_id)
);
