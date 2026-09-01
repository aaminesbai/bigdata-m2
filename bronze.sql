CREATE DATABASE IF NOT EXISTS bronze;

-- Patients -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.patient (
    patient_id String,
    birth_date Date,
    sex LowCardinality(String),
    region_code LowCardinality(String),
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, patient_id);

INSERT INTO bronze.patient (
    patient_id,
    birth_date,
    sex,
    region_code,
    source_date,
    source_file
)
SELECT
    patient_id,
    birth_date,
    sex,
    region_code,
    toDate(extract(_path, 'patients/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/patients/*/patients.csv',
    'CSVWithNames',
    'patient_id String, birth_date Date, sex String, region_code String'
)
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.patient
);

-- Sejours --------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.sejour (
    stay_id String,
    patient_id String,
    service_code LowCardinality(String),
    admission_ts DateTime,
    discharge_ts Nullable(DateTime),
    admission_mode LowCardinality(String),
    discharge_mode LowCardinality(Nullable(String)),
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, stay_id);

-- Compatibility with the first version of bronze.sejour.
ALTER TABLE bronze.sejour
    ADD COLUMN IF NOT EXISTS source_date Date;

ALTER TABLE bronze.sejour
    ADD COLUMN IF NOT EXISTS source_file LowCardinality(String);

ALTER TABLE bronze.sejour
    ADD COLUMN IF NOT EXISTS ingested_at DateTime64(3) DEFAULT now64(3);

INSERT INTO bronze.sejour (
    stay_id,
    patient_id,
    service_code,
    admission_ts,
    discharge_ts,
    admission_mode,
    discharge_mode,
    source_date,
    source_file
)
SELECT
    stay_id,
    patient_id,
    service_code,
    admission_ts,
    discharge_ts,
    admission_mode,
    discharge_mode,
    toDate(extract(_path, 'sejours/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/sejours/*/sejours.csv',
    'CSVWithNames',
    'stay_id String, patient_id String, service_code String, admission_ts DateTime, discharge_ts Nullable(DateTime), admission_mode String, discharge_mode Nullable(String)'
)
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.sejour
);

-- Diagnostics ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.diagnostic (
    stay_id String,
    code_cim10 LowCardinality(String),
    diagnostic_type Enum8('principal' = 1, 'associe' = 2),
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, stay_id, diagnostic_type, code_cim10);

INSERT INTO bronze.diagnostic (
    stay_id,
    code_cim10,
    diagnostic_type,
    source_date,
    source_file
)
SELECT
    stay_id,
    diagnostic.code_cim10,
    CAST(diagnostic.type AS Enum8('principal' = 1, 'associe' = 2)),
    toDate(extract(_path, 'diagnostics/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/diagnostics/*/diagnostics.json',
    'JSONEachRow',
    'stay_id String, diagnostics Array(Tuple(code_cim10 String, type String))'
)
ARRAY JOIN diagnostics AS diagnostic
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.diagnostic
);

-- Monitoring -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.monitoring (
    stay_id String,
    ts DateTime64(6, 'UTC'),
    heart_rate Int16,
    spo2 Int16,
    temp_c Float32,
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (stay_id, ts);

INSERT INTO bronze.monitoring (
    stay_id,
    ts,
    heart_rate,
    spo2,
    temp_c,
    source_date,
    source_file
)
SELECT
    assumeNotNull(stay_id),
    assumeNotNull(ts),
    toInt16(assumeNotNull(heart_rate)),
    toInt16(assumeNotNull(spo2)),
    toFloat32(assumeNotNull(temp_c)),
    toDate(extract(_path, 'monitoring/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/monitoring/*/monitoring.parquet',
    'Parquet'
)
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.monitoring
);

-- Referentiel des services ---------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.service (
    service_code LowCardinality(String),
    service_label String,
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, service_code);

INSERT INTO bronze.service (
    service_code,
    service_label,
    source_date,
    source_file
)
SELECT
    service_code,
    service_label,
    toDate(extract(_path, 'referentiels/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/referentiels/*/services.csv',
    'CSVWithNames',
    'service_code String, service_label String'
)
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.service
);

-- Referentiel CIM-10 ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS bronze.cim10 (
    code_cim10 LowCardinality(String),
    libelle String,
    source_date Date,
    source_file LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (source_date, code_cim10);

INSERT INTO bronze.cim10 (
    code_cim10,
    libelle,
    source_date,
    source_file
)
SELECT
    code_cim10,
    libelle,
    toDate(extract(_path, 'referentiels/([0-9]{4}-[0-9]{2}-[0-9]{2})/')),
    _path
FROM file(
    'lake/referentiels/*/cim10.csv',
    'CSVWithNames',
    'code_cim10 String, libelle String'
)
WHERE _path NOT IN (
    SELECT DISTINCT source_file
    FROM bronze.cim10
);
