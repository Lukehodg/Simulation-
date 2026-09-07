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
