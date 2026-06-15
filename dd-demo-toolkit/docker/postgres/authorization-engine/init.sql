-- Authorization Engine database schema
-- Loaded by postgres:16-alpine on first start via /docker-entrypoint-initdb.d/
--
-- Schemas:
--   public       — finance vertical (auth_transactions, fraud_scores, card_limits)
--   healthcare   — hospital patient/medication/device tables
--   hospitality  — hotel guest/revenue/IoT tables
--   insurance    — policy/claims/payment tables
--   manufacturing — production/telemetry/maintenance tables
--   datadog      — DBM explain plan helper (all verticals)

-- Required for DBM query fingerprinting (samples + explain plans)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- =============================================================================
-- Finance schema (public) — authorization engine
-- =============================================================================

CREATE TABLE auth_transactions (
    id               BIGSERIAL PRIMARY KEY,
    card_token       VARCHAR(64)   NOT NULL,
    merchant_id      VARCHAR(32)   NOT NULL,
    amount_cents     INTEGER       NOT NULL,
    region           VARCHAR(32)   NOT NULL,
    status           VARCHAR(16)   NOT NULL,  -- APPROVED / DECLINED / TIMEOUT
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_auth_tx_card_token   ON auth_transactions(card_token);
CREATE INDEX idx_auth_tx_created_at   ON auth_transactions(created_at DESC);
-- Intentionally NO index on merchant_id — full-scan queries against this
-- column are used by the db-worker during cascade to generate slow-query signal.

CREATE TABLE fraud_scores (
    id               BIGSERIAL PRIMARY KEY,
    card_token       VARCHAR(64)   NOT NULL,
    score            NUMERIC(5,2)  NOT NULL,
    risk_level       VARCHAR(16)   NOT NULL,  -- LOW / MEDIUM / HIGH / CRITICAL
    evaluated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_fraud_card_token ON fraud_scores(card_token);

CREATE TABLE card_limits (
    card_token              VARCHAR(64)  PRIMARY KEY,
    daily_limit_cents       INTEGER      NOT NULL DEFAULT 500000,
    current_day_total_cents INTEGER      NOT NULL DEFAULT 0,
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO card_limits (card_token, daily_limit_cents, current_day_total_cents)
SELECT
    'card_' || lpad(i::text, 6, '0'),
    500000,
    (random() * 100000)::int
FROM generate_series(1, 500) AS g(i);

-- =============================================================================
-- Healthcare schema — Smart Hospital
-- =============================================================================

CREATE SCHEMA healthcare;

CREATE TABLE healthcare.patient_encounters (
    encounter_id    BIGSERIAL    PRIMARY KEY,
    patient_mrn     VARCHAR(20)  NOT NULL,
    bed_id          VARCHAR(20)  NOT NULL,
    admission_type  VARCHAR(16)  NOT NULL,  -- EMERGENCY / SCHEDULED / TRANSFER
    department      VARCHAR(32)  NOT NULL,  -- ICU / ED / OR / MedSurg / Pharmacy
    admitted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pe_mrn        ON healthcare.patient_encounters(patient_mrn);
CREATE INDEX idx_pe_bed_id     ON healthcare.patient_encounters(bed_id);
-- Intentionally NO index on department — full-scan queries against this
-- column are used during cascade to generate slow-query signal in DBM.

CREATE TABLE healthcare.medication_orders (
    order_id        BIGSERIAL    PRIMARY KEY,
    encounter_id    BIGINT       NOT NULL REFERENCES healthcare.patient_encounters,
    drug_code       VARCHAR(16)  NOT NULL,
    route           VARCHAR(8)   NOT NULL,  -- IV / PO / IM
    status          VARCHAR(16)  NOT NULL,  -- PENDING / VERIFIED / DISPENSED / ADMINISTERED
    ordered_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_mo_encounter  ON healthcare.medication_orders(encounter_id);
CREATE INDEX idx_mo_status     ON healthcare.medication_orders(status);

CREATE TABLE healthcare.device_alerts (
    alert_id        BIGSERIAL    PRIMARY KEY,
    device_id       VARCHAR(32)  NOT NULL,
    device_type     VARCHAR(32)  NOT NULL,  -- infusion_pump / patient_monitor / ventilator
    bed_id          VARCHAR(20)  NOT NULL,
    alert_code      VARCHAR(16)  NOT NULL,  -- OCCLUSION / OFFLINE / LOW_BATTERY / SIGNAL_LOSS
    severity        VARCHAR(16)  NOT NULL,  -- INFO / WARNING / CRITICAL
    detected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_da_device     ON healthcare.device_alerts(device_id);
CREATE INDEX idx_da_detected   ON healthcare.device_alerts(detected_at DESC);

CREATE TABLE healthcare.infusion_sessions (
    session_id        BIGSERIAL    PRIMARY KEY,
    order_id          BIGINT       NOT NULL REFERENCES healthcare.medication_orders,
    pump_device_id    VARCHAR(32)  NOT NULL,
    flow_rate_ml_hr   NUMERIC(6,2) NOT NULL,
    volume_infused_ml NUMERIC(8,2) NOT NULL DEFAULT 0,
    status            VARCHAR(16)  NOT NULL,  -- ACTIVE / PAUSED / COMPLETED / ERROR
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_is_order_id   ON healthcare.infusion_sessions(order_id);
CREATE INDEX idx_is_status     ON healthcare.infusion_sessions(status);

INSERT INTO healthcare.patient_encounters (patient_mrn, bed_id, admission_type, department, admitted_at)
SELECT
    'MRN' || lpad(i::text, 7, '0'),
    (ARRAY['ICU', 'ED', 'OR', 'MedSurg', 'Pharmacy'])[1 + (i % 5)] || '-F' || (1 + (i % 5)) || '-Bed-' || lpad(((i % 20) + 1)::text, 2, '0'),
    (ARRAY['EMERGENCY', 'SCHEDULED', 'TRANSFER'])[1 + (i % 3)],
    (ARRAY['ICU', 'ED', 'OR', 'MedSurg', 'Pharmacy'])[1 + (i % 5)],
    NOW() - (random() * INTERVAL '30 days')
FROM generate_series(1, 300) AS g(i);

INSERT INTO healthcare.medication_orders (encounter_id, drug_code, route, status, ordered_at)
SELECT
    1 + (i % 300),
    (ARRAY['VANCOMYCIN', 'HEPARIN', 'INSULIN', 'MORPHINE', 'METOPROLOL', 'AMOXICILLIN'])[1 + (i % 6)],
    (ARRAY['IV', 'PO', 'IM'])[1 + (i % 3)],
    (ARRAY['PENDING', 'VERIFIED', 'DISPENSED', 'ADMINISTERED'])[1 + (i % 4)],
    NOW() - (random() * INTERVAL '7 days')
FROM generate_series(1, 600) AS g(i);

INSERT INTO healthcare.infusion_sessions (order_id, pump_device_id, flow_rate_ml_hr, volume_infused_ml, status, started_at)
SELECT
    1 + (i % 600),
    'BD-ALARIS-' || lpad(i::text, 4, '0'),
    (50 + random() * 150)::numeric(6,2),
    (random() * 500)::numeric(8,2),
    (ARRAY['ACTIVE', 'ACTIVE', 'ACTIVE', 'PAUSED', 'COMPLETED'])[1 + (i % 5)],
    NOW() - (random() * INTERVAL '24 hours')
FROM generate_series(1, 250) AS g(i);

INSERT INTO healthcare.device_alerts (device_id, device_type, bed_id, alert_code, severity, detected_at)
SELECT
    'BD-ALARIS-' || lpad(i::text, 4, '0'),
    (ARRAY['infusion_pump', 'patient_monitor', 'ventilator'])[1 + (i % 3)],
    (ARRAY['ICU', 'ED', 'OR', 'MedSurg', 'Pharmacy'])[1 + (i % 5)] || '-F' || (1 + (i % 5)) || '-Bed-' || lpad(((i % 20) + 1)::text, 2, '0'),
    (ARRAY['OCCLUSION', 'OFFLINE', 'LOW_BATTERY', 'SIGNAL_LOSS'])[1 + (i % 4)],
    (ARRAY['INFO', 'WARNING', 'CRITICAL'])[1 + (i % 3)],
    NOW() - (random() * INTERVAL '48 hours')
FROM generate_series(1, 200) AS g(i);

-- =============================================================================
-- Hospitality schema — Smart Hotel Portfolio
-- =============================================================================

CREATE SCHEMA hospitality;

CREATE TABLE hospitality.properties (
    property_id   VARCHAR(16)   PRIMARY KEY,
    brand_type    VARCHAR(32)   NOT NULL,  -- Luxury Collection / Premium Resort / Full Service / etc.
    region        VARCHAR(16)   NOT NULL,  -- Americas / EMEA / APAC
    city          VARCHAR(64)   NOT NULL,
    total_rooms   INT           NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
-- Intentionally NO index on brand_type — full-scan join on this column
-- is used during cascade to generate slow-query signal in DBM.

CREATE TABLE hospitality.guest_reservations (
    reservation_id   BIGSERIAL    PRIMARY KEY,
    property_id      VARCHAR(16)  NOT NULL REFERENCES hospitality.properties,
    guest_email      VARCHAR(128) NOT NULL,
    check_in_date    DATE         NOT NULL,
    check_out_date   DATE         NOT NULL,
    room_number      VARCHAR(8)   NOT NULL,
    nightly_rate_usd NUMERIC(7,2) NOT NULL,
    status           VARCHAR(16)  NOT NULL,  -- CONFIRMED / CHECKED_IN / CHECKED_OUT / CANCELLED
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_gr_property    ON hospitality.guest_reservations(property_id);
CREATE INDEX idx_gr_room        ON hospitality.guest_reservations(property_id, room_number);
-- Intentionally NO index on status — full-scan queries against status during
-- cascade demonstrate connection pool exhaustion from IoT alarm storm inserts.

CREATE TABLE hospitality.room_iot_gateways (
    gateway_id           VARCHAR(32)  PRIMARY KEY,
    property_id          VARCHAR(16)  NOT NULL REFERENCES hospitality.properties,
    room_number          VARCHAR(8)   NOT NULL,
    connected_devices    INT          NOT NULL DEFAULT 0,
    online_status        BOOLEAN      NOT NULL DEFAULT TRUE,
    last_heartbeat       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_gw_property    ON hospitality.room_iot_gateways(property_id);

CREATE TABLE hospitality.guest_service_requests (
    request_id       BIGSERIAL    PRIMARY KEY,
    reservation_id   BIGINT       NOT NULL REFERENCES hospitality.guest_reservations,
    issue_type       VARCHAR(32)  NOT NULL,  -- ROOM_CONTROL / LOCK / THERMOSTAT / ENTERTAINMENT
    severity         VARCHAR(16)  NOT NULL,  -- LOW / MEDIUM / HIGH
    status           VARCHAR(16)  NOT NULL,  -- OPEN / IN_PROGRESS / RESOLVED
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at      TIMESTAMPTZ
);
CREATE INDEX idx_sr_reservation ON hospitality.guest_service_requests(reservation_id);
CREATE INDEX idx_sr_status      ON hospitality.guest_service_requests(status);

CREATE TABLE hospitality.daily_revenue_snapshot (
    snapshot_id        BIGSERIAL    PRIMARY KEY,
    property_id        VARCHAR(16)  NOT NULL REFERENCES hospitality.properties,
    snapshot_date      DATE         NOT NULL,
    revpar_usd         NUMERIC(7,2) NOT NULL,
    adr_usd            NUMERIC(7,2) NOT NULL,
    occupancy_pct      NUMERIC(5,2) NOT NULL,
    total_revenue_usd  NUMERIC(12,2) NOT NULL,
    calculated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_rev_property   ON hospitality.daily_revenue_snapshot(property_id, snapshot_date DESC);

INSERT INTO hospitality.properties (property_id, brand_type, region, city, total_rooms)
SELECT
    'PROP-' || lpad(i::text, 4, '0'),
    (ARRAY['Luxury Collection', 'Premium Resort', 'Full Service', 'Upscale Select', 'Select Service', 'Extended Stay'])[1 + (i % 6)],
    (ARRAY['Americas', 'EMEA', 'APAC'])[1 + (i % 3)],
    (ARRAY['New York', 'London', 'Tokyo', 'Singapore', 'Dubai', 'Sydney', 'Paris', 'Chicago'])[1 + (i % 8)],
    100 + (i % 400)
FROM generate_series(1, 60) AS g(i);

INSERT INTO hospitality.guest_reservations (property_id, guest_email, check_in_date, check_out_date, room_number, nightly_rate_usd, status)
SELECT
    'PROP-' || lpad(1 + (i % 60)::text, 4, '0'),
    'guest' || i || '@example.com',
    CURRENT_DATE - (i % 14),
    CURRENT_DATE + (1 + i % 7),
    lpad(((i % 400) + 100)::text, 3, '0'),
    (89 + random() * 800)::numeric(7,2),
    (ARRAY['CONFIRMED', 'CONFIRMED', 'CHECKED_IN', 'CHECKED_IN', 'CHECKED_OUT', 'CANCELLED'])[1 + (i % 6)]
FROM generate_series(1, 600) AS g(i);

INSERT INTO hospitality.room_iot_gateways (gateway_id, property_id, room_number, connected_devices, online_status, last_heartbeat)
SELECT
    'GW-' || lpad(i::text, 6, '0'),
    'PROP-' || lpad(1 + (i % 60)::text, 4, '0'),
    lpad(((i % 400) + 100)::text, 3, '0'),
    8 + (i % 6),
    (i % 10 != 0),
    NOW() - (random() * INTERVAL '5 minutes')
FROM generate_series(1, 400) AS g(i);

INSERT INTO hospitality.guest_service_requests (reservation_id, issue_type, severity, status, created_at)
SELECT
    1 + (i % 600),
    (ARRAY['ROOM_CONTROL', 'LOCK', 'THERMOSTAT', 'ENTERTAINMENT'])[1 + (i % 4)],
    (ARRAY['LOW', 'MEDIUM', 'HIGH'])[1 + (i % 3)],
    (ARRAY['OPEN', 'IN_PROGRESS', 'RESOLVED', 'RESOLVED'])[1 + (i % 4)],
    NOW() - (random() * INTERVAL '7 days')
FROM generate_series(1, 300) AS g(i);

INSERT INTO hospitality.daily_revenue_snapshot (property_id, snapshot_date, revpar_usd, adr_usd, occupancy_pct, total_revenue_usd, calculated_at)
SELECT
    'PROP-' || lpad(1 + (i % 60)::text, 4, '0'),
    CURRENT_DATE - (i / 60),
    (60 + random() * 200)::numeric(7,2),
    (89 + random() * 300)::numeric(7,2),
    (55 + random() * 40)::numeric(5,2),
    (10000 + random() * 90000)::numeric(12,2),
    NOW() - ((i / 60) || ' days')::interval
FROM generate_series(0, 299) AS g(i);

-- =============================================================================
-- Insurance schema — Property & Casualty Claims
-- =============================================================================

CREATE SCHEMA insurance;

CREATE TABLE insurance.policies (
    policy_id           VARCHAR(20)  PRIMARY KEY,
    policyholder_name   VARCHAR(128) NOT NULL,
    line_of_business    VARCHAR(16)  NOT NULL,  -- Auto / Home / Life / Commercial
    region              VARCHAR(16)  NOT NULL,  -- us-east / us-central / us-west / canada
    premium_annual_usd  NUMERIC(8,2) NOT NULL,
    effective_date      DATE         NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE'  -- ACTIVE / LAPSED / CANCELLED
);
CREATE INDEX idx_pol_status     ON insurance.policies(status);

CREATE TABLE insurance.claims (
    claim_id             BIGSERIAL    PRIMARY KEY,
    policy_id            VARCHAR(20)  NOT NULL REFERENCES insurance.policies,
    loss_date            DATE         NOT NULL,
    loss_type            VARCHAR(32)  NOT NULL,  -- Wind / Flood / Theft / Collision / Fire
    reported_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    estimated_reserve_usd NUMERIC(10,2),
    paid_amount_usd      NUMERIC(10,2) NOT NULL DEFAULT 0,
    status               VARCHAR(24)  NOT NULL  -- FNOL_RECEIVED / ASSIGNED / UNDER_INVESTIGATION / SETTLED / CLOSED
);
CREATE INDEX idx_cl_policy      ON insurance.claims(policy_id);
CREATE INDEX idx_cl_reported    ON insurance.claims(reported_at DESC);
-- Intentionally NO index on loss_type — full-scan queries during claims
-- surge cascade generate slow-query signal in DBM.

CREATE TABLE insurance.claim_documents (
    doc_id           BIGSERIAL    PRIMARY KEY,
    claim_id         BIGINT       NOT NULL REFERENCES insurance.claims,
    document_type    VARCHAR(32)  NOT NULL,  -- PHOTO / ESTIMATE / POLICE_REPORT / MEDICAL_RECORD
    s3_object_key    VARCHAR(256) NOT NULL,
    ocr_confidence   NUMERIC(5,2),
    uploaded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cd_claim       ON insurance.claim_documents(claim_id);

CREATE TABLE insurance.claims_payments (
    payment_id       BIGSERIAL    PRIMARY KEY,
    claim_id         BIGINT       NOT NULL REFERENCES insurance.claims,
    payee_name       VARCHAR(128) NOT NULL,
    amount_usd       NUMERIC(10,2) NOT NULL,
    payment_method   VARCHAR(16)  NOT NULL,  -- ACH / CHECK / WIRE
    status           VARCHAR(16)  NOT NULL,  -- PENDING / PROCESSED / FAILED
    processed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cp_claim       ON insurance.claims_payments(claim_id);
CREATE INDEX idx_cp_status      ON insurance.claims_payments(status);

CREATE TABLE insurance.reinsurance_share (
    share_id         BIGSERIAL    PRIMARY KEY,
    claim_id         BIGINT       NOT NULL REFERENCES insurance.claims,
    reinsurer_id     VARCHAR(32)  NOT NULL,
    share_pct        NUMERIC(5,2) NOT NULL,
    share_amount_usd NUMERIC(10,2),
    status           VARCHAR(16)  NOT NULL DEFAULT 'SUBMITTED',  -- SUBMITTED / APPROVED / PAID
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ri_claim       ON insurance.reinsurance_share(claim_id);

INSERT INTO insurance.policies (policy_id, policyholder_name, line_of_business, region, premium_annual_usd, effective_date, status)
SELECT
    'POL-' || lpad(i::text, 8, '0'),
    'Policyholder ' || i,
    (ARRAY['Auto', 'Home', 'Life', 'Commercial'])[1 + (i % 4)],
    (ARRAY['us-east', 'us-central', 'us-west', 'canada'])[1 + (i % 4)],
    (500 + random() * 4500)::numeric(8,2),
    CURRENT_DATE - (random() * 1825)::int,
    (ARRAY['ACTIVE', 'ACTIVE', 'ACTIVE', 'ACTIVE', 'LAPSED', 'CANCELLED'])[1 + (i % 6)]
FROM generate_series(1, 500) AS g(i);

INSERT INTO insurance.claims (policy_id, loss_date, loss_type, reported_at, estimated_reserve_usd, paid_amount_usd, status)
SELECT
    'POL-' || lpad(1 + (i % 500)::text, 8, '0'),
    CURRENT_DATE - (random() * 365)::int,
    (ARRAY['Wind', 'Flood', 'Theft', 'Collision', 'Fire'])[1 + (i % 5)],
    NOW() - (random() * INTERVAL '180 days'),
    (1000 + random() * 49000)::numeric(10,2),
    (random() * 20000)::numeric(10,2),
    (ARRAY['FNOL_RECEIVED', 'ASSIGNED', 'UNDER_INVESTIGATION', 'SETTLED', 'CLOSED'])[1 + (i % 5)]
FROM generate_series(1, 400) AS g(i);

INSERT INTO insurance.claim_documents (claim_id, document_type, s3_object_key, ocr_confidence, uploaded_at)
SELECT
    1 + (i % 400),
    (ARRAY['PHOTO', 'ESTIMATE', 'POLICE_REPORT', 'MEDICAL_RECORD'])[1 + (i % 4)],
    'claims/' || (1 + (i % 400)) || '/' || md5(i::text) || '.pdf',
    (60 + random() * 40)::numeric(5,2),
    NOW() - (random() * INTERVAL '90 days')
FROM generate_series(1, 300) AS g(i);

INSERT INTO insurance.claims_payments (claim_id, payee_name, amount_usd, payment_method, status, processed_at)
SELECT
    1 + (i % 400),
    'Payee ' || i,
    (500 + random() * 25000)::numeric(10,2),
    (ARRAY['ACH', 'CHECK', 'WIRE'])[1 + (i % 3)],
    (ARRAY['PENDING', 'PROCESSED', 'PROCESSED', 'PROCESSED', 'FAILED'])[1 + (i % 5)],
    CASE WHEN i % 5 != 0 THEN NOW() - (random() * INTERVAL '30 days') ELSE NULL END
FROM generate_series(1, 200) AS g(i);

INSERT INTO insurance.reinsurance_share (claim_id, reinsurer_id, share_pct, share_amount_usd, status)
SELECT
    1 + (i % 400),
    (ARRAY['MUNICH_RE', 'SWISS_RE', 'BERKSHIRE', 'LLOYD_LONDON'])[1 + (i % 4)],
    (10 + random() * 40)::numeric(5,2),
    (1000 + random() * 20000)::numeric(10,2),
    (ARRAY['SUBMITTED', 'APPROVED', 'PAID'])[1 + (i % 3)]
FROM generate_series(1, 150) AS g(i);

-- =============================================================================
-- Manufacturing schema — Automotive Production Plant
-- =============================================================================

CREATE SCHEMA manufacturing;

CREATE TABLE manufacturing.production_lines (
    line_id              VARCHAR(32)  PRIMARY KEY,
    plant                VARCHAR(32)  NOT NULL,
    line_name            VARCHAR(64)  NOT NULL,
    zone                 VARCHAR(16)  NOT NULL,  -- Zone-A / Zone-B / Zone-C
    target_cycle_time_ms INT          NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE manufacturing.equipment_devices (
    device_id            VARCHAR(32)  PRIMARY KEY,
    line_id              VARCHAR(32)  NOT NULL REFERENCES manufacturing.production_lines,
    device_type          VARCHAR(32)  NOT NULL,  -- robot_arm / cnc_machine / conveyor / press / vision_system
    manufacturer         VARCHAR(64)  NOT NULL,
    model                VARCHAR(64)  NOT NULL,
    commissioned_date    DATE         NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ed_line        ON manufacturing.equipment_devices(line_id);
-- Intentionally NO index on device_type — analytical full-scans by device_type
-- during the RUL batch cascade generate slow-query signal in DBM.

CREATE TABLE manufacturing.telemetry_snapshots (
    snapshot_id    BIGSERIAL    PRIMARY KEY,
    device_id      VARCHAR(32)  NOT NULL REFERENCES manufacturing.equipment_devices,
    metric_type    VARCHAR(32)  NOT NULL,  -- cycle_time_ms / servo_error_count / temperature_c / vibration_g
    metric_value   NUMERIC(10,2) NOT NULL,
    recorded_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ts_device      ON manufacturing.telemetry_snapshots(device_id);
CREATE INDEX idx_ts_recorded    ON manufacturing.telemetry_snapshots(recorded_at DESC);
-- Intentionally NO index on metric_type — the RUL batch analytical query
-- that joins on metric_type causes full scans during the cascade phase.

CREATE TABLE manufacturing.maintenance_schedules (
    schedule_id    BIGSERIAL    PRIMARY KEY,
    device_id      VARCHAR(32)  NOT NULL REFERENCES manufacturing.equipment_devices,
    work_type      VARCHAR(32)  NOT NULL,  -- PREVENTIVE / CORRECTIVE / EMERGENCY
    planned_date   DATE,
    completed_date DATE,
    notes          TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ms_device      ON manufacturing.maintenance_schedules(device_id);

CREATE TABLE manufacturing.rul_predictions (
    prediction_id       BIGSERIAL    PRIMARY KEY,
    device_id           VARCHAR(32)  NOT NULL REFERENCES manufacturing.equipment_devices,
    predicted_rul_hours NUMERIC(10,2) NOT NULL,
    confidence_pct      NUMERIC(5,2) NOT NULL,
    predicted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_rul_device     ON manufacturing.rul_predictions(device_id);
CREATE INDEX idx_rul_predicted  ON manufacturing.rul_predictions(predicted_at DESC);

INSERT INTO manufacturing.production_lines (line_id, plant, line_name, zone, target_cycle_time_ms)
VALUES
    ('ASSY-1-DET', 'Plant-A-Detroit',  'Assembly Line 1',    'Zone-A', 58000),
    ('ASSY-2-DET', 'Plant-A-Detroit',  'Assembly Line 2',    'Zone-A', 58000),
    ('WELD-1-DET', 'Plant-A-Detroit',  'Welding Cell 1',     'Zone-B', 34000),
    ('WELD-2-DET', 'Plant-A-Detroit',  'Welding Cell 2',     'Zone-B', 34000),
    ('PAINT-1-DET','Plant-A-Detroit',  'Paint Line 1',       'Zone-C', 120000),
    ('ASSY-1-AUS', 'Plant-B-Austin',   'Assembly Line 1',    'Zone-A', 62000),
    ('WELD-1-AUS', 'Plant-B-Austin',   'Welding Cell 1',     'Zone-B', 36000),
    ('PAINT-1-AUS','Plant-B-Austin',   'Paint Line 1',       'Zone-C', 115000),
    ('ASSY-1-MUN', 'Plant-C-Munich',   'Montagelinie 1',     'Zone-A', 55000),
    ('WELD-1-MUN', 'Plant-C-Munich',   'Schweißzelle 1',     'Zone-B', 32000);

INSERT INTO manufacturing.equipment_devices (device_id, line_id, device_type, manufacturer, model, commissioned_date)
SELECT
    (ARRAY['ASSY-1-DET','ASSY-2-DET','WELD-1-DET','WELD-2-DET','PAINT-1-DET',
           'ASSY-1-AUS','WELD-1-AUS','PAINT-1-AUS','ASSY-1-MUN','WELD-1-MUN'])[1 + (i % 10)] || '-' ||
    (ARRAY['robot_arm','cnc_machine','conveyor','press','vision_system'])[1 + (i % 5)] || '-' || lpad(i::text, 3, '0'),
    (ARRAY['ASSY-1-DET','ASSY-2-DET','WELD-1-DET','WELD-2-DET','PAINT-1-DET',
           'ASSY-1-AUS','WELD-1-AUS','PAINT-1-AUS','ASSY-1-MUN','WELD-1-MUN'])[1 + (i % 10)],
    (ARRAY['robot_arm','cnc_machine','conveyor','press','vision_system'])[1 + (i % 5)],
    (ARRAY['FANUC','ABB','KUKA','Siemens','Cognex'])[1 + (i % 5)],
    (ARRAY['M-20iA','IRB 6700','KR 120','SINUMERIK 840D','In-Sight 9902'])[1 + (i % 5)],
    CURRENT_DATE - (365 + (i % 1825))
FROM generate_series(1, 80) AS g(i);

-- Large telemetry dataset — row volume makes analytical queries visibly slow
INSERT INTO manufacturing.telemetry_snapshots (device_id, metric_type, metric_value, recorded_at)
SELECT
    (SELECT device_id FROM manufacturing.equipment_devices ORDER BY device_id OFFSET (i % 80) LIMIT 1),
    (ARRAY['cycle_time_ms','servo_error_count','temperature_c','vibration_g'])[1 + (i % 4)],
    CASE (i % 4)
        WHEN 0 THEN (45000 + random() * 30000)::numeric(10,2)
        WHEN 1 THEN (random() * 5)::numeric(10,2)
        WHEN 2 THEN (35 + random() * 50)::numeric(10,2)
        ELSE        (0.1 + random() * 2)::numeric(10,2)
    END,
    NOW() - ((random() * 7)::int || ' days')::interval - (random() * INTERVAL '24 hours')
FROM generate_series(1, 5000) AS g(i);

INSERT INTO manufacturing.maintenance_schedules (device_id, work_type, planned_date, completed_date, notes)
SELECT
    (SELECT device_id FROM manufacturing.equipment_devices ORDER BY device_id OFFSET (i % 80) LIMIT 1),
    (ARRAY['PREVENTIVE','PREVENTIVE','CORRECTIVE','EMERGENCY'])[1 + (i % 4)],
    CURRENT_DATE + (i % 90) - 45,
    CASE WHEN i % 3 = 0 THEN CURRENT_DATE - (i % 30) ELSE NULL END,
    NULL
FROM generate_series(1, 120) AS g(i);

INSERT INTO manufacturing.rul_predictions (device_id, predicted_rul_hours, confidence_pct, predicted_at)
SELECT
    (SELECT device_id FROM manufacturing.equipment_devices ORDER BY device_id OFFSET (i % 80) LIMIT 1),
    (200 + random() * 8000)::numeric(10,2),
    (70 + random() * 29)::numeric(5,2),
    NOW() - (random() * INTERVAL '48 hours')
FROM generate_series(1, 200) AS g(i);

-- =============================================================================
-- Datadog DBM user — access to all vertical schemas
-- =============================================================================

CREATE USER datadog WITH PASSWORD 'datadog';
GRANT pg_monitor TO datadog;

-- public (finance)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datadog;

-- healthcare
GRANT USAGE ON SCHEMA healthcare TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA healthcare TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA healthcare GRANT SELECT ON TABLES TO datadog;

-- hospitality
GRANT USAGE ON SCHEMA hospitality TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA hospitality TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA hospitality GRANT SELECT ON TABLES TO datadog;

-- insurance
GRANT USAGE ON SCHEMA insurance TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA insurance TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA insurance GRANT SELECT ON TABLES TO datadog;

-- manufacturing
GRANT USAGE ON SCHEMA manufacturing TO datadog;
GRANT SELECT ON ALL TABLES IN SCHEMA manufacturing TO datadog;
ALTER DEFAULT PRIVILEGES IN SCHEMA manufacturing GRANT SELECT ON TABLES TO datadog;

-- explain_statement function — enables explain plans without superuser
CREATE SCHEMA datadog;
GRANT USAGE ON SCHEMA datadog TO datadog;
GRANT CREATE ON SCHEMA datadog TO datadog;

CREATE OR REPLACE FUNCTION datadog.explain_statement(
   l_query TEXT,
   OUT explain JSON
)
RETURNS SETOF JSON AS
$$
DECLARE
  curs REFCURSOR;
  plan JSON;
BEGIN
  OPEN curs FOR EXECUTE pg_catalog.concat('EXPLAIN (FORMAT JSON) ', l_query);
  FETCH curs INTO plan;
  CLOSE curs;
  RETURN QUERY SELECT plan;
END;
$$
LANGUAGE 'plpgsql'
RETURNS NULL ON NULL INPUT
SECURITY DEFINER;
