"""Switches that turn individual safety mechanisms off, for measurement only.

The serving path composes five mechanisms that each withhold content the naive pipeline
would render. Their *combined* effect is visible in every coverage run - `p_answered`
counts what survived all five - but the contribution of any one of them is not. This
module makes each one switchable so the chain can be measured against itself.

It exists because "the safety chain costs coverage" is an architectural claim in
`docs/roadmap.md` and the README, and an unmeasured one. A baseline that switches the
mechanisms off, run on the same questions against the same release, turns it into a
number: how many answers each mechanism withholds, and - once the buckets are filled in -
how many of those were worth withholding.

## The default is the measured system

`AblationProfile()` with no arguments is every mechanism *on*, which is exactly the
behaviour every prior report was produced under. Nothing in the serving or benchmark path
changes unless a caller passes a non-default profile, and `is_production` records whether
one did. A run whose profile is not `is_production` is a baseline, never the product.

## What each switch removes, and what that models

* `suppress_duplicates` - off, the Annex A `all` worksheet's verbatim copies compete for
  top-k slots again (F1: 2,080 of 5,145 approved records are second copies). This models
  a pipeline that deduplicates by content hash, which is what QA did, and which could not
  see these copies because the `Tab` column makes `content_search` differ.
* `qualify_roles_by_form` - off, `PRIMARY_SUPPORT` is counted wherever the corpus wrote
  it, including on the 4,244 records whose form cannot bear a recommendation (F2). This
  models trusting the corpus's own role labels, which is the obvious implementation.
* `enforce_role_gate` - off, retrieval never withholds on role completeness at all; any
  non-empty result set proceeds to generation. This is the ordinary RAG arrangement.
* `enforce_model_sufficiency` - off, a model that reports insufficient evidence is
  overridden and its claims are rendered anyway. This models a pipeline with no
  abstention path, and it is the switch most likely to manufacture a wrong answer.
* `discard_ungrounded_claims` - off, a claim citing evidence that was never retrieved is
  rendered rather than discarded. This models citation-by-assertion: the claim keeps a
  citation the pipeline never checked.

## Reading a baseline number

Turning these off *raises* `p_answered` mechanically, and a higher `p_answered` is not a
better system. Without the per-question correctness classification the difference between
profiles is a difference in **rendering behaviour only** - it says how many more answers
the naive pipeline emits, and cannot say how many of those are wrong. `ANSWERED_WRONG` is
the quantity that would make the comparison an argument for the chain rather than a
description of it, and it is unmeasured. Any report built on these profiles states that.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class AblationProfile:
    """Which safety mechanisms are active. All-on is the production system."""

    suppress_duplicates: bool = True
    qualify_roles_by_form: bool = True
    enforce_role_gate: bool = True
    enforce_model_sufficiency: bool = True
    discard_ungrounded_claims: bool = True

    @property
    def is_production(self) -> bool:
        """True when every mechanism is on, i.e. this is the measured system."""

        return all(getattr(self, field.name) for field in fields(self))

    @property
    def disabled(self) -> tuple[str, ...]:
        """The mechanisms this profile switches off, for stamping into a run's output."""

        return tuple(
            field.name for field in fields(self) if not getattr(self, field.name)
        )

    def describe(self) -> str:
        if self.is_production:
            return "production (all safety mechanisms active)"
        return "ablated: " + ", ".join(f"-{name}" for name in self.disabled)


# The measured system. Bound to a name so call sites read as a decision rather than as a
# default argument that happens to be empty.
PRODUCTION = AblationProfile()

# Every mechanism off: the ordinary RAG arrangement this project argues against. The
# comparison baseline, and never a serving configuration.
NAIVE_BASELINE = AblationProfile(
    suppress_duplicates=False,
    qualify_roles_by_form=False,
    enforce_role_gate=False,
    enforce_model_sufficiency=False,
    discard_ungrounded_claims=False,
)
