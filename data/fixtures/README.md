# Fixtures

Only public guideline material, synthetic cases, and properly deidentified benchmark data may be stored here. Do not add PHI.

`foundation_applicability.json` contains synthetic, executable safety cases for the
canonical eligibility/context contract. Each case is parsed through the production
Pydantic models and evaluated by the production applicability engine in the API safety
test suite. Keep expected reason strings explicit so semantic drift fails the build.

`corpus-release-v1.json` is a synthetic, frozen release bundle for independent API,
web, and corpus-steward development. Its manifest binds three exact evidence records
covering primary support, applicability, and exception roles. The fixture-only
attestation references are contract examples, not production signatures.

`trust-root-synthetic.json` drives deterministic multi-page inventory, drift,
conditional-fetch, failure, and reconciliation tests. It is safe to register only in local
or test databases.
