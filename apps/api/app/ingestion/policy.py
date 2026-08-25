import ipaddress
from urllib.parse import urlsplit


class PublisherPolicyError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class PublisherDomainPolicy:
    def __init__(self, allowed_domains: frozenset[str]) -> None:
        if not allowed_domains:
            raise ValueError("publisher policy requires at least one allowed domain")
        self.allowed_domains = allowed_domains

    def validate_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as error:
            raise PublisherPolicyError("SOURCE_URL_INVALID", "source URL is invalid") from error

        if parsed.scheme.lower() != "https":
            raise PublisherPolicyError(
                "SOURCE_URL_NOT_HTTPS", "publisher acquisitions require HTTPS"
            )
        if parsed.username is not None or parsed.password is not None:
            raise PublisherPolicyError(
                "SOURCE_URL_USERINFO_FORBIDDEN", "source URL cannot include user information"
            )
        if port not in {None, 443}:
            raise PublisherPolicyError(
                "SOURCE_URL_PORT_FORBIDDEN", "publisher acquisitions require the HTTPS port"
            )
        hostname = parsed.hostname
        if hostname is None:
            raise PublisherPolicyError("SOURCE_URL_INVALID", "source URL requires a hostname")
        try:
            normalized = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise PublisherPolicyError(
                "SOURCE_URL_INVALID", "source URL hostname is invalid"
            ) from error
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            pass
        else:
            raise PublisherPolicyError(
                "SOURCE_URL_IP_LITERAL_FORBIDDEN", "source URL cannot use an IP literal"
            )

        if not any(
            normalized == domain or normalized.endswith(f".{domain}")
            for domain in self.allowed_domains
        ):
            raise PublisherPolicyError(
                "PUBLISHER_DOMAIN_NOT_ALLOWED",
                "source URL does not belong to the registered publisher",
            )
        return normalized
