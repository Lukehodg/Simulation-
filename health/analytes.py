"""Blood test vocabulary, units and reference ranges.

Three things make lab data hard to store honestly, and all three are handled
here so nothing downstream has to think about them:

*Names.* Every lab writes the same analyte differently — "Vitamin D (25-OH)",
"25-hydroxyvitamin D", "VITD". They are canonicalised on the way in.

*Units.* A UK lab reports glucose in mmol/L and an American paper discusses
mg/dL. Comparing a number to a reference range or a study without converting
is the single easiest way to reach a confidently wrong conclusion, so values
and their ranges are converted together into one canonical unit per analyte.

*Reference ranges.* These are properties of the assay and the population, not
of you — they differ between labs, and for many analytes between sexes and
ages. The lab's own range always wins. The fallbacks here exist so that a
result pasted without one can still be placed, they are marked as generic
wherever they are used, and they are adult female ranges: change
REFERENCE_PROFILE if that is not you.
"""

from __future__ import annotations

from dataclasses import dataclass

REFERENCE_PROFILE = "adult_female"


@dataclass(frozen=True)
class Analyte:
    key: str
    label: str
    unit: str
    group: str
    #: Generic reference interval. None where the useful bound is one-sided or
    #: too assay-dependent to state.
    ref_low: float | None = None
    ref_high: float | None = None
    #: Set where a result is meaningfully affected by where you are in your
    #: cycle, so draws taken in different phases are not compared blindly.
    cycle_sensitive: bool = False
    note: str | None = None


ANALYTES: dict[str, Analyte] = {a.key: a for a in [
    # Iron studies — the group most worth tracking alongside a cycle
    Analyte("ferritin", "Ferritin", "ug/L", "iron", 15, 200, cycle_sensitive=True,
            note="an acute-phase reactant: rises with inflammation, so a normal "
                 "ferritin alongside a raised CRP can still mask iron deficiency"),
    Analyte("haemoglobin", "Haemoglobin", "g/L", "iron", 120, 150, cycle_sensitive=True),
    Analyte("iron", "Serum iron", "umol/L", "iron", 10, 30, cycle_sensitive=True),
    Analyte("transferrin_saturation", "Transferrin saturation", "%", "iron", 20, 50),
    Analyte("b12", "Vitamin B12", "pmol/L", "vitamins", 180, 900),
    Analyte("folate", "Folate", "nmol/L", "vitamins", 7, None),
    Analyte("vitamin_d", "Vitamin D (25-OH)", "nmol/L", "vitamins", 50, 125),

    # Thyroid
    Analyte("tsh", "TSH", "mIU/L", "thyroid", 0.4, 4.0),
    Analyte("free_t4", "Free T4", "pmol/L", "thyroid", 12, 22),
    Analyte("free_t3", "Free T3", "pmol/L", "thyroid", 3.1, 6.8),

    # Metabolic
    Analyte("hba1c", "HbA1c", "mmol/mol", "metabolic", None, 41),
    Analyte("glucose", "Fasting glucose", "mmol/L", "metabolic", 3.9, 5.5),
    Analyte("insulin", "Fasting insulin", "pmol/L", "metabolic", 18, 173),

    # Lipids
    Analyte("cholesterol", "Total cholesterol", "mmol/L", "lipids", None, 5.0),
    Analyte("ldl", "LDL cholesterol", "mmol/L", "lipids", None, 3.0),
    Analyte("hdl", "HDL cholesterol", "mmol/L", "lipids", 1.2, None),
    Analyte("triglycerides", "Triglycerides", "mmol/L", "lipids", None, 1.7),

    # Inflammation, liver, kidney
    Analyte("crp", "CRP", "mg/L", "inflammation", None, 5),
    Analyte("alt", "ALT", "U/L", "liver", None, 33),
    Analyte("ast", "AST", "U/L", "liver", None, 32),
    Analyte("creatinine", "Creatinine", "umol/L", "kidney", 45, 90),
    Analyte("egfr", "eGFR", "mL/min/1.73m2", "kidney", 90, None),

    # Hormones — every one of these is meaningless without the cycle day
    Analyte("oestradiol", "Oestradiol", "pmol/L", "hormones", cycle_sensitive=True,
            note="varies several-fold across the cycle; only comparable between "
                 "draws taken in the same phase"),
    Analyte("progesterone", "Progesterone", "nmol/L", "hormones", cycle_sensitive=True,
            note="the mid-luteal value is the one that means anything; a "
                 "follicular draw is expected to be low"),
    Analyte("lh", "LH", "IU/L", "hormones", cycle_sensitive=True),
    Analyte("fsh", "FSH", "IU/L", "hormones", cycle_sensitive=True),
    Analyte("testosterone", "Testosterone", "nmol/L", "hormones", 0.3, 1.7),
    Analyte("shbg", "SHBG", "nmol/L", "hormones", 30, 120),
    Analyte("prolactin", "Prolactin", "mIU/L", "hormones", 100, 500),
    Analyte("amh", "AMH", "pmol/L", "hormones"),
    Analyte("cortisol", "Cortisol (morning)", "nmol/L", "hormones", 133, 537),
]}

#: Lab spellings we have seen, lowercased and stripped of punctuation.
ALIASES: dict[str, str] = {
    "25 hydroxyvitamin d": "vitamin_d", "25 oh vitamin d": "vitamin_d",
    "vitamin d 25 oh": "vitamin_d", "vitamin d": "vitamin_d", "vit d": "vitamin_d",
    "vitamin b12": "b12", "active b12": "b12", "cobalamin": "b12",
    "serum folate": "folate", "folate serum": "folate",
    "thyroid stimulating hormone": "tsh", "tsh": "tsh",
    "free thyroxine": "free_t4", "ft4": "free_t4", "t4 free": "free_t4",
    "free triiodothyronine": "free_t3", "ft3": "free_t3",
    "haemoglobin a1c": "hba1c", "hemoglobin a1c": "hba1c", "a1c": "hba1c",
    "hb a1c": "hba1c", "glycated haemoglobin": "hba1c",
    "haemoglobin": "haemoglobin", "hemoglobin": "haemoglobin", "hb": "haemoglobin",
    "total cholesterol": "cholesterol", "cholesterol total": "cholesterol",
    "ldl cholesterol": "ldl", "ldl c": "ldl", "ldl": "ldl",
    "hdl cholesterol": "hdl", "hdl c": "hdl", "hdl": "hdl",
    "c reactive protein": "crp", "crp hs": "crp", "hs crp": "crp",
    "high sensitivity crp": "crp",
    "alanine aminotransferase": "alt", "alt sgpt": "alt",
    "aspartate aminotransferase": "ast",
    "serum iron": "iron", "iron": "iron",
    "transferrin saturation": "transferrin_saturation", "tsat": "transferrin_saturation",
    "estradiol": "oestradiol", "oestradiol": "oestradiol", "e2": "oestradiol",
    "luteinising hormone": "lh", "luteinizing hormone": "lh",
    "follicle stimulating hormone": "fsh",
    "sex hormone binding globulin": "shbg",
    "anti mullerian hormone": "amh",
    "total testosterone": "testosterone", "testosterone total": "testosterone",
    "fasting glucose": "glucose", "glucose fasting": "glucose", "glucose": "glucose",
    "fasting insulin": "insulin",
    "estimated gfr": "egfr", "egfr": "egfr",
    "vitamin d3": "vitamin_d",
}

#: (analyte, unit as written) -> multiplier to reach the canonical unit.
#: Only conversions we are confident of; anything else is left alone and
#: reported as unconverted rather than silently mangled.
CONVERSIONS: dict[tuple[str, str], float] = {
    ("vitamin_d", "ng/ml"): 2.496,
    ("b12", "pg/ml"): 0.738, ("b12", "ng/l"): 0.738,
    ("folate", "ng/ml"): 2.266, ("folate", "ug/l"): 2.266,
    ("ferritin", "ng/ml"): 1.0,
    ("haemoglobin", "g/dl"): 10.0,
    ("iron", "ug/dl"): 0.179,
    ("glucose", "mg/dl"): 1 / 18.016,
    ("cholesterol", "mg/dl"): 1 / 38.67,
    ("ldl", "mg/dl"): 1 / 38.67,
    ("hdl", "mg/dl"): 1 / 38.67,
    ("triglycerides", "mg/dl"): 1 / 88.57,
    ("creatinine", "mg/dl"): 88.4,
    ("crp", "mg/dl"): 10.0,
    ("testosterone", "ng/dl"): 0.0347,
    ("oestradiol", "pg/ml"): 3.671,
    ("progesterone", "ng/ml"): 3.18,
    ("cortisol", "ug/dl"): 27.59,
    ("insulin", "uiu/ml"): 6.945, ("insulin", "miu/l"): 6.945,
    ("tsh", "uiu/ml"): 1.0,
    ("prolactin", "ng/ml"): 21.2,
}

#: Unit spellings normalised before lookup.
UNIT_ALIASES = {
    "µg/l": "ug/l", "μg/l": "ug/l", "mcg/l": "ug/l",
    "µmol/l": "umol/l", "μmol/l": "umol/l",
    "µiu/ml": "uiu/ml", "μiu/ml": "uiu/ml",
    "µg/dl": "ug/dl", "μg/dl": "ug/dl",
    "iu/ml": "iu/l", "u/l": "u/l",
    "%": "%", "percent": "%",
}


def normalise_name(raw: str) -> str:
    text = "".join(c if c.isalnum() or c.isspace() else " " for c in raw.lower())
    return " ".join(text.split())


def canonical_analyte(raw: str) -> str | None:
    """Map a lab's spelling onto our vocabulary, or None if we don't know it."""
    name = normalise_name(raw)
    if name in ALIASES:
        return ALIASES[name]
    key = name.replace(" ", "_")
    if key in ANALYTES:
        return key
    return None


def normalise_unit(raw: str | None) -> str:
    if not raw:
        return ""
    unit = raw.strip().lower().replace(" ", "")
    return UNIT_ALIASES.get(unit, unit)


def convert(analyte: str, value: float | None, unit: str | None
            ) -> tuple[float | None, str, bool]:
    """Convert into the analyte's canonical unit.

    Returns (value, unit, converted_cleanly). The last flag is False when we
    did not recognise the unit — the number is kept as given and marked, rather
    than being assumed to already be canonical.
    """
    spec = ANALYTES.get(analyte)
    if spec is None or value is None:
        return value, normalise_unit(unit), False

    given = normalise_unit(unit)
    canonical = normalise_unit(spec.unit)
    if not given or given == canonical:
        return value, spec.unit, True

    factor = CONVERSIONS.get((analyte, given))
    if factor is None:
        return value, given, False
    return round(value * factor, 4), spec.unit, True


def hba1c_percent_to_mmol(percent: float) -> float:
    """NGSP % to IFCC mmol/mol. Its own function because it is an affine
    transform, not a multiplication, and treating it as one is a classic error."""
    return round((percent - 2.15) * 10.929, 1)
