"""What the compounds someone is on are known to do to the numbers this system
measures.

The parallel is `analytes.py`: a small, curated vocabulary with documented
effects, at the same evidential bar. It exists so that a resting heart rate
that is up because of a GLP-1 agonist is read as *expected*, a strength trend
on exogenous testosterone is read as *compound plus training*, and a marker
moving the opposite way to what a compound predicts is flagged as the
interesting case it is.

What this module is NOT: dosing guidance, cycle design, ancillary-drug or PCT
advice. `monitor` entries name a marker and why it matters; the correct
response to one moving is "a doctor should see this", never a protocol change.
Nothing here, and nothing that reads it, tells anyone what to take or how much.

`raises` / `lowers` hold canonical metric names (see `metrics.py`) and analyte
keys (see `analytes.py`) — the same keys the rest of the system uses, so the
effect can be checked against the person's own series.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STRONG = "strong"        # consistent RCT / pharmacology
MODERATE = "moderate"    # consistent observational, mechanism clear
WEAK = "weak"            # plausible, contested, or sparse


@dataclass(frozen=True)
class Compound:
    key: str
    label: str
    klass: str                       # androgen | glp1 | ai | gonadotropin
    #: canonical metric / analyte keys this pushes up and down
    raises: tuple[str, ...] = ()
    lowers: tuple[str, ...] = ()
    #: strength / lean-mass gain is partly the compound, not the training
    strength_confound: bool = False
    #: resting HR bump to expect, bpm (low, high) — subtracted before judging
    resting_hr_offset_bpm: tuple[float, float] | None = None
    #: slow markers (blood counts, lipids) settle over about this many weeks
    onset_weeks: float = 6.0
    #: (marker, why it matters) — escalated to a clinician, never acted on here
    monitor: tuple[tuple[str, str], ...] = ()
    #: analytes a panel taken while on this should be sure to include
    pre_panel: tuple[str, ...] = ()
    note: str = ""
    evidence: str = MODERATE

    def effect_on(self, key: str) -> str | None:
        if key in self.raises:
            return "raises"
        if key in self.lowers:
            return "lowers"
        return None


COMPOUNDS: dict[str, Compound] = {c.key: c for c in [
    Compound(
        key="testosterone", label="Testosterone (exogenous)", klass="androgen",
        raises=("haematocrit", "haemoglobin", "rbc", "oestradiol", "ldl",
                "resting_hr", "creatinine", "hba1c"),
        lowers=("hdl", "shbg", "lh", "fsh", "hrv_rmssd"),
        strength_confound=True,
        resting_hr_offset_bpm=(0.0, 3.0),
        onset_weeks=10.0,
        monitor=(
            ("haematocrit", "supraphysiologic androgens raise red cell mass; a "
                            "rising haematocrit is the classic reason to involve "
                            "a doctor"),
            ("blood_pressure", "androgens and the fluid shift can raise it; it is "
                               "not measured here, so measure it"),
            ("hdl", "typically falls; the cardiovascular picture is worth a "
                    "clinician's read alongside the rest of the lipid panel"),
            ("oestradiol", "rises with aromatisation; symptomatic only a doctor "
                           "should interpret"),
        ),
        pre_panel=("haematocrit", "haemoglobin", "rbc", "hdl", "ldl",
                   "cholesterol", "triglycerides", "oestradiol", "psa", "alt",
                   "ast", "shbg", "lh", "fsh"),
        note="Exogenous testosterone suppresses the HPG axis (LH/FSH → ~0), "
             "raises red cell mass and oestradiol, lowers HDL, and adds lean "
             "mass and strength independent of the training stimulus. Effects "
             "on blood counts and lipids build over roughly 8–12 weeks.",
        evidence=STRONG,
    ),
    Compound(
        key="retatrutide", label="Retatrutide", klass="glp1",
        raises=("resting_hr",),
        lowers=("body_mass", "energy_intake", "hba1c", "glucose",
                "triglycerides", "appetite"),
        resting_hr_offset_bpm=(2.0, 5.0),
        onset_weeks=4.0,
        monitor=(
            ("resting_hr", "GLP-1/GIP/glucagon agonists raise resting heart rate "
                           "a few bpm; a larger or climbing rise is worth "
                           "raising with a doctor"),
            ("energy_availability", "appetite suppression plus a heavy training "
                                    "load can drop energy availability far "
                                    "enough to matter — track weight loss rate "
                                    "and intake"),
            ("heart_rate", "palpitations or a resting HR well above the expected "
                           "few-bpm rise should be assessed"),
        ),
        pre_panel=("hba1c", "glucose", "triglycerides", "alt", "ast",
                   "creatinine", "egfr"),
        note="A GIP/GLP-1/glucagon triple agonist (trial-stage). Suppresses "
             "appetite and drives weight loss; raises resting heart rate ~2–5 "
             "bpm as a class effect; improves glycaemia and triglycerides. GI "
             "effects and dehydration add day-to-day HRV noise. Rapid weight "
             "loss alongside high training load is the combination to watch.",
        evidence=MODERATE,
    ),
    Compound(
        key="nandrolone", label="Nandrolone", klass="androgen",
        raises=("haematocrit", "haemoglobin", "rbc", "prolactin"),
        lowers=("hdl", "shbg", "lh", "fsh", "hrv_rmssd"),
        strength_confound=True, onset_weeks=10.0,
        monitor=(("haematocrit", "as for any androgen — a rising value needs a "
                                 "doctor"),
                 ("prolactin", "nandrolone can raise it; symptomatic elevation "
                               "is a clinical question")),
        pre_panel=("haematocrit", "hdl", "prolactin", "oestradiol"),
        note="19-nor androgen; androgenic blood-count and lipid effects as "
             "testosterone, plus a prolactin effect. Progestogenic activity.",
        evidence=MODERATE,
    ),
    Compound(
        key="trenbolone", label="Trenbolone", klass="androgen",
        raises=("resting_hr", "prolactin"),
        lowers=("hdl", "shbg", "lh", "fsh", "hrv_rmssd", "sleep_efficiency"),
        strength_confound=True, resting_hr_offset_bpm=(2.0, 8.0), onset_weeks=8.0,
        monitor=(("hdl", "trenbolone suppresses HDL markedly"),
                 ("resting_hr", "night sweats, a raised resting HR and disturbed "
                                "sleep are commonly reported; persistent or "
                                "severe warrants review"),
                 ("kidney_function", "case reports of renal strain — keep "
                                     "creatinine/eGFR on the panel")),
        pre_panel=("hdl", "ldl", "creatinine", "egfr", "prolactin"),
        note="Potent 19-nor androgen. Strong lipid suppression, frequently "
             "disturbed sleep and elevated resting HR, no aromatisation.",
        evidence=WEAK,
    ),
    Compound(
        key="oxandrolone", label="Oxandrolone", klass="androgen",
        raises=("alt", "ast"),
        lowers=("hdl", "shbg", "lh", "fsh"),
        strength_confound=True, onset_weeks=6.0,
        monitor=(("hdl", "oral 17-aa androgens suppress HDL sharply"),
                 ("alt", "17-alpha-alkylated — liver enzymes can rise; a "
                         "clinician should read a rise")),
        pre_panel=("hdl", "ldl", "alt", "ast", "ggt", "bilirubin"),
        note="Oral 17-aa androgen. Marked HDL suppression and potential hepatic "
             "enzyme rise; little aromatisation or water retention.",
        evidence=MODERATE,
    ),
    Compound(
        key="anastrozole", label="Anastrozole", klass="ai",
        raises=("shbg",), lowers=("oestradiol", "hdl"),
        onset_weeks=2.0,
        monitor=(("oestradiol", "aromatase inhibition can drive oestradiol too "
                                "low — joint pain, low libido, poor lipids and "
                                "bone loss follow; dosing is a clinical matter"),),
        pre_panel=("oestradiol", "hdl", "ldl"),
        note="Aromatase inhibitor: lowers oestradiol. Over-suppression carries "
             "its own harms, so both the level and the symptoms are a "
             "clinician's call.",
        evidence=STRONG,
    ),
    Compound(
        key="hcg", label="hCG", klass="gonadotropin",
        raises=("oestradiol", "testosterone"),
        onset_weeks=3.0,
        monitor=(("oestradiol", "hCG stimulates testicular oestradiol as well as "
                                "testosterone"),),
        pre_panel=("testosterone", "oestradiol"),
        note="Luteinising-hormone analogue; maintains testicular function and "
             "intratesticular testosterone during androgen use.",
        evidence=MODERATE,
    ),
    Compound(
        key="glp1", label="GLP-1 agonist (general)", klass="glp1",
        raises=("resting_hr",),
        lowers=("body_mass", "energy_intake", "hba1c", "glucose",
                "triglycerides", "appetite"),
        resting_hr_offset_bpm=(2.0, 4.0), onset_weeks=4.0,
        monitor=(("resting_hr", "small class-effect rise; a larger one is worth "
                                "review"),
                 ("energy_availability", "appetite suppression plus training "
                                         "load — watch intake and weight loss "
                                         "rate")),
        pre_panel=("hba1c", "glucose", "triglycerides"),
        note="Semaglutide, tirzepatide and similar. Appetite suppression and "
             "weight loss, improved glycaemia and triglycerides, a few-bpm "
             "resting-HR rise.",
        evidence=STRONG,
    ),
]}

ALIASES: dict[str, str] = {
    "test": "testosterone", "testosterone": "testosterone", "test e": "testosterone",
    "test c": "testosterone", "testosterone enanthate": "testosterone",
    "testosterone cypionate": "testosterone", "trt": "testosterone",
    "reta": "retatrutide", "retatrutide": "retatrutide", "lly 3437943": "retatrutide",
    "deca": "nandrolone", "nandrolone": "nandrolone", "npp": "nandrolone",
    "nandrolone decanoate": "nandrolone",
    "tren": "trenbolone", "trenbolone": "trenbolone", "tren a": "trenbolone",
    "tren e": "trenbolone",
    "var": "oxandrolone", "anavar": "oxandrolone", "oxandrolone": "oxandrolone",
    "adex": "anastrozole", "arimidex": "anastrozole", "anastrozole": "anastrozole",
    "ai": "anastrozole",
    "hcg": "hcg", "human chorionic gonadotropin": "hcg",
    "sema": "glp1", "semaglutide": "glp1", "ozempic": "glp1", "wegovy": "glp1",
    "tirz": "glp1", "tirzepatide": "glp1", "mounjaro": "glp1", "glp1": "glp1",
    "glp-1": "glp1",
}


def canonical(raw: str) -> str | None:
    """Map a name onto the vocabulary, or None if it is not known yet."""
    name = " ".join(raw.lower().split())
    if name in ALIASES:
        return ALIASES[name]
    key = name.replace(" ", "_").replace("-", "_")
    return key if key in COMPOUNDS else None


def expected_direction(key: str, active: list[str]) -> str | None:
    """Which way the active compounds, taken together, predict `key` moves."""
    votes = {c.effect_on(key) for c in
             (COMPOUNDS[a] for a in active if a in COMPOUNDS)}
    votes.discard(None)
    if votes == {"raises"}:
        return "raises"
    if votes == {"lowers"}:
        return "lowers"
    return None    # nothing, or a genuine disagreement between compounds
