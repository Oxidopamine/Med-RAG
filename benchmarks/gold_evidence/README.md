# Gold evidence sets

Each benchmark case references a minimum complete evidence set, including required
support, qualifier, exception, and applicability roles. Empty placeholders are not treated
as gold labels. Gold IDs, graded relevance, required-set membership, safety-leakage IDs,
and evidence-role requirements are sealed together in the suite under `../suites/`.

For `AUTOMATED_SOURCE_DERIVED` cases, each label also carries the exact canonical evidence
digest, derivation rule/version, and secret-ranked partition-assignment digest. No reviewer or
approval decision is part of benchmark construction.
