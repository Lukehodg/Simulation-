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

#: Generic reference ranges are sex-specific for a good number of analytes.
#: Where a panel says which, we use it; otherwise this is the fallback.
REFERENCE_PROFILE = "adult_female"
PROFILES = ("adult_female", "adult_male")


def profile_for(sex: str | None) -> str:
    if not sex:
        return REFERENCE_PROFILE
    return "adult_male" if sex.strip().lower().startswith("m") else "adult_female"


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
    #: True where an exogenous compound moves this analyte enough that a trend
    #: spanning the start of that compound is not a like-for-like comparison.
    compound_sensitive: bool = False
    note: str | None = None
    #: Sex-specific generic intervals, where one interval would be wrong for
    #: half the population. (low, high), either bound optional.
    ref_male: tuple[float | None, float | None] | None = None
    ref_female: tuple[float | None, float | None] | None = None

    def ranges(self, profile: str | None = None) -> tuple[float | None, float | None]:
        if profile == "adult_male" and self.ref_male:
            return self.ref_male
        if profile == "adult_female" and self.ref_female:
            return self.ref_female
        return self.ref_low, self.ref_high


ANALYTES: dict[str, Analyte] = {a.key: a for a in [
    # Iron studies — the group most worth tracking alongside a cycle
    Analyte("ferritin", "Ferritin", "ug/L", "iron", 15, 200, cycle_sensitive=True,
            ref_male=(30, 400), ref_female=(15, 200),
            note="an acute-phase reactant: rises with inflammation, so a normal "
                 "ferritin alongside a raised CRP can still mask iron deficiency"),
    Analyte("haemoglobin", "Haemoglobin", "g/L", "iron", 120, 150, cycle_sensitive=True,
            compound_sensitive=True, ref_male=(130, 175), ref_female=(120, 155)),
    Analyte("iron", "Serum iron", "umol/L", "iron", 10, 30, cycle_sensitive=True),
    Analyte("transferrin_saturation", "Transferrin saturation", "%", "iron", 20, 50),
    Analyte("b12", "Vitamin B12", "pmol/L", "vitamins", 180, 900),
    Analyte("folate", "Folate", "nmol/L", "vitamins", 7, None),
    Analyte("vitamin_d", "Vitamin D (25-OH)", "nmol/L", "vitamins", 50, 125),

    # Full blood count
    Analyte("haematocrit", "Haematocrit", "%", "fbc", compound_sensitive=True,
            ref_male=(40, 52), ref_female=(36, 48),
            note="exogenous androgens raise red cell mass; a rising haematocrit "
                 "on testosterone is expected and, past the top of the range, a "
                 "reason to see a doctor rather than adjust anything yourself"),
    Analyte("mch", "MCH", "pg", "fbc", 27, 32),
    Analyte("mchc", "MCHC", "g/L", "fbc", 320, 360),
    Analyte("mcv", "MCV", "fL", "fbc", 80, 100),
    Analyte("rbc", "Red blood cell count", "10^12/L", "fbc", compound_sensitive=True,
            ref_male=(4.5, 6.5), ref_female=(3.8, 5.8)),
    Analyte("wbc", "White blood cell count", "10^9/L", "fbc", 4.0, 10.0),
    Analyte("neutrophils", "Neutrophils", "10^9/L", "fbc", 2.0, 7.5),
    Analyte("lymphocytes", "Lymphocytes", "10^9/L", "fbc", 1.0, 3.5),
    Analyte("monocytes", "Monocytes", "10^9/L", "fbc", 0.2, 0.8),
    Analyte("eosinophils", "Eosinophils", "10^9/L", "fbc", 0.04, 0.4),
    Analyte("basophils", "Basophils", "10^9/L", "fbc", 0.01, 0.1),
    Analyte("platelets", "Platelets", "10^9/L", "fbc", 150, 450),

    # Thyroid
    Analyte("tsh", "TSH", "mIU/L", "thyroid", 0.4, 4.0),
    Analyte("free_t4", "Free T4", "pmol/L", "thyroid", 12, 22),
    Analyte("free_t3", "Free T3", "pmol/L", "thyroid", 3.1, 6.8),

    # Metabolic
    Analyte("hba1c", "HbA1c", "mmol/mol", "metabolic", None, 41,
            compound_sensitive=True),
    Analyte("glucose", "Fasting glucose", "mmol/L", "metabolic", 3.9, 5.5),
    Analyte("insulin", "Fasting insulin", "pmol/L", "metabolic", 18, 173),

    # Lipids
    Analyte("cholesterol", "Total cholesterol", "mmol/L", "lipids", None, 5.0),
    Analyte("ldl", "LDL cholesterol", "mmol/L", "lipids", None, 3.0,
            compound_sensitive=True),
    Analyte("hdl", "HDL cholesterol", "mmol/L", "lipids", 1.2, None,
            compound_sensitive=True,
            note="androgens, and oral 17-aa androgens especially, lower HDL; a "
                 "fall while on them is expected but still part of a "
                 "cardiovascular picture a clinician should read"),
    Analyte("triglycerides", "Triglycerides", "mmol/L", "lipids", None, 1.7,
            compound_sensitive=True),

    # Inflammation, liver, kidney
    Analyte("crp", "CRP", "mg/L", "inflammation", None, 5),
    Analyte("alt", "ALT", "U/L", "liver", None, 33,
            ref_male=(None, 41), ref_female=(None, 33)),
    Analyte("ast", "AST", "U/L", "liver", None, 32),
    Analyte("alp", "Alkaline phosphatase", "U/L", "liver", 30, 130),
    Analyte("ggt", "GGT", "U/L", "liver", None, 40,
            ref_male=(None, 60), ref_female=(None, 40),
            note="rises with alcohol, some medications, and fatty liver; a "
                 "single raised GGT is a prompt to look, not a diagnosis"),
    Analyte("bilirubin", "Total bilirubin", "umol/L", "liver", None, 21),
    Analyte("albumin", "Albumin", "g/L", "liver", 35, 50),
    Analyte("creatinine", "Creatinine", "umol/L", "kidney", 45, 90,
            ref_male=(60, 110), ref_female=(45, 90)),
    Analyte("urea", "Urea", "mmol/L", "kidney", 2.5, 7.8),
    Analyte("egfr", "eGFR", "mL/min/1.73m2", "kidney", 90, None),

    # Hormones — every one of these is meaningless without the cycle day
    Analyte("oestradiol", "Oestradiol", "pmol/L", "hormones", cycle_sensitive=True,
            compound_sensitive=True,
            note="varies several-fold across the cycle; only comparable between "
                 "draws taken in the same phase. Also rises with aromatising "
                 "androgens and with hCG"),
    Analyte("progesterone", "Progesterone", "nmol/L", "hormones", cycle_sensitive=True,
            note="the mid-luteal value is the one that means anything; a "
                 "follicular draw is expected to be low"),
    Analyte("lh", "LH", "IU/L", "hormones", cycle_sensitive=True,
            compound_sensitive=True,
            note="exogenous androgens suppress it toward zero; that is the "
                 "expected effect, not a pituitary problem"),
    Analyte("fsh", "FSH", "IU/L", "hormones", cycle_sensitive=True,
            compound_sensitive=True),
    Analyte("testosterone", "Testosterone", "nmol/L", "hormones", 0.3, 1.7,
            compound_sensitive=True,
            ref_male=(8.6, 29.0), ref_female=(0.3, 1.7),
            note="on exogenous testosterone the level reflects the dose and the "
                 "timing of the last injection, not endogenous production; a "
                 "trough draw and a peak draw are different questions"),
    Analyte("free_testosterone", "Free testosterone", "nmol/L", "hormones",
            compound_sensitive=True, ref_male=(0.175, 0.687)),
    Analyte("shbg", "SHBG", "nmol/L", "hormones", 30, 120, compound_sensitive=True),
    Analyte("prolactin", "Prolactin", "mIU/L", "hormones", 100, 500),
    Analyte("amh", "AMH", "pmol/L", "hormones"),
    Analyte("cortisol", "Cortisol (morning)", "nmol/L", "hormones", 133, 537),

    # Lipid ratio and prostate — reported by several UK panels
    Analyte("chol_hdl_ratio", "Total chol / HDL ratio", "ratio", "lipids",
            None, 5.0),
    Analyte("haematocrit_corrected", "Haematocrit (corrected)", "%", "fbc",
            ref_male=(40, 52), ref_female=(36, 48)),
    Analyte("psa", "PSA (total)", "ug/L", "other", None, 1.4),
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
    # Randox / UK panel spellings
    "mean cell haemoglobin mch": "mch", "mean cell haemoglobin": "mch",
    "mean cell haemoglobin concentration mchc": "mchc",
    "mean cell haemoglobin concentration": "mchc",
    "red blood cell mean cell volume mcv": "mcv", "mean cell volume": "mcv",
    "red blood cell count": "rbc", "white blood cell count": "wbc",
    "neutrophil count": "neutrophils", "lymphocyte count": "lymphocytes",
    "monocyte count": "monocytes", "eosinophil count": "eosinophils",
    "basophil count": "basophils", "platelet count": "platelets",
    "haematocrit": "haematocrit", "hematocrit": "haematocrit",
    "hctratio corrected": "haematocrit_corrected",
    "haematocrit ratio corrected": "haematocrit_corrected",
    "alanine aminotransferase alt": "alt",
    "aspartate aminotransferase ast": "ast",
    "alkaline phosphatase alp": "alp", "alkaline phosphatase": "alp",
    "gamma glutamyltransferase ggt": "ggt", "gamma glutamyl transferase": "ggt",
    "ggt": "ggt", "total bilirubin": "bilirubin", "bilirubin": "bilirubin",
    "albumin": "albumin", "urea": "urea",
    "estimated glomerular filtration rate egfr": "egfr",
    "thyroid stimulating hormone tsh": "tsh",
    "free thyroxine ft4": "free_t4", "free triiodothyronine ft3": "free_t3",
    "follicle stimulating hormone": "fsh", "luteinising hormone": "lh",
    "sex hormone binding globulin": "shbg",
    "free testosterone": "free_testosterone",
    "total prostate specific antigen tpsa": "psa",
    "prostate specific antigen": "psa", "psa": "psa",
    "total cholesterol hdl cholesterol ratio": "chol_hdl_ratio",
    "cholesterol hdl ratio": "chol_hdl_ratio",
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
    # Several UK panels print the cholesterol/HDL ratio with a percent sign.
    # It is a ratio; the unit is a typo, not a scale.
    ("chol_hdl_ratio", "%"): 1.0,
}

#: Unit spellings normalised before lookup.
UNIT_ALIASES = {
    "µg/l": "ug/l", "μg/l": "ug/l", "mcg/l": "ug/l",
    "µmol/l": "umol/l", "μmol/l": "umol/l",
    "µiu/ml": "uiu/ml", "μiu/ml": "uiu/ml",
    "µg/dl": "ug/dl", "μg/dl": "ug/dl",
    "iu/ml": "iu/l", "u/l": "u/l", "iu_l": "iu/l", "miu/ml": "miu/l",
    "10^9/l": "10^9/l", "10^12/l": "10^12/l", "fl": "fl", "pg": "pg",
    "ml/min/1.7": "ml/min/1.73m2", "ml/min/1.73m²": "ml/min/1.73m2",
    "ml/min": "ml/min/1.73m2",
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
