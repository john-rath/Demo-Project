-- Sensing Hospital mock database (sensing-postgres).
-- Backs patient-context-service (reads) and clinical-alerts-service
-- (reads/writes). Intentionally small so it's laptop-light; the shape maps
-- 1:1 to what an RDS instance would hold in the AWS/k8s target.

CREATE TABLE IF NOT EXISTS beds (
    bed_id        TEXT PRIMARY KEY,
    floor         TEXT NOT NULL,
    wing          TEXT NOT NULL,
    department    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id    TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    bed_id        TEXT REFERENCES beds(bed_id),
    acuity        TEXT NOT NULL DEFAULT 'stable'
);

-- One row per device->patient binding so patient-context-service can resolve
-- an incoming device_id to a patient + bed.
CREATE TABLE IF NOT EXISTS device_bindings (
    device_id     TEXT PRIMARY KEY,
    patient_id    TEXT REFERENCES patients(patient_id),
    bound_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id      BIGSERIAL PRIMARY KEY,
    patient_id    TEXT,
    bed_id        TEXT,
    severity      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts (created_at DESC);

-- Seed a small MedSurg Floor-3-East population matching the overlay footprint.
INSERT INTO beds (bed_id, floor, wing, department) VALUES
    ('MedSurg-301', '3', 'East', 'MedSurg'),
    ('MedSurg-302', '3', 'East', 'MedSurg'),
    ('MedSurg-303', '3', 'East', 'MedSurg'),
    ('MedSurg-304', '3', 'East', 'MedSurg'),
    ('MedSurg-305', '3', 'East', 'MedSurg')
ON CONFLICT (bed_id) DO NOTHING;

INSERT INTO patients (patient_id, display_name, bed_id, acuity) VALUES
    ('pat-1001', 'Patient A', 'MedSurg-301', 'stable'),
    ('pat-1002', 'Patient B', 'MedSurg-302', 'guarded'),
    ('pat-1003', 'Patient C', 'MedSurg-303', 'stable'),
    ('pat-1004', 'Patient D', 'MedSurg-304', 'critical'),
    ('pat-1005', 'Patient E', 'MedSurg-305', 'stable')
ON CONFLICT (patient_id) DO NOTHING;

INSERT INTO device_bindings (device_id, patient_id) VALUES
    ('rtls_badge-000', 'pat-1001'),
    ('rtls_badge-001', 'pat-1002'),
    ('rtls_badge-002', 'pat-1003'),
    ('rtls_badge-003', 'pat-1004'),
    ('rtls_badge-004', 'pat-1005')
ON CONFLICT (device_id) DO NOTHING;
