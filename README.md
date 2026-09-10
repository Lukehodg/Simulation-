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
| **Readiness & drivers** | A readiness-to-train call, personal sleep debt, what actually moves your recovery (confounder-aware), strength vs. recovery, a phase-aware month |
| **Drift & sleep** | Six-week trend detection (is the baseline itself moving), sleep architecture vs. your own baseline, an illness early-warning from resting HR + respiration + skin temp |
| **Protocol** | Compounds you're on as an axis of interpretation — documented effects, monitoring markers, pre-panel checklist; escalation, never dosing |
| **Brief** | `health brief` — the day (or the week) read back to you in two paragraphs, on a schedule if you want it |
| **Agent tools** | 29 MCP tools — the surface Claude actually talks to |
| **Interface** | A local page on 127.0.0.1, served from the same database |
| **Bloods page** | Drop a report in, see what is flagged, ask Claude to read it |
| **Indicators** | Per-domain status with the evidence behind each — deliberately no single score |
| **Training observations** | Stalled, dormant, progressing and imbalanced, computed from your sets |

Next: the Garmin export and `.FIT` parsers, energy availability (which needs
Garmin's expenditure data), and n-of-1 experiment tracking.

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

Already have an export? Skip the key entirely:

```sh
health ingest workout_data.csv --source hevy
```

The app's CSV export is one row per set. It lands verbatim, so a better parser
later re-reads the original file. Two things in that format are easy to get
wrong and are handled: timestamps are local wall time rather than UTC, and a
workout that returns to an exercise later restarts its set numbering — keying
the index on the exercise name files the second block over the first and loses
it silently.

The CSV carries no exercise template ids, so muscle groups stay empty until an
API sync brings the catalogue in; the volume view then matches them by name.


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

## Protocol

If you are on something — testosterone, a GLP-1 agonist, an aromatase inhibitor
— the system reads your numbers differently once it knows. Log it:

```sh
health protocol add testosterone --dose 200 --unit mg --freq weekly --from 2026-09-08
health protocol add retatrutide  --dose 2   --unit mg --freq weekly --from 2026-09-08
health protocol                          # what's active, weeks on, what to watch
health protocol stop retatrutide --on 2026-12-01
```

`compounds.py` is a small curated vocabulary — the same idea as `analytes.py` —
holding each compound's *documented* effects on the metrics and bloods this
system tracks, and the markers a clinician should watch. From that:

- **Context.** A resting heart rate that is up a few bpm on a GLP-1 agonist is
  read as expected rather than alarming; the elevation is adjusted for before
  the illness-watch judges it. A metric moving the *opposite* way to what a
  compound predicts is flagged as the informative case.
- **Strength.** Every strength trend is caveated as compound-plus-training, and
  a stall on testosterone is called out as meaning more than it would otherwise.
- **Bloods.** A low HDL or a suppressed LH on testosterone is annotated as
  expected and still flagged; a trend spanning the start of a compound is
  refused as not like-for-like; and the next panel gets a checklist of what to
  include (a full blood count and lipids on testosterone, HbA1c and
  triglycerides on a GLP-1).
- **Escalation, not management.** A rising haematocrit, a poor lipid picture, a
  climbing resting HR — these go to "worth a doctor's review". The system holds
  a hard line: it reports documented effects and never comments on the dose,
  ancillary drugs, or PCT.

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

## Keeping it running

```sh
health schedule --install              # sync at 07:15 and 19:15 daily
health schedule --brief                # also write a brief at 07:45
health schedule                        # is it loaded, and what did the last run say
health backup --to ~/Dropbox/health    # archive raw/
```

Everything here assumes data keeps arriving, which it does not unless
something runs `health sync`. `schedule --install` writes a launchd agent
pointed at your own interpreter and project — launchd has almost no PATH, so a
bare `health` would not resolve — and sends output to `data/logs/sync.log`, so
a sync that has been quietly failing for a fortnight is visible rather than
assumed. A scheduled time that passes while the lid is shut runs on wake
instead of being skipped. On anything that is not macOS it prints the cron line
instead.

`--brief` adds a second agent that runs `health brief --save` after the morning
sync, logging to `data/logs/brief.log`. It is only installed if
`ANTHROPIC_API_KEY` is set — the brief has nothing to do without it —
and `health schedule --no-brief` removes it.

Two syncs cannot run at once: DuckDB gives the file to a single writer, so a
scheduled run that collides with a manual one now stops with a sentence rather
than a lock error that looks like a bug.

**Backups archive `raw/`, not the database.** The database is disposable —
`health replay` rebuilds every table from raw payloads. What cannot be rebuilt
is the payloads: WHOOP will not serve two-year-old records forever, Garmin
exports are manual and rate-limited, and a lab report deleted from your
downloads is gone. Archives carry a manifest describing what is inside them,
restores refuse to merge into a non-empty `raw/` unless forced, and every path
in an archive is checked before extraction — a tar file is a list of paths
someone else wrote, and `../` is a legal one.

## The interface

```sh
health serve          # opens http://127.0.0.1:8899
```

One page: the day's verdict, a readout row with each metric's deviation from
its own baseline, HRV against its baseline band with the current cycle phase
shaded, strength trends with their fit, and whatever your last blood panel
flagged.

It is deliberately a readout rather than a dashboard — rules instead of cards,
monospaced figures aligned so a column reads at a glance, and uncertainty
printed next to the figure it qualifies rather than hidden behind a tooltip.
The verdict line describes what the numbers did; it does not tell you what to
do about it, because that is not a judgement this data can make on its own.

Before anything is connected it renders a setup checklist with the exact
command for each source, since an empty database is the first thing you will
see. Each readout cell does the same individually — a missing metric shows
what to run rather than a dash.

Standard library only, bound to loopback, opened per request: holding the
database open would block `health sync` in another terminal, and when a sync
does hold the file the page says it is busy rather than leaking a lock error.

### Bloods

`health serve` also serves `/bloods`. Drop a PDF report onto the page and it
parses, converts, flags and stores it — the same path `health labs add` takes,
so `raw/` still explains everything in the tables.

What is out of range sits at the top on its own, with the caveat that belongs
to it ("a raised GGT is a prompt to look, not a diagnosis"). Everything in
range is one click away rather than thirty-seven rows of scrolling between you
and the reason you opened the page.

Then there is a button that asks Claude to read the panel — and **this is the
only thing in the system that leaves your machine**. So the boundary is drawn
narrowly and shown before you cross it:

- Only parsed values go: analyte, number, unit, reference range, whose range it
  was, the flag, previous values, and the cycle phase a draw was taken in where
  that changes the reading.
- The report never goes. Not the PDF, not the extracted text, not your name,
  date of birth, order number or the lab's address — none of it is in the
  payload, because the parser never stored it.
- **See exactly what would be sent** prints the payload on the page before you
  press anything, and a test asserts nothing identifying survives into it.
- No key, no request. Nothing is sent until you press the button.

What comes back is an interpretation with citations — what stands out, what
could explain it, what the literature says, what to ask your doctor, and what
it cannot tell you. It runs under a system prompt that forbids diagnosis and
dosing, requires it to say where a reference range came from, and tells it to
say plainly when evidence is thin. The papers it was given to read are listed
underneath, with their study designs, so you can check its homework.

```sh
security add-generic-password -a health -s ANTHROPIC_API_KEY -w 'sk-ant-...'
```

## Indicators, and why there is no health score

A single composite score needs weights across incommensurable things — how many
milliseconds of HRV is one unit of GGT worth? There is no non-arbitrary answer,
no outcome such a number has been validated against, and no way to trace why it
moved. What it reliably produces is a figure people optimise instead of the
thing it stands for.

So the bloods page shows several indicators instead, each interpretable on its
own and each carrying the strength of the evidence behind it:

```
Recovery       · HRV 71.7, +0.5 SD; resting HR 51.9, -0.6 SD    ok        · strong evidence
Training load  · steady: last 7 days averaging 11 against 10    ok        · moderate evidence
Liver          · GGT 114.2 U/L (high, range 10-71)              attention · strong evidence
Lipids         · HDL cholesterol 1.06 mmol/L (low, range >1.55) attention · strong evidence
```

A laboratory's own reference interval is the strongest evidence in the system;
a generic population range is weaker and is marked as such; the acute:chronic
workload ratio is widely used and weakly evidenced, so it says moderate. The
one figure that does aggregate is **completeness** — what fraction of the
relevant data actually exists — which is a question with a real answer.

The absence of a total is passed to the model explicitly, so it does not
helpfully invent one.

## Training observations

Computed from your logged sets, not generated: which lifts have stopped moving
(flat trend at a poor fit, over at least six sessions), which you have quietly
stopped doing, where the weekly volume actually goes, and how fast load is
ramping. Each carries the arithmetic it came from.

Training changes are the one kind of recommendation this system makes freely —
rep ranges are not medicine, the feedback loop is short, and the cost of being
wrong is a mediocre eight weeks.

**Supplements are handled differently.** The analysis will tell you what the
trials in its sources actually tested, what doses they used, and what is
contested — attributed, and only for an analyte that is genuinely out of range.
It will not tell you what to take. Interactions and contraindications depend on
your medications and history, which is exactly what it cannot see.

## Bloods against your metrics

Each panel is stored with what your wearables were doing in the 28 days before
the draw, so a blood test is read in the conditions it arrived in rather than
as a number in isolation. With one panel that is context, not correlation —
one draw is a point, not a direction, and the page says so. A second panel
turns it into a comparison, and `panel_changes` reports what moved in both the
bloods and the metrics, with cycle-sensitive analytes flagged as comparable
only within the same phase.

## The brief

```sh
health brief                     # the day, in two paragraphs
health brief --week              # the week, plus its most interesting pattern
health brief --ask "why has training felt hard this week?"
health brief --no-send           # print exactly what would be sent, and stop
health brief --notify            # also text it to yourself over iMessage
```

Where the rest of the system hands you numbers, this hands you a reading of
them. It assembles the computed features — deviations from baseline, the
readiness call, sleep debt, training load, cycle phase, and for the weekly pass
the cross-domain passes below — into a small labelled payload, and Claude
writes back what the body is saying and what to do about training today. It
never sees a raw series and never does arithmetic; `--no-send` shows you the
exact payload first, the same contract as the bloods page. Needs
`ANTHROPIC_API_KEY`. `health schedule --brief` runs it every morning and drops
the result in `data/briefs/`.

`--notify` also texts it to you. It sends over iMessage via `osascript` — a
message to yourself — rather than a push service, so the brief's numbers travel
the same iMessage/iCloud path that already carries your Health data, with no
third-party account. Set `HEALTH_NOTIFY_IMESSAGE` to your own number or Apple
ID; the first run triggers a one-time macOS Automation permission prompt.
`health schedule --brief --notify` bakes it into the morning job.

The signals it is built on are also callable directly, on the CLI and through
the agent:

- **`readiness`** — one call on today's training (push / proceed / hold / pull
  back) from recovery, load, sleep debt and cycle phase together, with every
  reason and its number attached. Deliberately not a 0–100 score, for the same
  reason there is no overall health score.
- **`recovery_drivers`** — which of *your own* behaviours actually move your HRV
  and resting heart rate, over 90 days, with the autocorrelation-corrected
  intervals `correlate` uses and only the relationships whose interval clears
  zero. It is a hypothesis generator and says so.
- **`strength_recovery_link`** — whether your best sessions land on your
  best-recovered days, per lift, as a residual from each lift's own trend, with
  the sample size on every bucket.
- **`phase_training_plan`** — where the heavy blocks and the deloads should fall
  across the next few weeks, checked against your own phase signature rather
  than the textbook. Says so plainly when there are no period logs yet.

## The agent

```sh
claude mcp add health -- /path/to/.venv/bin/health mcp --root /path/to/project
```

That exposes 29 tools — baselines, deviations, correlations, training load,
lift progression, cycle phase, blood results, literature search, the readiness
call and its cross-domain siblings, and a daily brief that pulls them together.
Then you can just ask:

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
