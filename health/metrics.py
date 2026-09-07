"""The canonical metric vocabulary.

Sources disagree about names, units and even meaning. Everything is translated
into these names on the way in, so that a query for `resting_hr` gets the same
thing whichever device recorded it.

The one place we deliberately *refuse* to unify is HRV. WHOOP reports RMSSD
during slow-wave sleep; Garmin reports an overnight average. They are different
numbers measuring different things, and averaging them would invent a trend
that nobody's nervous system ever had. They keep separate metric names, and
`source` disambiguates further.
"""

from __future__ import annotations

# Cardiovascular / autonomic
HRV_RMSSD = "hrv_rmssd"                     # ms
HRV_SDNN = "hrv_sdnn"                       # ms, Apple Health's flavour
RESTING_HR = "resting_hr"                   # bpm
HR_AVG = "hr_avg"                           # bpm
HR_MAX = "hr_max"                           # bpm
RESPIRATORY_RATE = "respiratory_rate"       # breaths/min
SPO2 = "spo2"                               # %
VO2_MAX = "vo2_max"                         # ml/kg/min

# Sleep (durations in minutes; the sleeps table carries the detail)
SLEEP_DURATION = "sleep_duration"           # min
SLEEP_EFFICIENCY = "sleep_efficiency"       # %
SLEEP_MIDPOINT = "sleep_midpoint"           # hours after local midnight

# Scores and load
RECOVERY_SCORE = "recovery_score"           # %, WHOOP
STRAIN = "strain"                           # 0-21, WHOOP
BODY_BATTERY = "body_battery"               # 0-100, Garmin
TRAINING_READINESS = "training_readiness"   # 0-100, Garmin

# Body
BODY_MASS = "body_mass"                     # kg
BODY_FAT = "body_fat_pct"                   # %
LEAN_MASS = "lean_mass"                     # kg
SKIN_TEMP_DEV = "skin_temp_deviation"       # degC from personal baseline
WRIST_TEMP = "wrist_temp"                   # degC

# Activity
STEPS = "steps"                             # count
ACTIVE_ENERGY = "active_energy"             # kcal
BASAL_ENERGY = "basal_energy"               # kcal
EXERCISE_MINUTES = "exercise_minutes"       # min

# Nutrition (daily totals also land in nutrition_days)
ENERGY_INTAKE = "energy_intake"             # kcal
PROTEIN = "protein"                         # g
CARBS = "carbs"                             # g
FAT = "fat"                                 # g
FIBRE = "fibre"                             # g
SODIUM = "sodium"                           # mg
CAFFEINE = "caffeine"                       # mg
WATER = "water"                             # ml
ALCOHOL = "alcohol"                         # units

UNITS: dict[str, str] = {
    HRV_RMSSD: "ms", HRV_SDNN: "ms", RESTING_HR: "bpm", HR_AVG: "bpm", HR_MAX: "bpm",
    RESPIRATORY_RATE: "br/min", SPO2: "%", VO2_MAX: "ml/kg/min",
    SLEEP_DURATION: "min", SLEEP_EFFICIENCY: "%", SLEEP_MIDPOINT: "h",
    RECOVERY_SCORE: "%", STRAIN: "score", BODY_BATTERY: "score", TRAINING_READINESS: "score",
    BODY_MASS: "kg", BODY_FAT: "%", LEAN_MASS: "kg",
    SKIN_TEMP_DEV: "degC", WRIST_TEMP: "degC",
    STEPS: "count", ACTIVE_ENERGY: "kcal", BASAL_ENERGY: "kcal", EXERCISE_MINUTES: "min",
    ENERGY_INTAKE: "kcal", PROTEIN: "g", CARBS: "g", FAT: "g", FIBRE: "g",
    SODIUM: "mg", CAFFEINE: "mg", WATER: "ml", ALCOHOL: "units",
}

# Which source wins when several measure the same thing. Analyses read the
# canonical view; every source's data stays queryable underneath it.
SOURCE_PRIORITY: dict[str, list[str]] = {
    HRV_RMSSD: ["whoop", "garmin_fit", "garmin_export"],
    RESTING_HR: ["whoop", "garmin_fit", "garmin_export", "apple_health"],
    SLEEP_DURATION: ["whoop", "garmin_fit", "garmin_export", "apple_health"],
    SLEEP_EFFICIENCY: ["whoop", "garmin_fit", "apple_health"],
    RECOVERY_SCORE: ["whoop"],
    STRAIN: ["whoop"],
    STEPS: ["garmin_fit", "garmin_export", "apple_health"],
    ACTIVE_ENERGY: ["garmin_fit", "garmin_export", "apple_health"],
    BODY_MASS: ["apple_health", "garmin_export", "whoop"],
    ENERGY_INTAKE: ["mfp_csv", "apple_health"],
    PROTEIN: ["mfp_csv", "apple_health"],
    CARBS: ["mfp_csv", "apple_health"],
    FAT: ["mfp_csv", "apple_health"],
}

DEFAULT_PRIORITY = ["whoop", "garmin_fit", "garmin_export", "hevy", "mfp_csv", "apple_health"]


def unit_for(metric: str) -> str | None:
    return UNITS.get(metric)
