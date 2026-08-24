from app.reasoning.context_extractor import extract_context_preview


def test_extracts_only_explicit_clinical_context() -> None:
    context = extract_context_preview(
        "For a 74-year-old with AF and eGFR 28, what do guidelines say about anticoagulation?"
    )

    assert context.age == 74
    assert context.conditions == {"ATRIAL_FIBRILLATION"}
    assert context.measurements[0].concept == "EGFR"
    assert context.measurements[0].value == 28
    assert context.special_populations == {"RENAL_IMPAIRMENT"}
    assert context.topic == "anticoagulation"


def test_does_not_invent_absent_values() -> None:
    context = extract_context_preview("What does the guideline say about hypertension?")

    assert context.age is None
    assert context.conditions == set()
    assert context.measurements == []

