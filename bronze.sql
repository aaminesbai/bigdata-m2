CREATE DATABASE IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.sejour (
    stay_id VARCHAR(255) PRIMARY KEY,
    patient_id VARCHAR(255),
    admission_ts DATE,
    discharge_ts DATE,
    admission_mode VARCHAR(255),
    admission_type VARCHAR(255)
);

