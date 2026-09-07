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
| **Daily features** | Robust baselines, deviations, training load, sleep regularity, autocorrelation-corrected correlations |
| **Agent tools** | 19 MCP tools — the surface Claude actually talks to |

Next: the Garmin export and `.FIT` parsers, energy availability (which needs
Garmin's expenditure data), the morning brief as a scheduled job, and n-of-1
experiment tracking.

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

`health doctor` prints what each source still needs, so it is the thing to run
when something is not working.

### Hevy — two minutes

Hevy Pro only. Copy the key from `hevy.com/settings?developer`, then:

```sh
security add-generic-password -a health -s HEVY_API_KEY -w 'your-key'
health doctor          # should report your workout count
health sync hevy       # pages the whole history, then follows the events feed
```

### WHOOP — ten minutes, one-time OAuth

1. Sign in at [developer.whoop.com](https://developer.whoop.com) with your
   WHOOP account and create an app (free; needs an active membership).
2. Set the redirect URI to **exactly** `http://localhost:8765/callback`.
   Character for character, port and path included — a mismatch is the usual
   cause of a `redirect_uri` error, and it is the most common thing to get
   wrong here.
3. Request these scopes: `read:recovery read:cycles read:sleep read:workout
   read:profile read:body_measurement offline`. The last one is what gets you
   a refresh token; without it the connection dies after an hour.
4. Store the credentials and run the consent flow:

```sh
security add-generic-password -a health -s WHOOP_CLIENT_ID -w 'your-id'
security add-generic-password -a health -s WHOOP_CLIENT_SECRET -w 'your-secret'
health auth whoop      # opens the browser; approve; tokens land 0600 on disk
health sync whoop      # backfills two years by default, --since for more
```

`health auth whoop` starts a one-shot web server on port 8765 purely to catch
the redirect, checks the OAuth `state` matches, and shuts down. After that,
tokens refresh themselves and you should not need to do this again.

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

PDF reports from UK panel providers work directly:

```sh
health labs add report.pdf
```

The extracted text is what gets stored, not our reading of it, so a better line
parser later re-reads reports you added months ago. Two things in those reports
are easy to get backwards and are handled explicitly: a single printed
reference bound is a ceiling beside cholesterol and a floor beside eGFR, so it
is placed by the lab's own L/H marking where there is one and by the analyte's
known direction otherwise — never guessed; and the report's stated biological
sex selects the generic ranges, since 240 ug/L of ferritin is unremarkable on a
male range and flagged high on a female one.

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

## The agent

```sh
claude mcp add health -- /path/to/.venv/bin/health mcp --root /path/to/project
```

That exposes 19 tools — baselines, deviations, correlations, training load,
lift progression, cycle phase, blood results, literature search, and a daily
brief that pulls them together. Then you can just ask:

> *why has my sleep been bad since August?*
> *am I actually getting stronger on RDLs, or just adding reps?*
> *what does the literature say about my GGT?*

The design rule is that **the model never queries the database freely, never
receives a raw dump, and never does arithmetic**. Python computes; the model
interprets. Every tool returns its own uncertainty alongside its answer —
sample sizes, confidence intervals, whether a baseline was usable at all —
because a number handed to a language model without its sample size comes back
to you stated with total confidence.

Failures come back as data rather than exceptions, too: an empty database
returns "run `health init` first" as a readable message, because the MCP SDK
strips exception text on the way out and a silent failure is exactly when the
model starts guessing.

## Asking it things

```sh
health sql "SELECT local_date, hrv, resting_hr, recovery, sleep_min FROM daily
            ORDER BY local_date DESC LIMIT 14"
```

There is a statistics layer under those tools worth knowing about. Baselines
are **robust** — median and MAD, not mean and standard deviation — so a
fortnight of illness does not redefine normal, and the day being judged is
excluded from its own baseline. Correlations are **corrected for
autocorrelation**: health series are strongly serially correlated, so 90 days
of data does not contain 90 independent observations, and treating it as
though it does turns ordinary noise into confident findings. `correlate`
reports the effective sample size and widens its interval accordingly.

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
