"""Clinical adjudication workflow and guarded benchmark-suite builders."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from app.corpus_steward.adjudication_repository import (
    AdjudicationRepositoryConflictError,
    SQLBenchmarkAdjudicationRepository,
)
from app.corpus_steward.adjudication_schemas import (
    AdjudicatedCaseRecord,
    AdjudicationAgreementReport,
    AdjudicationProcessPolicy,
    AdjudicationProcessPolicyContent,
    AdjudicationSealRequest,
    BenchmarkAccessPolicy,
    BenchmarkAccessPolicyContent,
    BenchmarkAdjudicationRecord,
    BenchmarkAdjudicationRecordContent,
    BenchmarkSuiteBuildRequest,
    BenchmarkSuiteBuildResult,
    BenchmarkThresholdPolicy,
    BenchmarkThresholdPolicyContent,
    ClinicalReviewImport,
    DisagreementResolutionImport,
)
from app.corpus_steward.benchmark_schemas import (
    BenchmarkCase,
    BenchmarkProvenanceMode,
    BenchmarkSuite,
    BenchmarkSuiteContent,
    BenchmarkSuitePartition,
    CaseAdjudication,
    ClinicalSafetyTopic,
)
from app.corpus_steward.schemas import ArtifactKind
from app.corpus_steward.storage import ImmutableStewardArtifactStore
from app.schemas.corpus import CorpusEvidenceRecord, CorpusReleaseBundle, canonical_json_bytes


class AdjudicationWorkflowError(RuntimeError):
    pass


class BenchmarkAccessDeniedError(AdjudicationWorkflowError):
    pass


class ClinicalAdjudicationService:
    def __init__(
        self,
        repository: SQLBenchmarkAdjudicationRepository,
        artifacts: ImmutableStewardArtifactStore,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts

    async def seal_access_policy(
        self, content: BenchmarkAccessPolicyContent
    ) -> BenchmarkAccessPolicy:
        policy = BenchmarkAccessPolicy.seal(content)
        artifact = self._put(
            policy,
            kind=ArtifactKind.BENCHMARK_ACCESS_POLICY,
            media_type="application/vnd.med-rag.benchmark-access-policy+json",
        )
        await self._repository.register_policy(policy, artifact)
        return policy

    async def seal_adjudication_process(
        self, content: AdjudicationProcessPolicyContent
    ) -> AdjudicationProcessPolicy:
        policy = AdjudicationProcessPolicy.seal(content)
        artifact = self._put(
            policy,
            kind=ArtifactKind.BENCHMARK_ADJUDICATION_POLICY,
            media_type="application/vnd.med-rag.adjudication-process-policy+json",
        )
        await self._repository.register_policy(policy, artifact)
        return policy

    async def seal_threshold_policy(
        self, content: BenchmarkThresholdPolicyContent
    ) -> BenchmarkThresholdPolicy:
        policy = BenchmarkThresholdPolicy.seal(content)
        artifact = self._put(
            policy,
            kind=ArtifactKind.BENCHMARK_THRESHOLD_POLICY,
            media_type="application/vnd.med-rag.benchmark-threshold-policy+json",
        )
        await self._repository.register_policy(policy, artifact)
        return policy

    async def import_review_decisions(
        self,
        imported: ClinicalReviewImport,
        *,
        access_policy_sha256: str,
        adjudication_process_sha256: str,
    ) -> None:
        access = await self._repository.access_policy(access_policy_sha256)
        process = await self._repository.adjudication_process(
            adjudication_process_sha256
        )
        artifacts = []
        for decision in imported.decisions:
            content = decision.content
            if content.evidence_access_revision_sha256 != access.policy_sha256:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} used another evidence-access revision"
                )
            if content.instructions_sha256 != process.content.instructions_sha256:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} used another adjudication instruction set"
                )
            if content.clinical_role not in process.content.allowed_clinical_roles:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} has an unapproved clinical role"
                )
            if content.adjudicator_identity in access.content.candidate_team_identities:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} was made by a candidate-team member"
                )
            if content.decided_at < access.content.effective_at:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} predates its evidence-access policy"
                )
            if content.decided_at < process.content.effective_at:
                raise AdjudicationWorkflowError(
                    f"review {content.review_id} predates its adjudication process"
                )
            artifacts.append(
                (
                    decision,
                    self._put(
                        decision,
                        kind=ArtifactKind.BENCHMARK_REVIEW_DECISION,
                        media_type=(
                            "application/vnd.med-rag.clinical-review-decision+json"
                        ),
                    ),
                )
            )
        await self._repository.register_review_decisions(tuple(artifacts))

    async def import_disagreement_resolutions(
        self,
        imported: DisagreementResolutionImport,
        *,
        access_policy_sha256: str,
        adjudication_process_sha256: str,
    ) -> None:
        access = await self._repository.access_policy(access_policy_sha256)
        process = await self._repository.adjudication_process(
            adjudication_process_sha256
        )
        case_ids = tuple(item.content.case_id for item in imported.resolutions)
        decisions = await self._repository.review_decisions(case_ids)
        by_digest = {item.decision_sha256: item for item in decisions}
        artifacts = []
        for resolution in imported.resolutions:
            content = resolution.content
            expected = set(content.review_decision_sha256s)
            if missing := expected - set(by_digest):
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} references unknown decisions: "
                    + ", ".join(sorted(missing))
                )
            resolved_decisions = [by_digest[item] for item in expected]
            if any(item.content.case_id != content.case_id for item in resolved_decisions):
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} mixes decisions from different cases"
                )
            if content.resolver_clinical_role not in process.content.allowed_resolver_roles:
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} has an unapproved resolver role"
                )
            if content.resolver_identity in access.content.candidate_team_identities:
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} was made by a candidate-team member"
                )
            if content.resolved_at < max(item.content.decided_at for item in resolved_decisions):
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} predates a reviewed decision"
                )
            if not content.finalized_case.safety_topics:
                raise AdjudicationWorkflowError(
                    f"resolution {content.resolution_id} finalized a case without safety topics"
                )
            artifacts.append(
                (
                    resolution,
                    self._put(
                        resolution,
                        kind=ArtifactKind.BENCHMARK_RESOLUTION,
                        media_type=(
                            "application/vnd.med-rag.disagreement-resolution+json"
                        ),
                    ),
                )
            )
        await self._repository.register_resolutions(tuple(artifacts))

    async def seal_adjudication(
        self, request: AdjudicationSealRequest
    ) -> BenchmarkAdjudicationRecord:
        access = await self._repository.access_policy(request.access_policy_sha256)
        process = await self._repository.adjudication_process(
            request.adjudication_process_sha256
        )
        decisions = await self._repository.review_decisions(request.case_ids)
        resolutions = await self._repository.resolutions(request.case_ids)
        by_case: dict[str, list] = defaultdict(list)
        for decision in decisions:
            by_case[decision.content.case_id].append(decision)
        resolution_by_case = {item.content.case_id: item for item in resolutions}
        if missing := set(request.case_ids) - set(by_case):
            raise AdjudicationWorkflowError(
                "adjudication cases lack reviewer decisions: " + ", ".join(sorted(missing))
            )

        case_records: list[AdjudicatedCaseRecord] = []
        exact_agreement_count = 0
        review_count = 0
        for case_id in request.case_ids:
            reviews = sorted(by_case[case_id], key=lambda item: item.content.review_id)
            if len(reviews) < process.content.minimum_independent_reviews_per_case:
                raise AdjudicationWorkflowError(
                    f"case {case_id} lacks the required independent review count"
                )
            identities = [item.content.adjudicator_identity for item in reviews]
            if len(identities) != len(set(identities)):
                raise AdjudicationWorkflowError(
                    f"case {case_id} repeats an adjudicator identity"
                )
            if any(
                item.content.evidence_access_revision_sha256 != access.policy_sha256
                or item.content.instructions_sha256
                != process.content.instructions_sha256
                or item.content.clinical_role
                not in process.content.allowed_clinical_roles
                or item.content.adjudicator_identity
                in access.content.candidate_team_identities
                for item in reviews
            ):
                raise AdjudicationWorkflowError(
                    f"case {case_id} contains a review outside the sealed policies"
                )
            partitions = {item.content.suite_partition for item in reviews}
            if len(partitions) != 1:
                raise AdjudicationWorkflowError(
                    f"case {case_id} reviewer decisions disagree on suite partition"
                )
            if any(item.content.decided_at > request.sealed_at for item in reviews):
                raise AdjudicationWorkflowError(
                    f"case {case_id} has a review after adjudication was sealed"
                )

            case_digests = {
                canonical_json_bytes(item.content.decided_case) for item in reviews
            }
            exact_agreement = len(case_digests) == 1
            resolution = resolution_by_case.get(case_id)
            if exact_agreement:
                if resolution is not None:
                    raise AdjudicationWorkflowError(
                        f"case {case_id} has a resolution despite exact reviewer agreement"
                    )
                final_case = reviews[0].content.decided_case
                adjudication = CaseAdjudication(
                    reviews=tuple(item.content.provenance() for item in reviews),
                    disagreement_observed=False,
                    resolution="NOT_REQUIRED",
                )
                resolution_sha256 = None
                exact_agreement_count += 1
            else:
                if resolution is None:
                    raise AdjudicationWorkflowError(
                        f"case {case_id} has unresolved reviewer disagreement"
                    )
                if set(resolution.content.review_decision_sha256s) != {
                    item.decision_sha256 for item in reviews
                }:
                    raise AdjudicationWorkflowError(
                        f"case {case_id} resolution does not cover its exact review set"
                    )
                if resolution.content.resolved_at > request.sealed_at:
                    raise AdjudicationWorkflowError(
                        f"case {case_id} was resolved after adjudication was sealed"
                    )
                final_case = resolution.content.finalized_case
                adjudication = CaseAdjudication(
                    reviews=tuple(item.content.provenance() for item in reviews),
                    disagreement_observed=True,
                    resolution="RESOLVED",
                    resolver_identity=resolution.content.resolver_identity,
                    resolution_note_sha256=resolution.content.resolution_note_sha256,
                    resolved_at=resolution.content.resolved_at,
                )
                resolution_sha256 = resolution.resolution_sha256
            finalized = final_case.model_copy(update={"adjudication": adjudication})
            case_records.append(
                AdjudicatedCaseRecord(
                    case_id=case_id,
                    suite_partition=next(iter(partitions)),
                    finalized_case=finalized,
                    review_decision_sha256s=tuple(
                        item.decision_sha256 for item in reviews
                    ),
                    exact_agreement=exact_agreement,
                    resolution_sha256=resolution_sha256,
                )
            )
            review_count += len(reviews)

        case_count = len(case_records)
        content = BenchmarkAdjudicationRecordContent(
            adjudication_record_id=request.adjudication_record_id,
            access_policy_sha256=access.policy_sha256,
            adjudication_process_sha256=process.policy_sha256,
            cases=tuple(case_records),
            agreement=AdjudicationAgreementReport(
                case_count=case_count,
                independent_review_count=review_count,
                exact_agreement_case_count=exact_agreement_count,
                disagreement_case_count=case_count - exact_agreement_count,
                exact_agreement_rate=exact_agreement_count / case_count,
            ),
            sealed_at=request.sealed_at,
        )
        record = BenchmarkAdjudicationRecord.seal(content)
        artifact = self._put(
            record,
            kind=ArtifactKind.BENCHMARK_ADJUDICATION_RECORD,
            media_type="application/vnd.med-rag.benchmark-adjudication-record+json",
        )
        await self._repository.register_adjudication_record(record, artifact)
        return record

    async def build_suite(
        self,
        request: BenchmarkSuiteBuildRequest,
        bundle: CorpusReleaseBundle,
    ) -> BenchmarkSuiteBuildResult:
        record = await self._repository.adjudication_record(
            request.adjudication_record_sha256
        )
        access = await self._repository.access_policy(
            record.content.access_policy_sha256
        )
        if not access.content.permits(request.actor_identity, request.suite_partition):
            raise BenchmarkAccessDeniedError(
                f"{request.actor_identity} may not build {request.suite_partition.value}"
            )
        process = await self._repository.adjudication_process(
            record.content.adjudication_process_sha256
        )
        threshold = await self._repository.threshold_policy(
            request.threshold_policy_sha256
        )
        if request.requested_at < record.content.sealed_at:
            raise AdjudicationWorkflowError("suite build predates the sealed adjudication record")
        if request.requested_at < threshold.content.established_at:
            raise AdjudicationWorkflowError("suite build predates the threshold policy")
        if request.requested_at < access.content.effective_at:
            raise AdjudicationWorkflowError("suite build predates the access policy")
        if request.requested_at < process.content.effective_at:
            raise AdjudicationWorkflowError("suite build predates the adjudication process")

        existing = await self._repository.suite_for_benchmark(
            request.benchmark_id, request.suite_partition
        )
        if existing is not None:
            self._require_existing_matches(existing, request)
            return existing
        if request.suite_partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
            holdout = await self._repository.holdout_suite_for_record(
                request.adjudication_record_sha256
            )
            if holdout is not None:
                self._require_existing_matches(holdout, request)
                return holdout

        manifest = bundle.manifest.content
        if (
            request.corpus_release_id != manifest.corpus_release_id
            or request.manifest_sha256 != bundle.manifest.manifest_sha256
        ):
            raise AdjudicationWorkflowError(
                "suite build request does not match the exact corpus release"
            )
        cases = tuple(
            item.finalized_case
            for item in record.content.cases
            if item.suite_partition is request.suite_partition
        )
        if not cases:
            raise AdjudicationWorkflowError(
                f"adjudication record has no {request.suite_partition.value} cases"
            )
        self._validate_sample_targets(cases, record, request.suite_partition, threshold)
        self._validate_cases_against_release(cases, bundle)
        suite = BenchmarkSuite.seal(
            BenchmarkSuiteContent(
                benchmark_id=request.benchmark_id,
                suite_partition=request.suite_partition,
                provenance_mode=BenchmarkProvenanceMode.INDEPENDENT_REVIEWED,
                access_policy_sha256=access.policy_sha256,
                adjudication_process_sha256=process.policy_sha256,
                adjudication_record_sha256=record.adjudication_record_sha256,
                threshold_policy_sha256=threshold.policy_sha256,
                required_safety_topics=tuple(ClinicalSafetyTopic),
                candidate_configuration_sha256=(
                    request.candidate_configuration_sha256
                ),
                corpus_release_id=request.corpus_release_id,
                manifest_sha256=request.manifest_sha256,
                cases=cases,
                modes=request.modes,
                candidate_mode=request.candidate_mode,
                top_k=request.top_k,
                acceptance=threshold.content.acceptance_for(
                    request.suite_partition
                ),
            )
        )
        artifact = self._put(
            suite,
            kind=ArtifactKind.BENCHMARK_SUITE,
            media_type="application/vnd.med-rag.retrieval-benchmark-suite+json",
        )
        result = BenchmarkSuiteBuildResult(
            suite=suite,
            artifact_sha256=artifact.sha256,
            storage_key=artifact.storage_key,
            built_by=request.actor_identity,
            built_at=request.requested_at,
        )
        try:
            await self._repository.register_suite(
                result,
                artifact,
                adjudication_record_sha256=record.adjudication_record_sha256,
                threshold_policy_sha256=threshold.policy_sha256,
            )
        except AdjudicationRepositoryConflictError:
            if request.suite_partition is BenchmarkSuitePartition.SEALED_HOLDOUT:
                existing_holdout = await self._repository.holdout_suite_for_record(
                    request.adjudication_record_sha256
                )
                if existing_holdout is not None:
                    self._require_existing_matches(existing_holdout, request)
                    return existing_holdout
            raise
        return result

    @staticmethod
    def _validate_sample_targets(
        cases: tuple[BenchmarkCase, ...],
        record: BenchmarkAdjudicationRecord,
        partition: BenchmarkSuitePartition,
        threshold: BenchmarkThresholdPolicy,
    ) -> None:
        policy = threshold.content
        required_count = policy.minimum_case_count_for(partition)
        acceptance = policy.acceptance_for(partition)
        minimum_count = max(required_count, acceptance.minimum_total_case_count)
        if len(cases) < minimum_count:
            raise AdjudicationWorkflowError(
                f"{partition.value} has {len(cases)} cases; policy requires {minimum_count}"
            )
        topic_counts = Counter(topic for case in cases for topic in case.safety_topics)
        for target in policy.safety_topic_sample_targets:
            required = (
                target.development_minimum
                if partition is BenchmarkSuitePartition.DEVELOPMENT
                else target.sealed_holdout_minimum
            )
            if topic_counts[target.safety_topic] < required:
                raise AdjudicationWorkflowError(
                    f"{partition.value} lacks required {target.safety_topic.value} coverage "
                    f"({topic_counts[target.safety_topic]}/{required})"
                )
        records = [
            item for item in record.content.cases if item.suite_partition is partition
        ]
        exact_rate = sum(item.exact_agreement for item in records) / len(records)
        if exact_rate < policy.minimum_exact_agreement_rate:
            raise AdjudicationWorkflowError(
                f"{partition.value} exact agreement rate {exact_rate:.6f} is below policy"
            )

    @staticmethod
    def _validate_cases_against_release(
        cases: Sequence[BenchmarkCase], bundle: CorpusReleaseBundle
    ) -> None:
        evidence = {item.evidence_id: item for item in bundle.evidence}
        for case in cases:
            referenced = {
                *(item.evidence_id for item in case.gold_evidence),
                *case.forbidden_evidence_ids,
            }
            if missing := referenced - set(evidence):
                raise AdjudicationWorkflowError(
                    f"case {case.case_id} references evidence outside the release: "
                    + ", ".join(sorted(missing))
                )
            gold = [evidence[item.evidence_id] for item in case.gold_evidence]
            if not ClinicalAdjudicationService._records_match_filter(gold, case):
                raise AdjudicationWorkflowError(
                    f"case {case.case_id} retrieval filter excludes its gold evidence"
                )
            roles = {role for item in gold for role in item.evidence_roles}
            if missing_roles := set(case.required_evidence_roles) - roles:
                raise AdjudicationWorkflowError(
                    f"case {case.case_id} gold evidence lacks required roles: "
                    + ", ".join(sorted(item.value for item in missing_roles))
                )

    @staticmethod
    def _records_match_filter(
        records: Sequence[CorpusEvidenceRecord], case: BenchmarkCase
    ) -> bool:
        filters = case.retrieval_filter
        return all(
            (not filters.jurisdictions or item.jurisdiction in filters.jurisdictions)
            and (not filters.languages or item.language in filters.languages)
            and (not filters.publisher_ids or item.publisher_id in filters.publisher_ids)
            and (
                not filters.source_version_ids
                or item.source_version_id in filters.source_version_ids
            )
            and (
                not filters.lifecycle_statuses
                or item.lifecycle_status.value in filters.lifecycle_statuses
            )
            for item in records
        )

    @staticmethod
    def _require_existing_matches(
        existing: BenchmarkSuiteBuildResult,
        request: BenchmarkSuiteBuildRequest,
    ) -> None:
        content = existing.suite.content
        expected = (
            content.benchmark_id == request.benchmark_id
            and content.suite_partition is request.suite_partition
            and content.adjudication_record_sha256
            == request.adjudication_record_sha256
            and content.threshold_policy_sha256 == request.threshold_policy_sha256
            and content.candidate_configuration_sha256
            == request.candidate_configuration_sha256
            and content.corpus_release_id == request.corpus_release_id
            and content.manifest_sha256 == request.manifest_sha256
            and content.modes == request.modes
            and content.candidate_mode is request.candidate_mode
            and content.top_k == request.top_k
            and existing.built_by == request.actor_identity
            and existing.built_at == request.requested_at
        )
        if not expected:
            raise AdjudicationRepositoryConflictError(
                "immutable benchmark suite build already exists with different inputs"
            )

    def _put(self, value, *, kind: ArtifactKind, media_type: str):
        return self._artifacts.put(
            canonical_json_bytes(value),
            media_type=media_type,
            kind=kind,
        )
