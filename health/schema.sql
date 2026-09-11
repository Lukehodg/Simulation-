-- Canonical schema. Every table is derived from raw/ and can be dropped and
-- rebuilt with `health replay`, so migrations are a rebuild, not a dance.

CREATE TABLE IF NOT EXISTS observations (
    ts          TIMESTAMPTZ NOT NULL,
    local_date  DATE        NOT NULL,
    metric      VARCHAR     NOT NULL,
    value       DOUBLE      NOT NULL,
    unit        VARCHAR,
    source      VARCHAR     NOT NULL,
    source_id   VARCHAR     NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, metric, source_id)
);
CREATE INDEX IF NOT EXISTS observations_metric_date ON observations (metric, local_date);

CREATE TABLE IF NOT EXISTS sleeps (
    source        VARCHAR     NOT NULL,
    source_id     VARCHAR     NOT NULL,
    start_ts      TIMESTAMPTZ NOT NULL,
    end_ts        TIMESTAMPTZ NOT NULL,
    local_date    DATE        NOT NULL,   -- the morning you woke up on
    duration_min  DOUBLE,                 -- time asleep, not time in bed
    in_bed_min    DOUBLE,
    efficiency    DOUBLE,
    rem_min       DOUBLE,
    deep_min      DOUBLE,
    light_min     DOUBLE,
    awake_min     DOUBLE,
    is_nap        BOOLEAN,
    hr_min        DOUBLE,
    hrv           DOUBLE,
    respiratory_rate DOUBLE,
    ingested_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS workouts (
    source       VARCHAR     NOT NULL,
    source_id    VARCHAR     NOT NULL,
    start_ts     TIMESTAMPTZ NOT NULL,
    end_ts       TIMESTAMPTZ,
    local_date   DATE        NOT NULL,
    type         VARCHAR,
    title        VARCHAR,
    duration_min DOUBLE,
    distance_m   DOUBLE,
    avg_hr       DOUBLE,
    max_hr       DOUBLE,
    kcal         DOUBLE,
    strain       DOUBLE,
    rpe          DOUBLE,
    ingested_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, source_id)
);

-- Hevy is the only source with real set-level detail, and set-level detail is
-- the only way to say anything true about strength progression.
CREATE TABLE IF NOT EXISTS strength_sets (
    source       VARCHAR     NOT NULL,
    workout_id   VARCHAR     NOT NULL,
    exercise_idx INTEGER     NOT NULL,
    set_idx      INTEGER     NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    local_date   DATE        NOT NULL,
    exercise     VARCHAR     NOT NULL,
    exercise_id  VARCHAR,
    set_type     VARCHAR,
    weight_kg    DOUBLE,
    reps         INTEGER,
    distance_m   DOUBLE,
    duration_s   DOUBLE,
    rpe          DOUBLE,
    ingested_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, workout_id, exercise_idx, set_idx)
);

-- Hevy's exercise catalogue, including your custom exercises. Muscle groups
-- come from here so that volume-by-muscle is Hevy's own classification and not
-- a list we would have to keep in step by hand.
CREATE TABLE IF NOT EXISTS exercise_templates (
    source        VARCHAR NOT NULL,
    template_id   VARCHAR NOT NULL,
    title         VARCHAR,
    primary_muscle    VARCHAR,
    secondary_muscles VARCHAR,
    equipment     VARCHAR,
    is_custom     BOOLEAN,
    ingested_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, template_id)
);

CREATE TABLE IF NOT EXISTS nutrition_days (
    source      VARCHAR NOT NULL,
    local_date  DATE    NOT NULL,
    kcal        DOUBLE,
    protein_g   DOUBLE,
    carbs_g     DOUBLE,
    fat_g       DOUBLE,
    fibre_g     DOUBLE,
    sugar_g     DOUBLE,
    sodium_mg   DOUBLE,
    caffeine_mg DOUBLE,
    water_ml    DOUBLE,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, local_date)
);

CREATE TABLE IF NOT EXISTS nutrition_items (
    source      VARCHAR NOT NULL,
    local_date  DATE    NOT NULL,
    meal        VARCHAR NOT NULL,
    item_idx    INTEGER NOT NULL,
    food        VARCHAR,
    quantity    VARCHAR,
    kcal        DOUBLE,
    protein_g   DOUBLE,
    carbs_g     DOUBLE,
    fat_g       DOUBLE,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, local_date, meal, item_idx)
);

CREATE TABLE IF NOT EXISTS cycle_events (
    source      VARCHAR NOT NULL,
    local_date  DATE    NOT NULL,
    event       VARCHAR NOT NULL,   -- period_start | period_end | flow | spotting | ...
    flow        VARCHAR,            -- none | light | medium | heavy | unspecified
    value       DOUBLE,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, local_date, event)
);

-- Blood tests. One row per analyte per panel. Values are stored in the
-- canonical unit for the analyte with the reference range converted alongside
-- them, and the lab's own words are kept in raw_* so nothing is lost to our
-- vocabulary being incomplete.
CREATE TABLE IF NOT EXISTS lab_results (
    source      VARCHAR NOT NULL,
    panel_id    VARCHAR NOT NULL,
    analyte     VARCHAR NOT NULL,
    local_date  DATE    NOT NULL,
    value       DOUBLE,
    unit        VARCHAR,
    ref_low     DOUBLE,
    ref_high    DOUBLE,
    ref_source  VARCHAR,        -- lab | generic | none
    flag        VARCHAR,        -- low | normal | high | unknown
    converted   BOOLEAN,        -- false when we did not recognise the unit
    lab         VARCHAR,
    fasting     BOOLEAN,
    note        VARCHAR,
    raw_name    VARCHAR,
    raw_value   VARCHAR,
    raw_unit    VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, panel_id, analyte)
);

-- What you are on, entered by hand. `event` is start | change | stop, one row
-- per compound per event. Reconstructed into protocol_days the same way
-- cycle_events becomes cycle_days.
CREATE TABLE IF NOT EXISTS protocol_events (
    source      VARCHAR NOT NULL,   -- 'manual'
    local_date  DATE    NOT NULL,
    event       VARCHAR NOT NULL,   -- start | change | stop
    compound    VARCHAR NOT NULL,   -- canonical key from compounds.py
    dose        DOUBLE,             -- per administration
    unit        VARCHAR,            -- mg | iu | ml
    freq        VARCHAR,            -- weekly | e3d | eod | daily
    route       VARCHAR,            -- im | subq | oral
    note        VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, local_date, event, compound)
);

-- Derived: one row per compound per day it is active. Rebuilt from
-- protocol_events by `health.features.protocol.rebuild`, never written directly.
CREATE TABLE IF NOT EXISTS protocol_days (
    local_date  DATE    NOT NULL,
    compound    VARCHAR NOT NULL,
    weekly_dose DOUBLE,             -- dose normalised to per-week
    unit        VARCHAR,
    weeks_on    DOUBLE,             -- since this compound's start, fractional
    PRIMARY KEY (local_date, compound)
);

-- What you said, once a day. The ratings and blood pressure also land in
-- observations (so correlate/scan/trend work on them for free); this carries
-- what doesn't fit there — the note, and the individual cuff readings behind
-- the averaged bp_systolic/bp_diastolic/bp_pulse.
CREATE TABLE IF NOT EXISTS checkins (
    source      VARCHAR NOT NULL,   -- 'checkin'
    local_date  DATE    NOT NULL,
    note        VARCHAR,
    readings    VARCHAR,            -- raw BP readings behind the average, e.g. "128/82,126/80"
    ingested_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, local_date)
);

-- A pre-registered n-of-1 trial. `start` is written once and is immutable
-- except for a `stop` — the whole point of pre-registration is that the
-- design cannot be reshaped once the data starts arriving. The block schedule
-- itself is never stored: it is a pure function of this row, computed by
-- `health.features.experiment`.
CREATE TABLE IF NOT EXISTS experiment_events (
    source              VARCHAR NOT NULL,   -- 'experiment'
    local_date          DATE    NOT NULL,   -- when the event was recorded
    event               VARCHAR NOT NULL,   -- start | stop
    experiment_id       VARCHAR NOT NULL,
    hypothesis          VARCHAR,
    exposure_type       VARCHAR,            -- manual | metric_threshold
    exposure_metric     VARCHAR,
    exposure_threshold  DOUBLE,
    outcome_metric      VARCHAR,
    predicted_direction VARCHAR,            -- raises | lowers
    block_days          INTEGER,
    blocks_planned      INTEGER,
    start_date          DATE,               -- first day of block 0
    starting_condition  VARCHAR,            -- A (control) | B (exposed)
    reason              VARCHAR,            -- for a stop event
    ingested_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, experiment_id, event)
);

-- Daily adherence for a `manual` exposure only — assumed true unless a miss is
-- logged, so a day with nothing here still counts (intention-to-treat).
CREATE TABLE IF NOT EXISTS experiment_adherence (
    source          VARCHAR NOT NULL,
    experiment_id   VARCHAR NOT NULL,
    local_date      DATE    NOT NULL,
    adhered         BOOLEAN NOT NULL,
    note            VARCHAR,
    ingested_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, experiment_id, local_date)
);

-- Derived: one row per day of every cycle we can reconstruct. Rebuilt from
-- cycle_events by `health.features.cycle.rebuild`, never written by a source.
CREATE TABLE IF NOT EXISTS cycle_days (
    local_date   DATE PRIMARY KEY,
    cycle_index  INTEGER NOT NULL,   -- 0 is the earliest cycle we can see
    cycle_day    INTEGER NOT NULL,   -- 1 is the first day of bleeding
    phase        VARCHAR NOT NULL,   -- menses | follicular | ovulation | luteal
    cycle_length INTEGER,            -- null while the cycle is still running
    is_predicted BOOLEAN NOT NULL    -- true once we are past observed data
);

-- Bookkeeping ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sync_state (
    source     VARCHAR PRIMARY KEY,
    cursor     VARCHAR,             -- opaque: a timestamp, page or nextToken
    last_run   TIMESTAMPTZ,
    last_ok    TIMESTAMPTZ,
    note       VARCHAR
);

CREATE TABLE IF NOT EXISTS raw_files (
    path        VARCHAR PRIMARY KEY,
    source      VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL,
    size_bytes  BIGINT,
    parsed_at   TIMESTAMPTZ
);

-- `health alert`'s dedup memory: a flag that has already been texted stays
-- here until it clears, so a condition that takes a week to resolve sends
-- one message, not one per scheduled check.
CREATE TABLE IF NOT EXISTS alert_state (
    flag_key       VARCHAR PRIMARY KEY,
    first_sent_at  TIMESTAMPTZ NOT NULL
);
