CREATE DATABASE IF NOT EXISTS silver;

CREATE TABLE IF NOT EXISTS silver.dim_patient (
    patient_id String,
    birth_date Date,
    sex LowCardinality(String),
    region_code LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY patient_id;

CREATE TABLE IF NOT EXISTS silver.dim_service (
    service_code LowCardinality(String),
    service_name String
)
ENGINE = MergeTree
ORDER BY service_code;

CREATE TABLE IF NOT EXISTS silver.fact_sejour (
    stay_id String,
    patient_id String,
    service_code LowCardinality(String),
    admission_ts DateTime,
    discharge_ts Nullable(DateTime)
)
ENGINE = MergeTree
ORDER BY stay_id;

CREATE TABLE IF NOT EXISTS silver.fact_monitoring (
    stay_id String,
    ts DateTime64(6, 'UTC'),
    heart_rate Int16,
    spo2 Int16,
    temp_c Float32,
    source_date Date
)
ENGINE = MergeTree
ORDER BY (stay_id, ts);
