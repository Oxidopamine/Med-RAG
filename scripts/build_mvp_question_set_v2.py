"""Extend the MVP coverage question set from the stage-1 draw of 50 to the full frame.

Stage 1 authored 50 of the 165 formal recommendations in the sampling frame. The
pre-registered stage 2 extends the same seeded draw to 150, and because
`random.Random(SEED).sample` is prefix-stable at this frame size, that draw *contains* the
stage-1 draw exactly - `build_mvp_question_set.py --sample-n 150` asserts it rather than
assuming it. This authors the remaining recommendations.

## Why the full 165 and not 150

The frame is 165. Drawing 150 of 165 samples 91% of the population, at which point the
finite-population correction shrinks the standard error by about 70% - the pre-registered
Wilson intervals, which assume an infinite population, are badly conservative there - and
the 15 questions that would complete the frame cost almost nothing to author. So all 115
remaining recommendations are authored, which yields both analyses from one run:

* the **pre-registered n = 150** result, computed on the seeded 150-item prefix, unchanged
  and still the primary; and
* a **full-frame census** with no sampling error at all, as a secondary.

That is a superset of the protocol, not a deviation from it. The stage-1 items are carried
over byte-for-byte: no question text from v1 is rewritten here, so any change in the
stage-1 subset's result between runs is attributable to the system, never to the questions.

## What is authored and what is derived

The question text is the only authored part, and it is a judgement - the same one L2 in the
README records as a threat to validity. Each recommendation is instantiated into one of the
ten Ely generic forms by writing the *clinical situation*, not by rewording the statement.
Everything else - chapter, strength, certainty, source statement, page index - is copied
from the frame the builder extracts from the parent guideline.

`review_status` on the emitted set is **REVIEWED**, and it says on what basis. The stage-1
set became REVIEWED when the project owner read all 49 individually. The 115 stage-2
questions were accepted as a batch on the owner's instruction, without an item-by-item
read, and the status field records that distinction rather than flattening it - the gate in
`run_mvp_coverage_stage1.py` only checks the prefix, so the field is the only place a later
reader can learn what the acceptance actually consisted of. L2 in the README still applies:
these questions are model-authored, and that is a threat to validity whether or not a human
signed them off in a batch.

## The lexical-leakage screen is recomputed, and that is a change

v1 stores a `source_term_overlap` per item, but no committed code computes it, and its
exact definition could not be reproduced from the repository: the closest reconstruction
(alphabetic tokens longer than three characters, counted as leaked when they occur as a
substring of the lowercased source statement) reproduces the published aggregates - mean
0.452 against 0.447, identical median 0.500 - but not every individual value. Rather than
carry a metric nobody can recompute, `overlap` here is that explicit definition, applied
uniformly to all 165 items. Both numbers are kept: `source_term_overlap_v1` preserves what
v1 published for the original 49, and `source_term_overlap` is the recomputed screen.

Usage:

    python scripts/build_mvp_question_set_v2.py \\
        --draw data/local/mvp-draw-n165.json \\
        --v1 benchmarks/questions/mvp-coverage-who-hiv-v1.json \\
        --output benchmarks/questions/mvp-coverage-who-hiv-v2.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

ELY_FORMS = {
    1: "What is the drug of choice for condition x?",
    2: "What is the cause of symptom x?",
    3: "What test is indicated in situation x?",
    4: "What is the dose of drug x?",
    5: "How should I treat condition x (not limited to drug treatment)?",
    6: "How should I manage condition x (not specifying diagnostic or therapeutic)?",
    7: "What is the cause of physical finding x?",
    8: "What is the cause of test finding x?",
    9: "Can drug x cause (adverse) finding y?",
    10: "Could this patient have condition x?",
}

REVIEW_FLAG_THRESHOLD = 0.60

# The pre-registered stage-2 draw size. Membership is decided by the seeded draw order.
PREREGISTERED_N = 150

# recommendation_id -> (ely form number, question, extra flags)
AUTHORED: dict[str, tuple[int, str, tuple[str, ...]]] = {
    # Chapter 2 - HIV testing and diagnosis
    "WHO2021-C2-004": (3, "A mother brings her toddler for routine vaccinations in a district where HIV is common, and nobody has ever checked the child's status. Should a test be part of that visit?", ()),
    "WHO2021-C2-009": (6, "Our programme only tests key populations inside the clinic. Should we also be reaching them where they live and work?", ()),
    "WHO2021-C2-012": (6, "A newly diagnosed man has several partners he is afraid to tell. What can the service do to help reach them?", ()),
    "WHO2021-C2-013": (6, "Can we ask clients from key populations to invite peers from their own social circles in for testing?", ()),
    "WHO2021-C2-016": (3, "Should a baby born to a mother with HIV have a virological sample taken in the first days of life, on top of the usual schedule?", ()),
    "WHO2021-C2-017": (3, "A severely malnourished toddler is admitted to the paediatric ward and her status has never been established. What should be done?", ("NEAR_DUPLICATE_OF:WHO2021-C2-003",)),
    "WHO2021-C2-019": (3, "A nine-month-old needs a definitive diagnosis. Which kind of test gives the answer at this age?", ()),
    "WHO2021-C2-022": (8, "An infant assay returns a very low positive signal close to the detection limit. How should such a result be handled?", ()),
    "WHO2021-C2-025": (6, "A 16-year-old who sells sex comes to the clinic. Should testing be offered, and what should go with it?", ()),
    "WHO2021-C2-027": (3, "In a district where HIV is common, should every adolescent attending the clinic be offered a test regardless of why they came?", ()),
    "WHO2021-C2-028": (6, "In a country where infection is concentrated in a few groups, how should services handle adolescents who want a test?", ()),
    "WHO2021-C2-030": (6, "Should testing for key populations happen in facilities, in the community, or both?", ("NEAR_DUPLICATE_OF:WHO2021-C2-009",)),
    "WHO2021-C2-031": (6, "Is it acceptable to reach untested peers through the social circles of clients already in care?", ("NEAR_DUPLICATE_OF:WHO2021-C2-013",)),
    "WHO2021-C2-035": (6, "Should everyone newly diagnosed be offered help with contacting partners, or only those who ask for it?", ("NEAR_DUPLICATE_OF:WHO2021-C2-012",)),
    "WHO2021-C2-036": (6, "A couple attends together and wants to be tested at the same time. How should that visit be handled?", ()),
    "WHO2021-C2-040": (6, "Can a trained community worker who is not a nurse carry out rapid tests on their own?", ()),
    "WHO2021-C2-041": (3, "Our national algorithm still ends with a confirmatory strip assay from the 1990s. Should that stay in place?", ()),
    "WHO2021-C2-049": (3, "Is there value in drawing a virological sample at delivery for an exposed newborn, in addition to the standard schedule?", ("NEAR_DUPLICATE_OF:WHO2021-C2-016",)),
    "WHO2021-C2-051": (3, "How do we establish whether a six-month-old was exposed, when antibody results at that age reflect the mother?", ()),
    "WHO2021-C2-052": (3, "A three-year-old needs a diagnosis. Can the standard rapid antibody algorithm be used?", ()),
    "WHO2021-C2-054": (8, "What should a laboratory do with early infant results that fall in a grey zone near the cut-off?", ("NEAR_DUPLICATE_OF:WHO2021-C2-022",)),
    # Chapter 3 - HIV prevention
    "WHO2021-C3-057": (1, "A man in a serodiscordant relationship keeps testing negative but is clearly at risk. What can be offered to keep him negative?", ()),
    "WHO2021-C3-062": (5, "After an occupational needlestick, is a two-drug course enough or should a third agent be added?", ()),
    "WHO2021-C3-063": (1, "Which two drugs form the base of a post-exposure course for an adult?", ()),
    "WHO2021-C3-066": (1, "A seven-year-old needs post-exposure prophylaxis. Which backbone should be used?", ()),
    "WHO2021-C3-067": (1, "Which agent should be added as the third drug in a post-exposure course?", ()),
    "WHO2021-C3-068": (1, "The preferred third agent for post-exposure prophylaxis is out of stock. What else can be used?", ()),
    "WHO2021-C3-072": (5, "A baby is born to a mother diagnosed late in pregnancy who is not virally suppressed. What prophylaxis does the newborn need?", ()),
    "WHO2021-C3-073": (5, "A high-risk breastfed infant has finished six weeks of prophylaxis. Is that the end of it?", ()),
    # Chapter 4 - ART
    "WHO2021-C4-078": (6, "A patient's diagnosis was confirmed today and he has been examined. How soon should treatment begin?", ()),
    "WHO2021-C4-080": (6, "A woman diagnosed this morning says she wants to start straight away. Can she?", ()),
    "WHO2021-C4-085": (6, "A patient is being treated for fungal meningitis and is not yet on antiretrovirals. When should those be started?", ()),
    "WHO2021-C4-092": (1, "The preferred first-line anchor drug cannot be used in this adult. What is the recommended alternative?", ()),
    "WHO2021-C4-098": (1, "An adult on a dolutegravir-based regimen has confirmed treatment failure. What should the next regimen contain?", ()),
    "WHO2021-C4-106": (6, "How often should the virus level be checked once someone is settled on treatment?", ()),
    "WHO2021-C4-107": (6, "Do we still need to track immune cell counts for a stable patient when virus levels are monitored routinely?", ()),
    "WHO2021-C4-109": (3, "Our facility has no access to virus-level testing. How do we detect that treatment is failing?", ()),
    "WHO2021-C4-110": (3, "We cannot transport plasma from remote sites. Is a dried spot sample acceptable for measuring virus levels, and does the cut-off change?", ()),
    "WHO2021-C4-113": (6, "A person starting treatment already carries resistance to the older non-nucleoside class. What should be avoided?", ()),
    # Chapter 5 - Advanced HIV disease
    "WHO2021-C5-115": (6, "A man arrives severely immunosuppressed and unwell. What should his care include beyond starting treatment?", ()),
    "WHO2021-C5-117": (3, "A patient with advanced disease has headache, fever and a stiff neck. What should be done to establish the cause?", ()),
    "WHO2021-C5-122": (3, "Before starting treatment in someone with a very low immune cell count, what should be looked for even if they feel well?", ()),
    "WHO2021-C5-124": (6, "A screening test for fungal antigen comes back positive but the patient has no symptoms. What now?", ()),
    "WHO2021-C5-128": (4, "After the intensive phase of antifungal treatment, what dose continues, and does it differ for a child?", ()),
    "WHO2021-C5-130": (6, "Is it safe to begin antiretrovirals while a patient is still in the early weeks of treatment for fungal meningitis?", ("NEAR_DUPLICATE_OF:WHO2021-C4-085",)),
    "WHO2021-C5-132": (5, "How should a severely ill patient with disseminated histoplasmosis be treated?", ()),
    "WHO2021-C5-134": (5, "A patient has mild histoplasmosis and can take oral medication. What treatment is indicated?", ()),
    "WHO2021-C5-136": (6, "A patient on antifungal maintenance is now stable, virally suppressed and immune-recovered. Must maintenance run a full year?", ()),
    "WHO2021-C5-137": (6, "When should antiretrovirals begin in someone with disseminated fungal infection but no sign of brain involvement?", ()),
    # Chapter 6 - Coinfections and comorbidities
    "WHO2021-C6-139": (6, "How much daily physical activity should a school-age child be getting?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-140": (6, "Beyond everyday movement, what kind of exercise should young people do, and how often?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-141": (6, "Is there guidance on how much recreational screen time young people should have?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-144": (6, "How much exercise per week should an adult aim for?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-148": (6, "An adult sits at a desk all day. Is meeting the usual activity target enough?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-149": (6, "What kind of activity should someone over 70 do to reduce their risk of falling?", ("GENERAL_POPULATION_ADVICE",)),
    "WHO2021-C6-150": (6, "An adult presents with advanced immunosuppression. What preventive medicine should start alongside treatment?", ()),
    "WHO2021-C6-151": (6, "In an area with heavy malaria transmission, does the immune cell count decide who gets preventive antibiotics?", ()),
    "WHO2021-C6-152": (6, "A patient has been stable on treatment for two years with a recovered immune system. Can the daily preventive antibiotic stop?", ()),
    "WHO2021-C6-153": (6, "In a high-malaria district, should the daily preventive antibiotic be stopped once someone is doing well?", ()),
    "WHO2021-C6-156": (6, "A seven-year-old has been suppressed on treatment for a year in an area with little malaria. Can preventive antibiotics stop?", ()),
    "WHO2021-C6-157": (6, "When does an exposed baby start preventive antibiotics, and when can they be stopped?", ()),
    "WHO2021-C6-160": (3, "What should be asked at every visit to find people who might have tuberculosis?", ()),
    "WHO2021-C6-162": (3, "Is there a blood test that helps decide who needs further tuberculosis investigation?", ()),
    "WHO2021-C6-163": (3, "Can imaging be used as a screening tool for tuberculosis in this population?", ()),
    "WHO2021-C6-164": (3, "We have digital radiography but no radiologist. Can software read the films for tuberculosis screening?", ()),
    "WHO2021-C6-166": (3, "On a medical ward where tuberculosis is very common, should every admitted patient be tested for it?", ()),
    "WHO2021-C6-169": (6, "A patient has no symptoms suggesting tuberculosis. Should anything be given to prevent it?", ()),
    "WHO2021-C6-170": (6, "A baby under a year lives with someone being treated for tuberculosis. What should the baby receive?", ()),
    "WHO2021-C6-171": (6, "A two-year-old lives where tuberculosis is common but has no known household contact. Should preventive treatment be offered?", ()),
    "WHO2021-C6-173": (6, "Someone screens negative on the symptom check. What is the next step?", ()),
    "WHO2021-C6-174": (6, "A patient reports a cough at screening. What happens next, and what if the workup finds nothing?", ()),
    "WHO2021-C6-177": (6, "For a household contact who is not living with HIV, what is enough to rule out active disease before preventive treatment?", ()),
    "WHO2021-C6-178": (3, "Which test identifies latent infection?", ()),
    "WHO2021-C6-182": (3, "In a country where hepatitis B is common, who should be offered testing for it?", ()),
    "WHO2021-C6-203": (3, "Where hepatitis C is common in the general population, how should testing be organized?", ()),
    "WHO2021-C6-207": (3, "Is there a way to target hepatitis testing at older age groups in a country with low overall prevalence?", ()),
    "WHO2021-C6-211": (6, "A pregnant woman has both HIV and hepatitis B. What protects the baby from acquiring hepatitis B?", ()),
    "WHO2021-C6-215": (6, "A coinfected patient has recovered from a first episode of visceral leishmaniasis. Is anything needed to stop it returning?", ()),
    "WHO2021-C6-217": (3, "Which screening test should be used first for cervical cancer in women living with HIV?", ()),
    "WHO2021-C6-219": (6, "After a positive result on the primary cervical screening test, should treatment follow directly or should another step come first?", ()),
    "WHO2021-C6-222": (6, "Can a woman take her own sample for cervical screening, or must a clinician collect it?", ()),
    "WHO2021-C6-223": (6, "At what age should cervical screening begin for a woman living with HIV?", ()),
    "WHO2021-C6-225": (6, "A woman of 55 has had two negative cervical screens in a row. Does she need to keep coming?", ()),
    "WHO2021-C6-228": (6, "How often should cervical screening be repeated when the primary test detects the virus itself?", ()),
    "WHO2021-C6-229": (6, "Where the virus-detection test is unavailable, how often should cervical screening be repeated?", ()),
    "WHO2021-C6-231": (6, "A woman screened positive but her follow-up test was negative. When should she be seen again?", ()),
    "WHO2021-C6-232": (6, "A woman had an abnormal smear but a normal colposcopy. What follow-up interval applies?", ()),
    "WHO2021-C6-237": (5, "How should a glandular pre-cancerous cervical lesion confirmed on histology be treated?", ()),
    "WHO2021-C6-239": (6, "Should heart disease risk be assessed differently in people living with HIV?", ()),
    "WHO2021-C6-246": (3, "Should asymptomatic men who have sex with men be screened by culture at every visit for gonorrhoea?", ()),
    "WHO2021-C6-248": (3, "Should female sex workers be screened for infections even when they have no symptoms?", ()),
    "WHO2021-C6-250": (3, "What infection screening belongs in a woman's first pregnancy visit?", ()),
    "WHO2021-C6-255": (5, "A woman has thick white discharge with itching. What should she be treated for?", ()),
    "WHO2021-C6-258": (5, "A patient presents with a genital ulcer and the laboratory cannot type it. How should they be treated?", ()),
    "WHO2021-C6-259": (5, "A patient reporting receptive anal sex presents with rectal discharge. How should this be managed where testing is limited?", ()),
    "WHO2021-C6-262": (6, "How long should a mother on treatment feed her baby at the breast?", ()),
    # Chapter 7 - Service delivery
    "WHO2021-C7-267": (6, "Someone has just received a positive result. What should happen to make sure they actually reach care?", ()),
    "WHO2021-C7-276": (6, "Can treatment be started somewhere other than the clinic?", ()),
    "WHO2021-C7-280": (6, "Is it acceptable to hand over the first supply of treatment on the day of diagnosis?", ("NEAR_DUPLICATE_OF:WHO2021-C4-080",)),
    "WHO2021-C7-282": (6, "How often does a stable patient need to be seen in clinic?", ()),
    "WHO2021-C7-284": (6, "How much medicine should a stable patient be given at a time?", ()),
    "WHO2021-C7-286": (6, "What should be offered to help people keep taking their treatment?", ()),
    "WHO2021-C7-290": (6, "What helps keep people in care over the long term?", ()),
    "WHO2021-C7-295": (6, "A patient has not collected medicine for four months. What should the programme do?", ()),
    "WHO2021-C7-297": (6, "Our facility has no doctor on site. Can a nurse start someone on first-line treatment?", ()),
    "WHO2021-C7-301": (6, "Can staff who are not laboratory-trained take samples and run point-of-care tests?", ()),
    "WHO2021-C7-305": (6, "Should a pregnant woman diagnosed at the antenatal clinic be referred elsewhere to start treatment?", ()),
    "WHO2021-C7-307": (6, "A patient starting tuberculosis treatment is found to have HIV. Where should antiretrovirals be started?", ()),
    "WHO2021-C7-308": (6, "Can tuberculosis treatment be given in the HIV clinic where the diagnosis was made?", ()),
    "WHO2021-C7-310": (6, "Can contraception and infection services be delivered in the same visit as HIV care?", ()),
    "WHO2021-C7-312": (6, "Can blood pressure and diabetes care be provided within HIV services?", ()),
    "WHO2021-C7-313": (6, "A patient receives opioid substitution therapy. Where should his antiretrovirals come from?", ()),
    "WHO2021-C7-315": (6, "What should services do differently to keep teenagers engaged in care?", ()),
    "WHO2021-C7-320": (6, "What support beyond medication should young people living with HIV receive?", ()),
    "WHO2021-C7-321": (6, "Results from infant testing take weeks to come back on paper. Is there a recommended way to speed that up?", ()),
}

_TOKEN = re.compile(r"[a-z]+")


def source_term_overlap(question: str, source_statement: str) -> float:
    """Fraction of a question's content terms that leak from its source recommendation.

    Content terms are alphabetic tokens longer than three characters. A term counts as
    leaked when it occurs as a substring of the lowercased source statement, which is what
    makes "testing" match "test" without a stemmer. Lower is better; the metric cannot
    separate an unavoidable clinical noun from the recommendation's distinctive framing,
    so a high value flags an item for review rather than convicting it.
    """

    terms = {token for token in _TOKEN.findall(question.lower()) if len(token) > 3}
    if not terms:
        return 0.0
    source = source_statement.lower()
    return len([term for term in terms if term in source]) / len(terms)


def question_id(recommendation_id: str) -> str:
    return recommendation_id.replace("WHO2021-", "MVPQ-")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draw", type=Path, required=True, help="emitted n=165 draw")
    parser.add_argument("--v1", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    draw = json.loads(arguments.draw.read_text(encoding="utf-8"))
    v1 = json.loads(arguments.v1.read_text(encoding="utf-8"))
    frame = {record["id"]: record for record in draw["draw"]}
    existing = {item["recommendation_id"]: item for item in v1["items"]}

    missing = [rid for rid in frame if rid not in existing and rid not in AUTHORED]
    if missing:
        raise SystemExit(f"{len(missing)} recommendations have no authored question: {missing[:5]}")

    # Membership of the pre-registered n=150 is a property of the *seeded draw order*, so
    # the emitted file must carry that order rather than a sorted view of it. An earlier
    # version of the emitter wrote the chapter-sorted list under the same key, which turned
    # this into "the first 150 by chapter" - wrong on 26 of 165 items - and produced a
    # headline number that was not the pre-registered one.
    if not draw.get("draw_order_is_seeded"):
        raise SystemExit(
            "the draw file does not declare draw_order_is_seeded; re-emit it with a "
            "current build_mvp_question_set.py, because a sorted draw cannot answer which "
            "items are in the pre-registered prefix"
        )
    order = [record["id"] for record in draw["draw"]]
    stage1_ids = {item["recommendation_id"] for item in v1["items"]}

    # The design guarantee, checked rather than trusted: stage 2 extends stage 1, so every
    # stage-1 recommendation must fall inside the pre-registered prefix. This is the
    # assertion whose absence let the sorted-order bug ship.
    preregistered = set(order[:PREREGISTERED_N])
    missing_stage1 = stage1_ids - preregistered
    if missing_stage1:
        raise SystemExit(
            f"{len(missing_stage1)} stage-1 recommendations fall outside the "
            f"pre-registered n={PREREGISTERED_N} prefix: {sorted(missing_stage1)}. "
            "The draw is not nesting, so the two-stage design is broken."
        )

    items: list[dict[str, Any]] = []
    for recommendation_id in sorted(frame, key=lambda r: (frame[r]["chapter_no"], r)):
        record = frame[recommendation_id]
        carried = existing.get(recommendation_id)
        if carried is not None:
            item = dict(carried)
            item["source_term_overlap_v1"] = carried.get("source_term_overlap")
            item["stage"] = 1
        else:
            form_no, question, extra = AUTHORED[recommendation_id]
            item = {
                "question_id": question_id(recommendation_id),
                "recommendation_id": recommendation_id,
                "chapter_no": record["chapter_no"],
                "chapter": record["chapter"],
                "ely_form_no": form_no,
                "ely_form": ELY_FORMS[form_no],
                "question": question,
                "strength": record["strength"],
                "certainty": record["certainty"],
                "source_statement": record["statement"],
                "source_pdf_page_index": record["pdf_page_index"],
                "flags": list(extra),
                "usable": True,
                "classification": None,
                "source_term_overlap_v1": None,
                "stage": 2,
            }
        # The one v1 UNUSABLE_FRAGMENT has no question text: an anaphoric statement with
        # no referent, excluded rather than invented. It carries no screen either.
        if not item.get("question"):
            item["source_term_overlap"] = None
            item["in_preregistered_150"] = recommendation_id in preregistered
            items.append(item)
            continue
        overlap = source_term_overlap(item["question"], item["source_statement"])
        item["source_term_overlap"] = round(overlap, 3)
        flags = [flag for flag in item.get("flags", []) if flag != "REVIEW_HIGH_SOURCE_OVERLAP"]
        if overlap >= REVIEW_FLAG_THRESHOLD:
            flags.append("REVIEW_HIGH_SOURCE_OVERLAP")
        item["flags"] = flags
        item["in_preregistered_150"] = recommendation_id in preregistered
        items.append(item)

    usable = [item for item in items if item["usable"]]
    overlaps = [
        item["source_term_overlap"] for item in usable if item["source_term_overlap"] is not None
    ]
    stage2 = [item for item in items if item["stage"] == 2]

    document = {
        "schema_version": 2,
        "set_id": "mvp-coverage-who-hiv-v2",
        "purpose": (
            "MVP answerable-coverage measurement, stage 2 and full-frame census. "
            "NOT release-bound benchmark material."
        ),
        "supersedes": v1["set_id"],
        "frame": v1["frame"],
        "sampling": {
            "method": "uniform without replacement over the frame sorted by id",
            "seed": draw["seed"],
            "frame_size": draw["frame_size"],
            "n_authored": len(items),
            "n_usable": len(usable),
            "preregistered_stage2_n": 150,
            "nesting": (
                "the n=150 draw's first 50 elements are exactly the stage-1 draw; "
                "build_mvp_question_set.py asserts this rather than assuming it"
            ),
            "census_note": (
                "all 165 frame members are authored, so the pre-registered n=150 analysis "
                "(items with in_preregistered_150) and a full-frame census with no "
                "sampling error are both available from one run"
            ),
        },
        "question_construction": "Ely generic clinical question forms; see ely-generic-question-types.md",
        # Free text by design: a later reader needs to know what the acceptance rested on,
        # not merely that a flag was set. The stage-1 half was read item by item; the
        # stage-2 half was accepted as a batch. Both facts are recorded, because whether a
        # per-question claim is quotable depends on which one applies to that question.
        "review_status": (
            "REVIEWED - accepted by the project owner on 2026-08-29 for the stage-2 and "
            "census runs. The 50 stage-1 questions were read individually on 2026-08-27 "
            "and are carried over with no text rewritten. The 115 stage-2 questions are "
            "model-authored and were accepted as a batch on the owner's instruction "
            "without individual review; the 17 items flagged REVIEW_HIGH_SOURCE_OVERLAP "
            "were not individually adjudicated. L2 in the README continues to apply."
        ),
        "source_term_overlap": {
            "definition": (
                "fraction of a question's content terms (alphabetic, longer than three "
                "characters) that occur as a substring of the lowercased source statement"
            ),
            "recomputed": (
                "v1 published a per-item value that no committed code reproduces; this "
                "definition matches its aggregates (mean 0.452 vs 0.447, median 0.500) but "
                "not every item. source_term_overlap_v1 preserves the v1 value where one "
                "existed."
            ),
            "comparator": "the 430-case source-derived development suite measures 1.000 on five strata",
            "n": len(usable),
            "mean": round(statistics.mean(overlaps), 3),
            "median": round(statistics.median(overlaps), 3),
            "min": min(overlaps),
            "max": max(overlaps),
            "at_or_above_0_60": sum(1 for value in overlaps if value >= REVIEW_FLAG_THRESHOLD),
            "known_limit": (
                "the metric cannot separate unavoidable clinical nouns from the "
                "recommendation's distinctive framing. A high score flags an item for "
                "review; it does not convict it."
            ),
        },
        "items": items,
    }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"items          : {len(items)} ({len(stage2)} newly authored, {len(items)-len(stage2)} carried)")
    print(f"usable         : {len(usable)}")
    print(f"in n=150 subset: {sum(1 for i in items if i['in_preregistered_150'])}")
    print(f"overlap        : mean {document['source_term_overlap']['mean']} "
          f"median {document['source_term_overlap']['median']} "
          f">=0.60 {document['source_term_overlap']['at_or_above_0_60']}")
    from collections import Counter
    flags = Counter(flag for item in items for flag in item["flags"])
    print(f"flags          : {dict(flags)}")
    print(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
