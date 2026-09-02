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
