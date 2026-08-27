"""The research serving path's refusal behaviour and the claim it makes about a release.

The risk this module carries is not that it fails - a failure at startup is loud and
fixable. It is that it succeeds while quietly overstating what it is serving, because the
difference between an activated release and a validated one is invisible in every field
except the two tested here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.config import Settings
from app.reasoning.serving_bootstrap import (
    ServingConfigurationError,
    ServingRuntime,
    build_serving_runtime,
)
from app.schemas.corpus import ActiveCorpusRelease, ReleaseServingMode


class _Details:
    async def evidence_details(self, corpus_release_id, evidence_ids):  # pragma: no cover
        return []


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class TestSwitchedOff:
    def test_absent_by_default(self):
        """The default deployment has no serving path and must not acquire one silently."""

        runtime = build_serving_runtime(_settings(), evidence_details_provider=_Details())
        assert runtime is None

    def test_none_is_not_an_error(self):
        """None is a deployment state: the API abstains with a reason that names it."""

        assert (
            build_serving_runtime(
                _settings(serving_enabled=False), evidence_details_provider=_Details()
            )
            is None
        )


class TestPartialConfigurationIsRefused:
    """A half-wired serving path answers nothing while looking wired.

    Left to degrade, it would abstain with a reason describing the wrong layer, and a
    reader would record a corpus limit where there was a missing environment variable.
    """

    def test_enabled_without_a_bundle_refuses(self):
        with pytest.raises(ServingConfigurationError, match="SERVING_RELEASE_BUNDLE_PATH"):
            build_serving_runtime(
                _settings(serving_enabled=True), evidence_details_provider=_Details()
            )

    def test_enabled_without_vectors_refuses(self, tmp_path):
        with pytest.raises(ServingConfigurationError, match="SERVING_VECTORS_PATH"):
            build_serving_runtime(
                _settings(
                    serving_enabled=True,
                    serving_release_bundle_path=tmp_path / "bundle.json",
                ),
                evidence_details_provider=_Details(),
            )

    def test_enabled_without_a_collection_refuses(self, tmp_path):
        with pytest.raises(ServingConfigurationError, match="SERVING_QDRANT_COLLECTION"):
            build_serving_runtime(
                _settings(
                    serving_enabled=True,
                    serving_release_bundle_path=tmp_path / "bundle.json",
                    serving_vectors_path=tmp_path / "vectors.json",
                ),
                evidence_details_provider=_Details(),
            )

    def test_the_message_explains_rather_than_names_a_variable(self, tmp_path):
        with pytest.raises(ServingConfigurationError) as raised:
            build_serving_runtime(
                _settings(serving_enabled=True), evidence_details_provider=_Details()
            )
        assert "looks wired and answers nothing" in str(raised.value)


class TestServedReleaseIdentity:
    """What the API tells a client about a release it never activated."""

    def test_research_serving_declares_itself(self):
        release = ActiveCorpusRelease(
            corpus_release_id="CR_test",
            manifest_sha256="a" * 64,
            qdrant_collection="corpus_cr_test--vp-1",
            serving_mode=ReleaseServingMode.RESEARCH_UNACTIVATED,
        )
        assert release.serving_mode is ReleaseServingMode.RESEARCH_UNACTIVATED
        assert release.activated_at is None

    def test_an_unactivated_release_may_not_carry_an_activation_instant(self):
        """The single most misleading value this payload could carry."""

        with pytest.raises(ValueError, match="must not carry activated_at"):
            ActiveCorpusRelease(
                corpus_release_id="CR_test",
                manifest_sha256="a" * 64,
                qdrant_collection="c",
                activated_at=datetime.now(timezone.utc),
                serving_mode=ReleaseServingMode.RESEARCH_UNACTIVATED,
            )

    def test_an_activated_release_must_carry_one(self):
        with pytest.raises(ValueError, match="must carry activated_at"):
            ActiveCorpusRelease(
                corpus_release_id="CR_test",
                manifest_sha256="a" * 64,
                qdrant_collection="c",
                serving_mode=ReleaseServingMode.ACTIVATED,
            )

    def test_the_governed_mode_is_the_default(self):
        """So a payload that omits the field cannot read as research serving by accident."""

        release = ActiveCorpusRelease(
            corpus_release_id="CR_test",
            manifest_sha256="a" * 64,
            qdrant_collection="c",
            activated_at=datetime.now(timezone.utc),
        )
        assert release.serving_mode is ReleaseServingMode.ACTIVATED

    @pytest.mark.asyncio
    async def test_the_runtime_serves_one_constant_release(self):
        """Re-reading per question would let stated provenance drift from loaded evidence."""

        release = ActiveCorpusRelease(
            corpus_release_id="CR_test",
            manifest_sha256="a" * 64,
            qdrant_collection="c",
            serving_mode=ReleaseServingMode.RESEARCH_UNACTIVATED,
        )
        runtime = ServingRuntime(pipeline=object(), release=release)  # type: ignore[arg-type]
        assert await runtime.active_release() is release
        assert await runtime.active_release() is await runtime.active_release()
