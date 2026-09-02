CREATE DATABASE IF NOT EXISTS gold;

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

CREATE TABLE IF NOT EXISTS gold.activite_urgences_par_jour (
    activity_date Date,
    emergency_visits UInt64,
    calculated_at DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY activity_date;

TRUNCATE TABLE gold.activite_urgences_par_jour;

INSERT INTO gold.activite_urgences_par_jour (
    activity_date,
    emergency_visits
)
SELECT
    toDate(admission_ts) AS activity_date,
    count() AS emergency_visits
FROM silver.fact_sejour
WHERE admission_mode = 'urgence'
GROUP BY activity_date;

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
WITH eligible_stays AS (
    SELECT
        stay_id,
        patient_id,
        discharge_ts
    FROM silver.fact_sejour
    WHERE discharge_ts IS NOT NULL
      AND ifNull(discharge_mode, '') != 'deces'
),
readmission_candidates AS (
    SELECT
        initial_stay.stay_id,
        initial_stay.discharge_ts,
        minIf(
            next_stay.admission_ts,
            next_stay.admission_ts > assumeNotNull(initial_stay.discharge_ts)
        ) AS next_admission_ts
    FROM eligible_stays AS initial_stay
    LEFT JOIN silver.fact_sejour AS next_stay
        ON initial_stay.patient_id = next_stay.patient_id
    GROUP BY
        initial_stay.stay_id,
        initial_stay.discharge_ts
),
observation_period AS (
    SELECT
        toDate(min(admission_ts)) AS start_date,
        toDate(max(admission_ts)) AS end_date
    FROM silver.fact_sejour
)
SELECT
    'global' AS metric_scope,
    count() AS eligible_discharges,
    countIf(
        next_admission_ts > assumeNotNull(discharge_ts)
        AND next_admission_ts <= addDays(assumeNotNull(discharge_ts), 30)
    ) AS observed_readmissions,
    round(
        100.0 * observed_readmissions / eligible_discharges,
        2
    ) AS provisional_readmission_rate_percent,
    observation.start_date,
    observation.end_date,
    toUInt16(dateDiff('day', observation.start_date, observation.end_date) + 1),
    toUInt8(1) AS is_provisional
FROM readmission_candidates
CROSS JOIN observation_period AS observation
GROUP BY
    observation.start_date,
    observation.end_date;

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
        OR heart_rate > 120
        OR spo2 < 92
        OR temp_c < 36
        OR temp_c > 38.5
    ) AS alert_readings,
    countIf(heart_rate < 50 OR heart_rate > 120) AS heart_rate_alerts,
    countIf(spo2 < 92) AS spo2_alerts,
    countIf(temp_c < 36 OR temp_c > 38.5) AS temperature_alerts,
    round(100.0 * alert_readings / total_readings, 2) AS alert_rate_percent
FROM silver.fact_monitoring
GROUP BY activity_date;

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
    uniqExact(sejour.patient_id) AS cohort_size,
    round(
        100.0 * cohort_size
        / (SELECT uniqExact(patient_id) FROM silver.fact_sejour),
        2
    ) AS prevalence_percent
FROM silver.fact_diag AS diagnostic
INNER JOIN silver.fact_sejour AS sejour
    ON diagnostic.stay_id = sejour.stay_id
GROUP BY diagnostic.code_cim10
HAVING cohort_size >= 5;

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
        sejour.patient_id AS patient_id,
        min(sejour.admission_ts) AS first_admission_ts,
        any(patient.birth_date) AS birth_date,
        any(patient.sex) AS sex
    FROM silver.fact_diag AS diagnostic
    INNER JOIN silver.fact_sejour AS sejour
        ON diagnostic.stay_id = sejour.stay_id
    INNER JOIN silver.dim_patient AS patient
        ON sejour.patient_id = patient.patient_id
    GROUP BY
        diagnostic.code_cim10,
        sejour.patient_id
),
aged_patients AS (
    SELECT
        code_cim10,
        diagnostic_name,
        patient_id,
        sex,
        age(
            'year',
            birth_date,
            toDate(first_admission_ts)
        ) AS patient_age
    FROM cohort_patients
)
SELECT
    code_cim10,
    diagnostic_name,
    multiIf(
        patient_age < 18, '0-17',
        patient_age < 40, '18-39',
        patient_age < 65, '40-64',
        patient_age < 80, '65-79',
        '80+'
    ) AS age_group,
    multiIf(
        patient_age < 18, 1,
        patient_age < 40, 2,
        patient_age < 65, 3,
        patient_age < 80, 4,
        5
    ) AS age_group_order,
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
