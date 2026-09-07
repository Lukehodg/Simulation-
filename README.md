# health

A local-first personal health data warehouse. It pulls Garmin, WHOOP,
MyFitnessPal and Hevy into one DuckDB file on your own machine, so that
questions spanning all four — *does my training load explain this week's
sleep, or is it my cycle?* — can actually be asked.

Nothing leaves the machine. There is no server, no account, and no cloud
component.

[`PLAN.md`](PLAN.md) is the design: why each source is reached the way it is,
what the analysis layer computes, and what comes next.

## Status

Built and tested:

| | |
|---|---|
| **Storage** | DuckDB schema, canonical views, idempotent loading, replay from raw |
| **WHOOP** | OAuth 2.0 flow, token refresh, incremental paging, sleep / recovery / cycle / workout parsing |
| **Hevy** | Full history paging, incremental events feed, set-level parsing, upstream edits and deletions |
| **Apple Health** | Health Auto Export JSON — carries Garmin dailies, MyFitnessPal macros, sleep and period logs |
| **Strength analysis** | Estimated 1RM trends with their fit, weekly tonnage by muscle group, stale-lift detection |
| **Cycle model** | Cycle reconstruction, phase inference, phase-aware baselines, physiological corroboration |
| **Blood tests** | Panel import with unit conversion, reference-range flagging, phase-annotated trends |
| **Research** | Europe PMC search returning study design, citations and DOIs |

Next: the Garmin export and `.FIT` parsers, then the rest of the features layer
(rolling baselines, training load, energy availability), and the MCP tool
server the agent talks to.

## Setup

```sh
make install          # uv venv + editable install
cp .env.example .env  # then fill in, or use the macOS Keychain
health init
```

Credentials are read from the environment, then the macOS Keychain, then
`~/.config/health/secrets.json` (0600). None of them are ever read from this
repository, and `data/` is gitignored.

```sh
security add-generic-password -a health -s HEVY_API_KEY -w 'your-key'
```

Then connect each source:

```sh
health auth whoop     # one-time browser consent; tokens refresh themselves
health doctor         # checks every credential and endpoint, one line each
health sync           # fetch everything new and load it
health status         # what's in the database, and how fresh
```

### WHOOP

Create a free app at [developer.whoop.com](https://developer.whoop.com) and
register the redirect URI **exactly** as `http://localhost:8765/callback`.
Put the client ID and secret in the Keychain as `WHOOP_CLIENT_ID` and
`WHOOP_CLIENT_SECRET`, then run `health auth whoop`.

### Hevy

Hevy Pro only. Grab the key from `hevy.com/settings?developer` and store it as
`HEVY_API_KEY`.

### Apple Health (this is also how Garmin and MyFitnessPal arrive)

Garmin has no personal API — its developer programme requires a legal entity,
and the library every unofficial client depended on was deprecated in March
2026. So Garmin Connect writes into Apple Health instead, alongside your
MyFitnessPal macros and your period logs, and
[Health Auto Export](https://apps.apple.com/us/app/health-auto-export-json-csv/id1115567069)
drops that as JSON into iCloud Drive on a schedule.

Point `HEALTH_APPLE_EXPORT_DIR` at the folder it writes to, and `health sync`
picks up anything new. To load a file by hand:

```sh
health ingest ~/Downloads/HealthAutoExport-2026-09-01.json
```

Body Battery, HRV status, training readiness and training load do **not** come
through this route — Garmin keeps them inside Connect. Those arrive with the
Garmin data export and the `.FIT` files off the watch.

## Strength

```sh
health lifts                          # every exercise: best e1RM, trend, staleness
health lifts "Squat (Barbell)"        # one lift, session by session
health volume --weeks 8               # weekly tonnage by muscle group
```

```
exercise                     muscle       last          n  best e1RM  trend
Bench Press (Barbell)        chest        2026-09-03   14    76.5 kg  up 1.67 kg/week over 8 sessions (r²=0.85)
Overhead Press (Barbell)     shoulders    2026-09-03   14    45.0 kg  down 0.64 kg/week over 8 sessions (r²=0.28)
Pull Up                      lats         2026-08-06    9    17.4 kg  up 1.77 kg/week over 8 sessions (r²=0.88)  (stale)
```

Every trend carries its sample size and r², because a confident-looking
"+2.1 kg/week" fitted to three sessions is worse than no answer at all. Sets
above 12 reps count toward volume but are excluded from strength estimates —
Epley is fitted to low-rep work and flatters endurance sets into fake PRs.
Exercises with no Hevy template appear as `unmapped` in the volume table rather
than disappearing from it.

## Cycle

```sh
health cycle                       # where you are, and how metrics move by phase
health cycle --day 2026-08-11      # judge one day against its own phase
```

```
day 19 of a typical 28-day cycle   (luteal)

metric                  menses  follicular   ovulation      luteal   luteal shift
hrv_rmssd                 71.6        72.2        65.4        53.3   -18.8  (as expected)
resting_hr                51.6        52.2        52.8        56.8   +4.6  (as expected)

hrv_rmssd 46.6 on 2026-08-11 — luteal (cycle day 19) — -1.5 SD against your usual
                                                     — -0.9 SD against the same phase (n=64)
  ^ a phase-blind baseline would flag this; the same phase of your own cycles
    says it is ordinary
```

That last line is the point of the whole module. None of Garmin, WHOOP or
MyFitnessPal condition their scores on cycle phase, so a recovery score that is
entirely normal for day 19 still reads as alarming — every month.

Ovulation is estimated by counting **backwards** from the next period rather
than forwards from the last, because luteal length is far more stable than
follicular length; a "day 14" assumption is wrong for most cycles and wrong in
a way that shifts with cycle length. The "as expected" column checks the
calendar against your own physiology: the luteal phase should raise resting
heart rate and lower HRV, and if your data disagrees it says so rather than
quietly resolving it.

Where there is not enough history the answer is withheld rather than
estimated — a phase baseline needs five same-phase days, a first cycle with no
completed cycle behind it is left unplaced rather than guessed at, and gaps too
long to be real cycles are reported as probable unlogged periods instead of
being averaged into your median.

## Blood tests

Labs arrive as a file, not an API. Write one panel per file, in either format:

```json
{"date": "2026-08-14", "lab": "Medichecks", "fasting": true,
 "results": [{"analyte": "Ferritin", "value": 11, "unit": "ug/L",
              "ref_low": 13, "ref_high": 150},
             {"analyte": "25-Hydroxyvitamin D", "value": 30, "unit": "ng/mL"}]}
```

```csv
analyte,value,unit,ref_low,ref_high
Ferritin,11,ug/L,13,150
```

```sh
health labs add panel.json
health labs                      # latest panel, flags, and what is out of range
health labs ferritin             # one analyte over time
```

```
analyte                            value  flag    range             phase
Ferritin                         11 ug/L  low     13-150            luteal
HbA1c                      35.5 mmol/mol  normal  <41 (generic)     luteal
LDL cholesterol                 3 mmol/L  normal  <3 (generic)      luteal
Vitamin D (25-OH)           74.88 nmol/L  normal  50-125 (generic)  luteal
```

Three things it handles that are easy to get quietly wrong:

**Units.** A UK lab reports LDL in mmol/L and an American paper discusses
mg/dL. Values are converted into one canonical unit per analyte — and the
reference range is converted with them, because converting one and not the
other turns a normal result into a scare. HbA1c percent uses its affine
conversion rather than a factor. A unit we don't recognise leaves the number
untouched and withholds the flag rather than guessing.

**Reference ranges.** Your lab's own range always wins, because it belongs to
their assay and their population. Where a result arrives without one, a generic
adult range is used and marked `(generic)` everywhere it appears. The
fallbacks are adult female ranges — change `REFERENCE_PROFILE` in
`health/analytes.py` if that is not you.

**Cycle phase.** Every draw is annotated with the phase it was taken in.
Oestradiol, progesterone, LH and FSH vary several-fold across a cycle, and
ferritin and haemoglobin move with menstrual blood loss, so a trend across
draws taken in different phases is refused rather than plotted:

```
Oestradiol moves with the cycle and these draws span follicular, luteal;
compare draws from the same phase, not this series
```

## Research

```sh
health research "creatine supplementation women"
health research --analyte ferritin --context "endurance athletes"
```

Searches Europe PMC — open, keyless, indexes MEDLINE and preprints — and
returns each paper with its study design, journal, year, citation count and
DOI. Results are ordered by design rather than citations, since a meta-analysis
outranks a more-cited narrative review. Searching `--analyte` uses your own
most recent result and its direction, because the literature on *low* ferritin
and the literature on *high* ferritin are different literatures.

It retrieves and labels evidence; it does not interpret it. A citation count is
popularity and a publication type is a design, and neither is a verdict on
whether a finding applies to you.

> Verified against a recorded Europe PMC response, not against the live API —
> the sandbox this was built in blocks outbound requests to it. If the shape
> has drifted, the failure will be loud and the fix small.

## Asking it things

```sh
health sql "SELECT local_date, hrv, resting_hr, recovery, sleep_min FROM daily
            ORDER BY local_date DESC LIMIT 14"
```

`daily` is the convenience pivot; `daily_metrics` is one row per metric per day
from the highest-priority source that recorded it; `observations` is everything
from everywhere, which is what you query when comparing two devices.

## How it's built

```
sources/  fetch (network, impure) and parse (pure) — deliberately separable
raw/      every payload ever received, immutable, never edited
DuckDB    normalised tables, upserted on each row's natural key
views     canonical series, daily pivot, working sets, session bests
```

Two rules do most of the work:

**Raw is sacred.** Every payload lands on disk before anything interprets it,
and parsers are pure functions of those bytes. When a parser turns out to be
wrong, `health replay` rebuilds every table from scratch — no re-downloading a
year of history that an API may no longer be willing to give you.

**Writes are idempotent.** Every table has a natural key and every write is an
upsert, so syncing twice, replaying, and re-fetching an overlap window all
converge on the same database. Syncs deliberately re-fetch a few days: WHOOP
re-scores a night hours later, and Hevy workouts get edited after the session.

One thing is deliberately *not* unified. WHOOP reports HRV as RMSSD during
slow-wave sleep; Apple reports SDNN. They are different numbers measuring
different things, so they keep separate metric names and are never averaged
together. Comparisons across devices z-score within source.

## Tests

```sh
make test
```

The suite covers the storage guarantees (idempotency, upstream deletions,
source priority), each parser against realistic payloads, and the end-to-end
promise that dropping the database and replaying `raw/` reproduces it exactly.
