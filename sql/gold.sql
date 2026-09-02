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
