from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class LinkedInCompanyUrl:
    canonical_url: str
    slug: str
    suggested_company: str
    suggested_domain: str


_LINKEDIN_HOST_SUFFIX = "linkedin.com"
_COMPANY_PATH_PREFIX = "/company/"


def parse_linkedin_company_url(raw_url: str) -> LinkedInCompanyUrl:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("url is required")

    parsed = urlparse(raw_url.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("url must use https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not include userinfo")

    host = (parsed.hostname or "").lower()
    if not _is_linkedin_host(host):
        raise ValueError("url host must be linkedin.com or a linkedin.com subdomain")

    path = parsed.path or ""
    slug = _extract_company_slug(path)
    if slug is None:
        raise ValueError("url path must be /company/<slug>")

    canonical = f"https://{host}{_COMPANY_PATH_PREFIX}{slug}"
    suggested_company = _slug_to_company_label(slug)
    suggested_domain = f"{slug}.com"

    return LinkedInCompanyUrl(
        canonical_url=canonical,
        slug=slug,
        suggested_company=suggested_company,
        suggested_domain=suggested_domain,
    )


def _is_linkedin_host(host: str) -> bool:
    if not host:
        return False
    return host == _LINKEDIN_HOST_SUFFIX or host.endswith(f".{_LINKEDIN_HOST_SUFFIX}")


def _extract_company_slug(path: str) -> str | None:
    if not path.startswith(_COMPANY_PATH_PREFIX):
        return None
    remainder = path[len(_COMPANY_PATH_PREFIX) :]
    if not remainder:
        return None
    slug = remainder.split("/", 1)[0].strip()
    if not slug:
        return None
    if not all(ch.isalnum() or ch in "-_" for ch in slug):
        return None
    return slug.lower()


def _slug_to_company_label(slug: str) -> str:
    parts = [part for part in slug.replace("_", "-").split("-") if part]
    if not parts:
        return slug
    return " ".join(part.capitalize() for part in parts)
