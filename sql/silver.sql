CREATE DATABASE IF NOT EXISTS silver;

-- Patients -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.dim_patient (
    patient_id String,
    birth_date Date,
    sex LowCardinality(String),
    region_code LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY patient_id;

INSERT INTO silver.dim_patient (
    patient_id,
    birth_date,
    sex,
    region_code
)
SELECT
    patient_id,
    argMax(birth_date, (source_date, ingested_at)),
    argMax(sex, (source_date, ingested_at)),
    argMax(region_code, (source_date, ingested_at))
FROM bronze.patient
WHERE sex IN ('M', 'F')
GROUP BY patient_id
HAVING patient_id NOT IN (
    SELECT patient_id
    FROM silver.dim_patient
);

-- Services -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.dim_service (
    service_code LowCardinality(String),
    service_name String
)
ENGINE = MergeTree
ORDER BY service_code;

INSERT INTO silver.dim_service (
    service_code,
    service_name
)
SELECT
    service_code,
    argMax(service_label, (source_date, ingested_at))
FROM bronze.service
GROUP BY service_code
HAVING service_code NOT IN (
    SELECT service_code
    FROM silver.dim_service
);

-- Referentiel CIM-10 ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.dim_cim10 (
    code_cim10 LowCardinality(String),
    libelle String
)
ENGINE = MergeTree
ORDER BY code_cim10;

INSERT INTO silver.dim_cim10 (
    code_cim10,
    libelle
)
SELECT
    code_cim10,
    argMax(libelle, (source_date, ingested_at))
FROM bronze.cim10
GROUP BY code_cim10
HAVING code_cim10 NOT IN (
    SELECT code_cim10
    FROM silver.dim_cim10
);

-- Sejours --------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.fact_sejour (
    stay_id String,
    patient_id String,
    service_code LowCardinality(String),
    admission_ts DateTime,
    discharge_ts Nullable(DateTime),
    admission_mode LowCardinality(String),
    discharge_mode LowCardinality(Nullable(String))
)
ENGINE = MergeTree
ORDER BY stay_id;

INSERT INTO silver.fact_sejour (
    stay_id,
    patient_id,
    service_code,
    admission_ts,
    discharge_ts,
    admission_mode,
    discharge_mode
)
SELECT
    stay_id,
    patient_id,
    service_code,
    admission_ts,
    discharge_ts,
    admission_mode,
    discharge_mode
FROM (
    SELECT
        stay_id,
        argMax(patient_id, (source_date, ingested_at)) AS patient_id,
        argMax(service_code, (source_date, ingested_at)) AS service_code,
        argMax(admission_ts, (source_date, ingested_at)) AS admission_ts,
        argMax(discharge_ts, (source_date, ingested_at)) AS discharge_ts,
        argMax(admission_mode, (source_date, ingested_at)) AS admission_mode,
        argMax(discharge_mode, (source_date, ingested_at)) AS discharge_mode
    FROM bronze.sejour
    GROUP BY stay_id
)
WHERE (discharge_ts IS NULL OR discharge_ts >= admission_ts)
  AND patient_id IN (
      SELECT patient_id
      FROM silver.dim_patient
  )
  AND service_code IN (
      SELECT service_code
      FROM silver.dim_service
  )
  AND stay_id NOT IN (
      SELECT stay_id
      FROM silver.fact_sejour
  );

-- Diagnostics ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS silver.fact_diag (
    stay_id String,
    code_cim10 LowCardinality(String),
    libelle String,
    diagnostic_type Enum8('principal' = 1, 'associe' = 2),
    source_date Date
)
ENGINE = MergeTree
ORDER BY (stay_id, code_cim10, diagnostic_type);

INSERT INTO silver.fact_diag (
    stay_id,
    code_cim10,
    libelle,
    diagnostic_type,
    source_date
)
SELECT
    diagnostic.stay_id,
    diagnostic.code_cim10,
    cim10.libelle,
    diagnostic.diagnostic_type,
    diagnostic.source_date
FROM (
    SELECT
        stay_id,
        code_cim10,
        diagnostic_type,
        argMax(source_date, (source_date, ingested_at)) AS source_date
    FROM bronze.diagnostic
    GROUP BY stay_id, code_cim10, diagnostic_type
) AS diagnostic
INNER JOIN silver.dim_cim10 AS cim10
    ON diagnostic.code_cim10 = cim10.code_cim10
WHERE diagnostic.stay_id IN (
    SELECT stay_id
    FROM silver.fact_sejour
)
  AND (diagnostic.stay_id, diagnostic.code_cim10, diagnostic.diagnostic_type)
      NOT IN (
          SELECT stay_id, code_cim10, diagnostic_type
          FROM silver.fact_diag
      );

-- Monitoring -----------------------------------------------------------------

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

INSERT INTO silver.fact_monitoring (
    stay_id,
    ts,
    heart_rate,
    spo2,
    temp_c,
    source_date
)
SELECT
    stay_id,
    ts,
    argMax(heart_rate, (source_date, ingested_at)),
    argMax(spo2, (source_date, ingested_at)),
    argMax(temp_c, (source_date, ingested_at)),
    argMax(source_date, (source_date, ingested_at))
FROM bronze.monitoring
WHERE heart_rate BETWEEN 20 AND 250
  AND spo2 BETWEEN 50 AND 100
  AND temp_c BETWEEN 30 AND 45
  AND stay_id IN (
      SELECT stay_id
      FROM silver.fact_sejour
  )
  AND (stay_id, ts) NOT IN (
      SELECT stay_id, ts
      FROM silver.fact_monitoring
  )
GROUP BY stay_id, ts;
