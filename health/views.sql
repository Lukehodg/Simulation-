-- Views layered over the raw tables. Rebuilt on every `health init`, so
-- changing one is a code change, never a migration.

-- The canonical series: one row per metric per day, from the highest-priority
-- source that actually recorded it. Analyses read this; comparisons across
-- devices read `observations` directly and z-score within source.
CREATE OR REPLACE VIEW daily_metrics AS
WITH ranked AS (
    SELECT
        o.local_date,
        o.metric,
        o.source,
        AVG(o.value) AS value,
        ANY_VALUE(o.unit) AS unit,
        COUNT(*) AS n,
        ROW_NUMBER() OVER (
            PARTITION BY o.local_date, o.metric
            ORDER BY COALESCE(p.rank, 99), o.source
        ) AS pick
    FROM observations o
    LEFT JOIN source_priority p
           ON p.metric = o.metric AND p.source = o.source
    GROUP BY o.local_date, o.metric, o.source, p.rank
)
SELECT local_date, metric, value, unit, source, n
FROM ranked
WHERE pick = 1;

-- Convenience: one row per day, the handful of numbers you actually look at.
CREATE OR REPLACE VIEW daily AS
SELECT
    local_date,
    MAX(CASE WHEN metric = 'hrv_rmssd'      THEN value END) AS hrv,
    MAX(CASE WHEN metric = 'resting_hr'     THEN value END) AS resting_hr,
    MAX(CASE WHEN metric = 'recovery_score' THEN value END) AS recovery,
    MAX(CASE WHEN metric = 'sleep_duration' THEN value END) AS sleep_min,
    MAX(CASE WHEN metric = 'strain'         THEN value END) AS strain,
    MAX(CASE WHEN metric = 'steps'          THEN value END) AS steps,
    MAX(CASE WHEN metric = 'energy_intake'  THEN value END) AS kcal_in,
    MAX(CASE WHEN metric = 'protein'        THEN value END) AS protein_g,
    MAX(CASE WHEN metric = 'body_mass'      THEN value END) AS weight_kg
FROM daily_metrics
GROUP BY local_date;

-- Estimated 1RM per set (Epley). Kept as a view so the formula lives in one
-- place; anything above ~10 reps is extrapolation and is filtered by callers.
CREATE OR REPLACE VIEW strength_e1rm AS
SELECT
    local_date,
    exercise,
    weight_kg,
    reps,
    weight_kg * (1 + reps / 30.0) AS e1rm,
    source,
    workout_id
FROM strength_sets
WHERE weight_kg > 0 AND reps > 0 AND COALESCE(set_type, '') != 'warmup';
