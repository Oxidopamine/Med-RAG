import re

from app.schemas.domain import ClinicalContext, Measurement

AGE_PATTERN = re.compile(r"\b(\d{1,3})\s*(?:-|\s)\s*year(?:s)?(?:-|\s)old\b", re.IGNORECASE)
MEASUREMENT_PATTERNS = {
    "EGFR": re.compile(r"\begfr\s*(?:is|of|=|:)?\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE),
    "CRCL": re.compile(
        r"\b(?:crcl|creatinine clearance)\s*(?:is|of|=|:)?\s*(\d+(?:\.\d+)?)\b",
        re.IGNORECASE,
    ),
}
CONDITION_PATTERNS = {
    "ATRIAL_FIBRILLATION": re.compile(
        r"\b(?:atrial fibrillation|afib|af)\b", re.IGNORECASE
    ),
    "CHRONIC_KIDNEY_DISEASE": re.compile(
        r"\b(?:chronic kidney disease|ckd)\b", re.IGNORECASE
    ),
}


def extract_context_preview(question: str) -> ClinicalContext:
    """Extract only explicit user-provided facts for the pre-retrieval preview."""
    age_match = AGE_PATTERN.search(question)
    age = int(age_match.group(1)) if age_match else None

    conditions = {
        concept for concept, pattern in CONDITION_PATTERNS.items() if pattern.search(question)
    }
    measurements: list[Measurement] = []
    for concept, pattern in MEASUREMENT_PATTERNS.items():
        match = pattern.search(question)
        if match:
            measurements.append(
                Measurement(
                    concept=concept,
                    value=float(match.group(1)),
                    unit="mL/min/1.73m2" if concept == "EGFR" else "mL/min",
                    provenance="USER_TEXT_EXPLICIT",
                )
            )

    lower_question = question.lower()
    topic = "anticoagulation" if "anticoag" in lower_question else None
    special_populations = set()
    if any(item.concept in {"EGFR", "CRCL"} for item in measurements) or (
        "CHRONIC_KIDNEY_DISEASE" in conditions
    ):
        special_populations.add("RENAL_IMPAIRMENT")

    return ClinicalContext(
        age=age,
        conditions=conditions,
        measurements=measurements,
        special_populations=special_populations,
        question_type="treatment_guideline" if topic else "guideline_lookup",
        topic=topic,
    )

