"""The brief as a picture, for the notification itself.

`briefpage.py` draws the brief as an HTML page, which is the better read but
lives on the machine that wrote it. A phone gets the notification: 1,024
characters of text and, on Pushover, one attached image. This is that image —
the four weeks behind today's call, sized for a phone held upright.

It reads the database for the series and the features for the lines drawn over
them (the personal baseline band, the protein target), and does no arithmetic
of its own beyond laying them out. Each panel is one series against one axis;
a metric with too little data is left out rather than drawn from three points.

Chart conventions follow one palette and one set of mark specs: a single blue
for every series (one series per panel needs no legend — the title names it),
the baseline band as a 10% wash of that blue, 2px lines, hairline gridlines,
and status colours only on the readiness call, always beside its word.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import metrics as M
from .features import daily
from .store import Store

#: How far back each panel looks.
DAYS = 28
#: A panel needs at least this many readings to be worth drawing.
MIN_POINTS = 4

#: The same baseline convention readiness uses: "normal" is within one robust
#: standard deviation of the 28-day median.
_MAD_TO_SD = daily.MAD_TO_SD


@dataclass(frozen=True)
class Theme:
    surface: str
    text: str
    muted: str
    grid: str
    series: str


#: Both steps validated against their own surface (contrast >= 3:1).
THEMES = {
    "dark": Theme(surface="#1a1a19", text="#ffffff", muted="#c3c2b7",
                  grid="#2c2c2a", series="#3987e5"),
    "light": Theme(surface="#fcfcfb", text="#0b0b0b", muted="#52514e",
                   grid="#e6e5e1", series="#2a78d6"),
}

#: Reserved status colours, only ever shown beside the call's own word.
_STATUS = {"push": "#0ca30c", "proceed": "#0ca30c", "hold": "#fab219",
           "pull_back": "#d03b3b", "insufficient_data": None}
_CALL_WORDS = {"push": "Push", "proceed": "Proceed", "hold": "Hold",
               "pull_back": "Pull back", "insufficient_data": "Not enough data"}


@dataclass
class Panel:
    title: str
    unit: str
    points: list[tuple[date, float]]
    kind: str = "line"                 # line | bar
    band: tuple[float, float] | None = None
    target: float | None = None
    target_label: str | None = None
    note: str | None = None
    decimals: int = 0


def _series(store: Store, metric: str, day: date) -> list[tuple[date, float]]:
    return [(d, float(v)) for d, v in
            daily.series(store, metric, day - timedelta(days=DAYS - 1), day)
            if v is not None]


def _band(store: Store, metric: str, day: date) -> tuple[float, float] | None:
    base = daily.baseline(store, metric, as_of=day)
    if not base.usable:
        return None
    spread = base.mad * _MAD_TO_SD
    return base.median - spread, base.median + spread


def panels(store: Store, day: date) -> list[Panel]:
    """What the card will draw, in order, with nothing drawn from thin data."""
    from .features import program

    out: list[Panel] = []

    for metric, title, unit in ((M.HRV_RMSSD, "HRV", "ms"),
                                (M.RESTING_HR, "Resting heart rate", "bpm")):
        pts = _series(store, metric, day)
        if len(pts) >= MIN_POINTS:
            out.append(Panel(title, unit, pts, band=_band(store, metric, day),
                             note="shaded: your normal range"))

    sleep = [(d, v / 60) for d, v in _series(store, M.SLEEP_DURATION, day)]
    if len(sleep) >= MIN_POINTS:
        out.append(Panel("Sleep", "h", sleep, kind="bar", decimals=1))

    weight = _series(store, M.BODY_MASS, day)
    if len(weight) >= MIN_POINTS:
        trend, _latest = program.weight_trend(store, day)
        note = None
        if trend is not None:
            ceiling = weight[-1][1] * program.LOSS_BAND_PCT[1] / 100
            direction = "losing" if trend < 0 else "gaining"
            note = (f"{direction} {abs(trend):.2f} kg/wk · loss ceiling "
                    f"{ceiling:.2f} kg/wk")
        out.append(Panel("Bodyweight", "kg", weight, decimals=1, note=note))

    protein = _series(store, M.PROTEIN, day)
    if len(protein) >= MIN_POINTS:
        target = program.protein_target(store, day).target_g
        out.append(Panel("Protein", "g", protein, kind="bar", target=target,
                         target_label=f"target {target} g" if target else None))

    return out


def _style_axis(ax, theme: Theme) -> None:
    ax.set_facecolor(theme.surface)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.grid)
    ax.spines["bottom"].set_linewidth(1)
    ax.grid(axis="y", color=theme.grid, linewidth=1)
    ax.set_axisbelow(True)
    ax.tick_params(colors=theme.muted, labelsize=9, length=0, pad=4)


def _draw(ax, panel: Panel, theme: Theme, day: date) -> None:
    import matplotlib.dates as mdates
    from matplotlib.ticker import MaxNLocator

    dates = [d for d, _ in panel.points]
    values = [v for _, v in panel.points]
    _style_axis(ax, theme)

    if panel.band:
        lo, hi = panel.band
        ax.axhspan(lo, hi, color=theme.series, alpha=0.10, linewidth=0, zorder=0)

    if panel.kind == "bar":
        ax.bar(dates, values, width=0.7, color=theme.series, zorder=2)
    else:
        ax.plot(dates, values, color=theme.series, linewidth=2,
                solid_capstyle="round", solid_joinstyle="round", zorder=2)
        ax.plot(dates[-1:], values[-1:], "o", markersize=8, color=theme.series,
                markeredgecolor=theme.surface, markeredgewidth=2, zorder=3,
                clip_on=False)

    if panel.target:
        ax.axhline(panel.target, color=theme.muted, linewidth=1, zorder=1)
        if panel.target_label:
            ax.annotate(panel.target_label, xy=(dates[0], panel.target),
                        xytext=(0, 3), textcoords="offset points",
                        color=theme.muted, fontsize=8, va="bottom")

    # Title left, the latest value right: the one number the panel is about.
    ax.set_title(panel.title, loc="left", color=theme.text, fontsize=11,
                 fontweight="bold", pad=6)
    latest = f"{values[-1]:.{panel.decimals}f} {panel.unit}"
    ax.set_title(latest, loc="right", color=theme.text, fontsize=11, pad=6)
    if panel.note:
        ax.text(0, -0.30, panel.note, transform=ax.transAxes, color=theme.muted,
                fontsize=8, va="top")

    # A `date` plus half a day is the same date, so the limits are datetimes:
    # half a day either side keeps the first and last bar whole.
    midnight = datetime.combine(day, datetime.min.time())
    ax.set_xlim(midnight - timedelta(days=DAYS - 0.5), midnight + timedelta(days=0.5))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.yaxis.set_major_locator(MaxNLocator(3, integer=panel.decimals == 0))

    if panel.kind == "bar":
        ax.set_ylim(0, max(values + [panel.target or 0]) * 1.12)
    else:
        lows = values + ([panel.band[0]] if panel.band else [])
        highs = values + ([panel.band[1]] if panel.band else [])
        pad = (max(highs) - min(lows)) * 0.15 or 1
        ax.set_ylim(min(lows) - pad, max(highs) + pad)


def render_png(store: Store, day: date | None = None, *,
               theme: str = "dark") -> bytes | None:
    """The card as PNG bytes, or None when there is nothing worth drawing.

    None rather than an empty card, so a notification with no data behind it
    goes out as text alone instead of carrying a blank picture.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .features import readiness as readiness_features

    day = day or date.today()
    colours = THEMES[theme]
    drawn = panels(store, day)
    if not drawn:
        return None

    call = readiness_features.readiness(store, day).recommendation
    height = 1.7 + 1.75 * len(drawn)
    fig = plt.figure(figsize=(5.4, height), dpi=200, facecolor=colours.surface)
    top = 1 - 1.55 / height
    grid = fig.add_gridspec(len(drawn), 1, left=0.10, right=0.95,
                            top=top, bottom=0.45 / height, hspace=0.95)

    fig.text(0.05, 1 - 0.38 / height, f"{day:%A %d %B}", color=colours.muted,
             fontsize=10, va="center")
    status = _STATUS.get(call)
    word = _CALL_WORDS.get(call, call.replace("_", " "))
    y = 1 - 0.80 / height
    x = 0.05
    if status:
        # The colour sits beside the word, never instead of it.
        fig.text(x, y, "\u25CF", color=status, fontsize=18, va="center")
        x += 0.055
    fig.text(x, y, f"Today: {word}", color=colours.text,
             fontsize=16, fontweight="bold", va="center")

    for i, panel in enumerate(drawn):
        _draw(fig.add_subplot(grid[i]), panel, colours, day)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=colours.surface)
    plt.close(fig)
    return buffer.getvalue()
