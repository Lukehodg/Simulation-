# Personal Health AI — build plan

A local-first system that pulls everything Garmin, WHOOP, MyFitnessPal and Hevy
know about you into one database, computes honest statistics on top of it
(cycle-aware), and puts an LLM on top that *reasons* over those numbers instead
of guessing at them.

Decisions already made: **iPhone**, **runs locally on your machine**, **I build
it, you use it**.

---

## 1. The constraint that shapes everything: what each source will actually give us

This is the part most "health AI" projects get wrong — they design the clever
part first and discover in week three that the data can't be had. Current state
of each of your four sources, as of September 2026:

| Source | Official access? | What we get | Difficulty |
|---|---|---|---|
| **WHOOP** | Yes — public API v2, OAuth 2.0, free with membership, webhooks | Sleep (with stages), recovery, HRV, resting HR, physiological cycles, strain, workouts, body measurements | **Easy** |
| **Hevy** | Yes — public API, bearer key from `hevy.com/settings?developer`, Pro accounts only | Every set, rep, weight, RPE, routines, exercise templates, exercise history | **Easy** |
| **MyFitnessPal** | No — public API killed in 2019 | Daily macro totals via Apple Health; per-food detail only via Premium CSV export | **Medium** |
| **Garmin** | No personal API. Dev program requires a legal entity, rejects personal use, and new onboarding is reportedly paused. The `garth` library that every unofficial client depended on was deprecated in March 2026 and new logins are broken | Everything — but through side doors | **Hard** |

Two consequences:

1. **Nothing gets built on scraping Garmin Connect.** That road is closed and
   would break again anyway. We use documented exports and the files the watch
   itself writes.
2. **Ingestion is a ladder per source, not a single connector.** Each source has
   a preferred path and a fallback, and the schema is written so a metric can
   arrive by any of them without the analysis layer caring.

### The Garmin ladder (in build order)

1. **Watch → Apple Health → local folder.** Garmin Connect on iOS writes steps,
   heart rate, sleep, workouts, weight and respiration into Apple Health. The
   app **Health Auto Export** (~£5, iOS) runs a scheduled automation that dumps
   150+ Apple Health metrics as JSON to iCloud Drive; a local watcher picks the
   file up. No server, no exposed endpoint, no account — your data goes phone →
   your iCloud → your laptop. This is also how **MyFitnessPal macros** and
   **menstrual cycle logs** arrive, which is why it's rung one.
   *Does not carry:* Body Battery, HRV status, training readiness, training
   load — Garmin keeps those proprietary metrics inside Connect.
2. **Garmin's official data export** (`garmin.com/account/datamanagement/exportdata`).
   A GDPR-mandated ZIP of your entire history: activity CSVs, GPX, and wellness
   JSON. This is the **backfill** — years of history in one shot. Request it on
   day one, it takes a few days to arrive. Re-request quarterly for the
   proprietary fields.
3. **`.FIT` files straight off the watch.** Plug the watch in over USB and it
   mounts as storage: `/GARMIN/Activity/*.FIT` plus the monitoring and sleep FIT
   files. These are the *native* records — higher fidelity than anything the API
   would have given us — parsed locally with `fitdecode`. No network, no
   account, nothing to deprecate. Recommended as the recurring high-fidelity
   path if you'll plug the watch in every week or two.
4. *Considered and rejected:* commercial aggregators (Terra, Rook) unify all of
   this behind one API, but start around $399/month. Absurd for one person.

---

## 2. Architecture

Deliberately boring. Every box is a file or a Python module on your laptop.

```
  ┌── WHOOP API ────────┐   OAuth2, polled hourly
  ┌── Hevy API ─────────┤   bearer key, incremental
  ┌── Apple Health ─────┤   HAE → iCloud Drive → folder watcher
  │     (Garmin, MFP,   │   (steps, HR, sleep, macros, menstrual)
  │      cycle logs)    │
  ┌── Garmin export ────┤   ZIP parser, one-shot backfill
  └── Watch .FIT files ─┘   USB, fitdecode
             │
             ▼
      raw/  (immutable JSON/Parquet, one file per fetch, never edited)
             │   ← re-parseable forever; if a normaliser has a bug we replay
             ▼
      DuckDB  ── normalised tables, idempotent upsert on (source, source_id)
             │
             ▼
      features ── daily fact table: baselines, z-scores, load, cycle phase
             │
             ▼
      MCP tool server ── typed, deterministic queries over the features
             │
             ▼
      Claude ── interpretation, briefings, research, Q&A
```

**Stack:** Python 3.12 + `uv`, DuckDB for storage (single file, real SQL,
reads Parquet directly, no server), `pydantic` for schemas, `httpx` for
connectors, `launchd` for scheduling, `pytest` for the parsers.

**The rule that makes it trustworthy:** the LLM never does arithmetic and never
sees a raw dump. Python computes every number; the model gets small, labelled
result sets through tools and does what it's actually good at — noticing
patterns, connecting them to literature, explaining them, asking the next
question.

---

## 3. Data model

Two layers. Raw is sacred and append-only; everything else is derived and can be
rebuilt from scratch with one command.

```sql
-- one row per measurement, whatever it is
observations(
  ts TIMESTAMP,          -- UTC
  local_date DATE,       -- the day *you* would call it
  metric VARCHAR,        -- 'hrv_rmssd', 'resting_hr', 'sleep_duration', ...
  value DOUBLE,
  unit VARCHAR,
  source VARCHAR,        -- 'whoop' | 'garmin_fit' | 'apple_health' | ...
  source_id VARCHAR,     -- upstream id, for idempotent upsert
  ingested_at TIMESTAMP,
  PRIMARY KEY (source, metric, source_id)
);

sleeps(start_ts, end_ts, local_date, duration_min, efficiency,
       rem_min, deep_min, light_min, awake_min, hr_min, hrv, source, source_id);

workouts(start_ts, end_ts, local_date, type, duration_min, distance_m,
         avg_hr, max_hr, kcal, strain, rpe, source, source_id);

strength_sets(       -- Hevy, the one source with real set-level detail
  workout_id, exercise, set_index, weight_kg, reps, rpe, is_warmup, ts);

nutrition_days(local_date, kcal, protein_g, carbs_g, fat_g, fibre_g,
               sodium_mg, caffeine_mg, water_ml, source);

nutrition_items(local_date, meal, food, qty, kcal, protein_g, ...);  -- MFP CSV only

cycle_events(local_date, event, flow, source);  -- 'period_start','period_end',...
```

### Handling the overlap — Garmin *and* WHOOP both measure sleep and HRV

Both get stored. Neither wins globally. A `source_priority` view picks one
canonical series per metric so that every analysis is reproducible:

- **WHOOP** — recovery, HRV, sleep staging, resting HR (it's the dedicated
  recovery device and its overnight sampling is denser)
- **Garmin** — daytime steps, GPS workouts, VO2max, training load, Body Battery
- **Hevy** — all strength volume and progression
- **Apple Health / MFP** — nutrition

**Never mix HRV across devices in one series.** WHOOP reports RMSSD during
slow-wave sleep; Garmin reports an overnight average — different numbers
measuring different things. Every derived statistic is z-scored *within source*,
so a device change shows up as a baseline reset rather than a fake trend.

---

## 4. The features layer — where the actual intelligence lives

One row per day, recomputed nightly. This is what the model reads.

- **Rolling baselines** — 28-day median and MAD per metric, plus EWMA. "Your
  HRV is 42" is meaningless; "your HRV is 1.8 MAD below your 28-day baseline,
  third consecutive day" is a finding.
- **Training load** — acute (7d) vs chronic (28d) load ratio from Garmin strain
  and Hevy tonnage, so ramp rate is visible rather than inferred.
- **Strength progression** — estimated 1RM per lift (Epley), volume per muscle
  group per week, staleness detection per exercise.
- **Energy availability** — intake minus exercise expenditure relative to lean
  mass. Combined with cycle regularity this is the one thing in the system with
  a genuine red flag attached to it (see §7).
- **Sleep regularity** — variance in midpoint, which predicts more than duration
  does.
- **Lagged relationships** — does yesterday's alcohol/late meal/training load
  move tonight's HRV, deep sleep, resting HR? Computed with explicit lags and
  honest uncertainty (see the caveat in §6).

---

## 5. Cycle modelling — treated as a first-class axis, not a widget

This is the piece that makes the system more useful than any of the four apps
individually, because none of them condition their scores on where you are in
your cycle.

**Input:** period logs from Apple Health (loggable in Garmin Connect, WHOOP, or
the Health app itself — all three land in Apple Health, and Health Auto Export
carries menstrual data).

**Phase estimation:** menses from logged flow; ovulation estimated by counting
*backwards* from the next period rather than forwards from the last, since
luteal length is far more stable than follicular length. Then corroborated
physiologically — the luteal phase raises resting heart rate and skin
temperature and lowers HRV, all three of which you already measure on two
devices. Where the physiological signature and the calendar disagree, the system
says so instead of picking silently.

**Phase-aware baselines — the real payoff.** Comparing today's recovery to your
all-time baseline is noise when the luteal phase reliably depresses it. So every
baseline is computed *within phase*: today's HRV against your own mid-luteal
distribution, not against the whole year. A "red recovery" that's normal for day
23 stops generating a false alarm, and a genuinely bad day in the follicular
phase stops being masked.

**What that unlocks:**
- Training and nutrition recommendations that account for the phase you're in
- Cycle-length and symptom trend tracking, with drift flagged
- Correctly attributing a bad week to the cycle rather than to overtraining
  (and vice versa — which is exactly what you'd want to catch)

---

## 6. The AI layer

**Not** "dump a CSV into a prompt". A local MCP server exposes typed tools; the
model calls them:

```
query_metric(metric, start, end, grain)      → time series + baseline band
compare_windows(metric, window_a, window_b)  → effect size, CI, n
cycle_summary(n_cycles)                      → phase map, current phase, trends
phase_baseline(metric, phase)                → within-phase distribution
training_load(start, end)                    → acute:chronic, per-modality
lift_progression(exercise)                   → e1RM series, volume, staleness
nutrition_summary(start, end)                → macros vs targets, adherence
correlate(metric_a, metric_b, lag_days)      → r, CI, n, autocorr warning
search_literature(question)                  → PubMed/Semantic Scholar w/ citations
run_experiment(...)                          → n-of-1 protocol tracking
```

Three things run on top:

1. **Morning brief** — a scheduled job: what happened yesterday, what the body
   is saying today, what to do about it. Two paragraphs, not a dashboard.
2. **Ask anything** — "why has my sleep been bad since August?", "am I actually
   getting stronger on RDLs or just adding reps?", "what changes in the week
   before my period?" The model queries, computes, answers with the numbers
   attached.
3. **Weekly research pass** — takes the week's most interesting pattern, searches
   the literature for it, and reports what's known, with citations and an honest
   note on study quality.

### The statistical honesty problem (and how we handle it)

An LLM given a year of daily health data will find correlations, because with
~40 metrics there are ~800 pairs and some will hit p < 0.05 by chance. Health
data is also autocorrelated (today looks like yesterday), weekly-seasonal
(weekends differ), and confounded (a hard week is also a badly-slept week).
Naive correlation over it is close to worthless.

So the `correlate` tool returns effect size and confidence interval rather than
a p-value, reports the effective sample size after accounting for
autocorrelation, and flags multiple comparisons. And the system prompt is
explicit: observed associations are hypotheses, and the way to test one is
**`run_experiment`** — a pre-registered n-of-1 protocol (state the hypothesis
and the metric first, alternate two-week blocks, then analyse). That turns "your
HRV correlates with magnesium" from a horoscope into something you can actually
act on.

---

## 7. Safety and privacy

**Safety.** The agent surfaces patterns and cites evidence. It does not
diagnose, does not prescribe, and does not dose. Where a pattern has a real
clinical shape it escalates rather than advises — the concrete case worth
building for: sustained low energy availability alongside high training load and
cycle disruption is the signature of RED-S, and the correct output is "these
three things together are worth a conversation with a doctor", not a nutrition
tweak.

**Privacy.** Data never leaves your machine except as small aggregates in LLM
calls. Credentials live in the macOS Keychain, never in the repo. The DuckDB
file and everything under `raw/` are gitignored, and the repo carries a
pre-commit hook that refuses to commit them. If you later want the most
sensitive queries answered with nothing leaving the laptop at all, the tool
layer is model-agnostic and a local model can be pointed at it.

---

## 8. Roadmap

Each phase ends with something you can actually use.

| Phase | Work | You get |
|---|---|---|
| **0** | Repo scaffold, DuckDB schema, secrets, scheduler, `make rebuild` | Foundation |
| **1** | WHOOP + Hevy connectors (both have real APIs — fastest path to real data) | Recovery, sleep and every set you've lifted, queryable |
| **2** | Garmin backfill (export ZIP parser + `.FIT` parser) and the Apple Health bridge, which also brings MFP macros and cycle logs | Complete history, all four sources |
| **3** | Features layer: baselines, load, progression, energy availability | Honest numbers instead of app scores |
| **4** | Cycle model and phase-aware baselines | The thing none of your apps do |
| **5** | MCP server, morning brief, ask-anything | The actual AI |
| **6** | Literature search, n-of-1 experiments | Research loop |
| **7** | Local dashboard | Something to look at |

### What I need from you, and when

Two of these have lead time, so they're worth doing today even though we need
them in phase 2:

- **Now:** request your Garmin data export at
  `garmin.com/account/datamanagement/exportdata` — it takes days to arrive
- **Now:** confirm Hevy Pro; if yes, grab the API key from
  `hevy.com/settings?developer`
- **Phase 1:** create a WHOOP developer app at `developer.whoop.com` (free) —
  I'll walk you through the OAuth consent once, then it's automatic
- **Phase 2:** install Health Auto Export on the iPhone (~£5) and confirm
  Garmin Connect and MyFitnessPal are both writing to Apple Health
- **Phase 2, optional:** MyFitnessPal Premium if you want per-food detail rather
  than daily macro totals; and the watch cable if you want native `.FIT` fidelity

### Open questions for later

- How far back does your history go on each app? Changes how much backfill work
  is worth it.
- Where do you log your period today — Garmin, WHOOP, the Health app, or a
  separate app? (Determines whether cycle data flows automatically or needs a
  one-off import.)
- Any bloods, DEXA scans, or clinical results you'd want in the same database?
  The `observations` table takes them as-is.
