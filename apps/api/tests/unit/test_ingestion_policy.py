import socket

import httpx
import pytest
from pydantic import ValidationError

from app.ingestion.downloader import AcquisitionDownloadError, AsyncPDFDownloader
from app.ingestion.policy import PublisherDomainPolicy, PublisherPolicyError
from app.schemas.ingestion import PublisherCreate


def test_publisher_policy_accepts_exact_domain_and_subdomains() -> None:
    policy = PublisherDomainPolicy(frozenset({"guidelines.example.org"}))

    assert (
        policy.validate_url("https://guidelines.example.org/guide.pdf")
        == "guidelines.example.org"
    )
    assert (
        policy.validate_url("https://cdn.guidelines.example.org/guide.pdf")
        == "cdn.guidelines.example.org"
    )


@pytest.mark.parametrize(
    ("url", "reason_code"),
    [
        ("http://guidelines.example.org/guide.pdf", "SOURCE_URL_NOT_HTTPS"),
        ("https://guidelines.example.org.evil.test/guide.pdf", "PUBLISHER_DOMAIN_NOT_ALLOWED"),
        ("https://guidelines.example.org:8443/guide.pdf", "SOURCE_URL_PORT_FORBIDDEN"),
        (
            "https://user@guidelines.example.org/guide.pdf",
            "SOURCE_URL_USERINFO_FORBIDDEN",
        ),
        ("https://127.0.0.1/guide.pdf", "SOURCE_URL_IP_LITERAL_FORBIDDEN"),
    ],
)
def test_publisher_policy_rejects_unsafe_source_urls(url: str, reason_code: str) -> None:
    policy = PublisherDomainPolicy(frozenset({"guidelines.example.org"}))

    with pytest.raises(PublisherPolicyError) as caught:
        policy.validate_url(url)

    assert caught.value.reason_code == reason_code


def test_publisher_contract_normalizes_domains_and_rejects_unknown_fields() -> None:
    publisher = PublisherCreate(
        name="Synthetic Society",
        allowed_domains=["Guidelines.Example.Org."],
    )

    assert publisher.allowed_domains == {"guidelines.example.org"}
    with pytest.raises(ValidationError):
        PublisherCreate(
            name="Synthetic Society",
            allowed_domains=["guidelines.example.org"],
            trust_override=True,
        )


async def test_downloader_revalidates_publisher_policy_after_redirect() -> None:
    async def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://untrusted.example.test/guide.pdf"},
            request=request,
        )

    downloader = AsyncPDFDownloader(
        max_bytes=1024,
        timeout_seconds=2,
        allow_private_networks=True,
        transport=httpx.MockTransport(redirect),
    )
    policy = PublisherDomainPolicy(frozenset({"guidelines.example.org"}))

    with pytest.raises(PublisherPolicyError) as caught:
        await downloader.download("https://guidelines.example.org/redirect", policy)

    assert caught.value.reason_code == "PUBLISHER_DOMAIN_NOT_ALLOWED"


async def test_downloader_rejects_domains_resolving_to_private_networks(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    downloader = AsyncPDFDownloader(max_bytes=1024, timeout_seconds=2)
    policy = PublisherDomainPolicy(frozenset({"guidelines.example.org"}))

    with pytest.raises(AcquisitionDownloadError) as caught:
        await downloader.download("https://guidelines.example.org/guide.pdf", policy)

    assert caught.value.reason_code == "PRIVATE_NETWORK_DESTINATION"
