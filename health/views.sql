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

-- Working sets only: warmups are not training volume and would flatter every
-- number downstream.
CREATE OR REPLACE VIEW working_sets AS
SELECT
    s.*,
    s.weight_kg * s.reps AS volume_kg,
    -- Epley. Honest above about 12 reps it is not: the formula is fitted to
    -- low-rep work and drifts badly past that, so high-rep sets still count
    -- for volume but are excluded from strength estimates.
    CASE WHEN s.reps <= 12 THEN s.weight_kg * (1 + s.reps / 30.0) END AS e1rm,
    t.primary_muscle,
    t.equipment
FROM strength_sets s
LEFT JOIN exercise_templates t
       ON t.source = s.source AND t.template_id = s.exercise_id
WHERE s.weight_kg > 0
  AND s.reps > 0
  AND lower(COALESCE(s.set_type, 'normal')) NOT IN ('warmup', 'warm_up');

-- One row per exercise per session: the best set you actually did that day.
CREATE OR REPLACE VIEW session_bests AS
SELECT
    local_date,
    exercise,
    ANY_VALUE(exercise_id)   AS exercise_id,
    ANY_VALUE(primary_muscle) AS primary_muscle,
    MAX(e1rm)                AS best_e1rm,
    SUM(volume_kg)           AS volume_kg,
    COUNT(*)                 AS sets,
    MAX(weight_kg)           AS top_weight_kg,
    ANY_VALUE(workout_id)    AS workout_id,
    ANY_VALUE(source)        AS source
FROM working_sets
GROUP BY local_date, exercise;

-- Weekly tonnage by muscle group. Sets whose exercise has no template land in
-- 'unmapped' rather than vanishing — a silently dropped exercise is how a
-- volume chart lies to you.
CREATE OR REPLACE VIEW weekly_volume AS
SELECT
    date_trunc('week', local_date)              AS week,
    COALESCE(primary_muscle, 'unmapped')        AS muscle,
    SUM(volume_kg)                              AS volume_kg,
    SUM(reps)                                   AS reps,
    COUNT(*)                                    AS sets,
    COUNT(DISTINCT local_date)                  AS sessions
FROM working_sets
GROUP BY 1, 2;
