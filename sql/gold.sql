CREATE DATABASE IF NOT EXISTS gold;

-- DMS par service --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.dms_par_service (
    service_code LowCardinality(String),
    service_name String,
    completed_stays UInt64,
    average_stay_days Float64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY service_code;

TRUNCATE TABLE gold.dms_par_service;

INSERT INTO gold.dms_par_service (
    service_code,
    service_name,
    completed_stays,
    average_stay_days
)
SELECT
    sejour.service_code,
    service.service_name,
    count() AS completed_stays,
    round(
        avg(
            dateDiff(
                'second',
                sejour.admission_ts,
                assumeNotNull(sejour.discharge_ts)
            ) / 86400.0
        ),
        2
    ) AS average_stay_days
FROM silver.fact_sejour AS sejour
INNER JOIN silver.dim_service AS service
    ON sejour.service_code = service.service_code
WHERE sejour.discharge_ts IS NOT NULL
GROUP BY
    sejour.service_code,
    service.service_name;

-- Activite des urgences ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.activite_urgences_par_jour (
    activity_date Date,
    emergency_visits UInt64,
    current_stays UInt64,
    average_stay_hours Float64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY activity_date;

ALTER TABLE gold.activite_urgences_par_jour
    ADD COLUMN IF NOT EXISTS current_stays UInt64 AFTER emergency_visits;

ALTER TABLE gold.activite_urgences_par_jour
    ADD COLUMN IF NOT EXISTS average_stay_hours Float64 AFTER current_stays;

TRUNCATE TABLE gold.activite_urgences_par_jour;

INSERT INTO gold.activite_urgences_par_jour (
    activity_date,
    emergency_visits,
    current_stays,
    average_stay_hours
)
SELECT
    toDate(admission_ts) AS activity_date,
    count() AS emergency_visits,
    countIf(discharge_ts IS NULL) AS current_stays,
    round(
        avg(dateDiff('minute', admission_ts, discharge_ts) / 60.0),
        1
    ) AS average_stay_hours
FROM silver.fact_sejour
WHERE service_code = 'URGENCES'
GROUP BY activity_date;

-- Readmissions a 30 jours ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.taux_readmission_30_jours (
    metric_scope LowCardinality(String),
    eligible_discharges UInt64,
    observed_readmissions UInt64,
    provisional_readmission_rate_percent Float64,
    observation_start_date Date,
    observation_end_date Date,
    observation_days UInt16,
    is_provisional UInt8,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY metric_scope;

TRUNCATE TABLE gold.taux_readmission_30_jours;

INSERT INTO gold.taux_readmission_30_jours (
    metric_scope,
    eligible_discharges,
    observed_readmissions,
    provisional_readmission_rate_percent,
    observation_start_date,
    observation_end_date,
    observation_days,
    is_provisional
)
WITH readmitted_stays AS (
    SELECT
        current_stay.stay_id
    FROM silver.fact_sejour AS current_stay
    INNER JOIN silver.fact_sejour AS previous_stay
        ON current_stay.patient_id = previous_stay.patient_id
    WHERE current_stay.stay_id != previous_stay.stay_id
      AND previous_stay.discharge_ts IS NOT NULL
      AND current_stay.admission_ts > previous_stay.discharge_ts
      AND current_stay.admission_ts <= addDays(
          assumeNotNull(previous_stay.discharge_ts),
          30
      )
    GROUP BY current_stay.stay_id
),
observation_period AS (
    SELECT
        toDate(min(admission_ts)) AS start_date,
        toDate(max(admission_ts)) AS end_date
    FROM silver.fact_sejour
)
SELECT
    'global' AS metric_scope,
    (SELECT count() FROM silver.fact_sejour) AS eligible_discharges,
    (SELECT count() FROM readmitted_stays) AS observed_readmissions,
    round(
        100.0 * observed_readmissions / eligible_discharges,
        2
    ) AS provisional_readmission_rate_percent,
    observation.start_date,
    observation.end_date,
    toUInt16(dateDiff('day', observation.start_date, observation.end_date) + 1),
    toUInt8(0) AS is_provisional
FROM observation_period AS observation;

-- Alertes des constantes ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.alertes_constantes_par_jour (
    activity_date Date,
    total_readings UInt64,
    alert_readings UInt64,
    heart_rate_alerts UInt64,
    spo2_alerts UInt64,
    temperature_alerts UInt64,
    alert_rate_percent Float64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY activity_date;

TRUNCATE TABLE gold.alertes_constantes_par_jour;

INSERT INTO gold.alertes_constantes_par_jour (
    activity_date,
    total_readings,
    alert_readings,
    heart_rate_alerts,
    spo2_alerts,
    temperature_alerts,
    alert_rate_percent
)
SELECT
    toDate(ts) AS activity_date,
    count() AS total_readings,
    countIf(
        heart_rate < 50
        OR heart_rate > 100
        OR spo2 < 92
        OR temp_c > 38.5
    ) AS alert_readings,
    countIf(heart_rate < 50 OR heart_rate > 100) AS heart_rate_alerts,
    countIf(spo2 < 92) AS spo2_alerts,
    countIf(temp_c > 38.5) AS temperature_alerts,
    round(100.0 * alert_readings / total_readings, 1) AS alert_rate_percent
FROM silver.fact_monitoring
GROUP BY activity_date;

-- Prevalence par pathologie ------------------------------------------------------

CREATE TABLE IF NOT EXISTS gold.prevalence_par_pathologie (
    code_cim10 LowCardinality(String),
    diagnostic_name String,
    cohort_size UInt64,
    prevalence_percent Float64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY code_cim10;

TRUNCATE TABLE gold.prevalence_par_pathologie;

INSERT INTO gold.prevalence_par_pathologie (
    code_cim10,
    diagnostic_name,
    cohort_size,
    prevalence_percent
)
SELECT
    diagnostic.code_cim10,
    any(diagnostic.libelle) AS diagnostic_name,
    uniqExact(diagnostic.patient_id) AS cohort_size,
    round(
        100.0 * cohort_size
        / (SELECT count() FROM silver.dim_patient),
        2
    ) AS prevalence_percent
FROM silver.fact_diag AS diagnostic
GROUP BY diagnostic.code_cim10
HAVING cohort_size >= 5;

-- Distribution des cohortes par age et sexe --------------------------------------

CREATE TABLE IF NOT EXISTS gold.distribution_cohorte_age_sexe (
    code_cim10 LowCardinality(String),
    diagnostic_name String,
    age_group LowCardinality(String),
    age_group_order UInt8,
    sex LowCardinality(String),
    patient_count UInt64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (code_cim10, age_group_order, sex);

TRUNCATE TABLE gold.distribution_cohorte_age_sexe;

INSERT INTO gold.distribution_cohorte_age_sexe (
    code_cim10,
    diagnostic_name,
    age_group,
    age_group_order,
    sex,
    patient_count
)
WITH cohort_patients AS (
    SELECT
        diagnostic.code_cim10 AS code_cim10,
        any(diagnostic.libelle) AS diagnostic_name,
        diagnostic.patient_id AS patient_id,
        any(patient.birth_date) AS birth_date,
        any(patient.sex) AS sex
    FROM silver.fact_diag AS diagnostic
    INNER JOIN silver.dim_patient AS patient
        ON diagnostic.patient_id = patient.patient_id
    WHERE diagnostic.diagnostic_type = 'principal'
    GROUP BY
        diagnostic.code_cim10,
        diagnostic.patient_id
),
aged_patients AS (
    SELECT
        code_cim10,
        diagnostic_name,
        patient_id,
        sex,
        (SELECT toYear(max(admission_ts)) FROM silver.fact_sejour)
            - toYear(birth_date) AS patient_age
    FROM cohort_patients
)
SELECT
    code_cim10,
    diagnostic_name,
    concat(
        toString(intDiv(patient_age, 10) * 10),
        '-',
        toString(intDiv(patient_age, 10) * 10 + 9)
    ) AS age_group,
    toUInt8(intDiv(patient_age, 10)) AS age_group_order,
    sex,
    count() AS patient_count
FROM aged_patients
GROUP BY
    code_cim10,
    diagnostic_name,
    age_group,
    age_group_order,
    sex
HAVING patient_count >= 5;
