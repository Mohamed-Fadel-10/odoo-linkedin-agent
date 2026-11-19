from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError, field_validator

from odoo.client import OdooClient
from providers.base import CompanyInfo, Employee, ProviderResult
from providers.hunter import HunterProvider


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RecordStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class ContactLookup(Protocol):
    def search_by_emails(self, emails: Sequence[str]) -> list[dict[str, Any]]: ...

    def search_by_linkedin_urls(self, urls: Sequence[str]) -> list[dict[str, Any]]: ...

    def search_by_names(self, names: Sequence[str]) -> list[dict[str, Any]]: ...


class OdooContactLookup:
    def __init__(self, client: OdooClient) -> None:
        self._client = client

    def search_by_emails(self, emails: Sequence[str]) -> list[dict[str, Any]]:
        unique = sorted({e.strip().lower() for e in emails if e and e.strip()})
        if not unique:
            return []
        domain = _or_domain([["email", "=ilike", email] for email in unique])
        return self._client._call(  # noqa: SLF001 — intentional read-only reuse
            "res.partner",
            "search_read",
            domain=domain,
            fields=OdooClient.PARTNER_FIELDS,
            limit=max(len(unique) * 5, 50),
        )

    def search_by_linkedin_urls(self, urls: Sequence[str]) -> list[dict[str, Any]]:
        unique = sorted({u for u in urls if u})
        if not unique:
            return []
        domain = _or_domain([["x_linkedin_url", "=", url] for url in unique])
        return self._client._call(  # noqa: SLF001
            "res.partner",
            "search_read",
            domain=domain,
            fields=OdooClient.PARTNER_FIELDS,
            limit=max(len(unique) * 5, 50),
        )

    def search_by_names(self, names: Sequence[str]) -> list[dict[str, Any]]:
        unique = sorted({n.strip() for n in names if n and n.strip()})
        if not unique:
            return []
        domain = _or_domain([["name", "=ilike", name] for name in unique])
        return self._client._call(  # noqa: SLF001
            "res.partner",
            "search_read",
            domain=domain + [["is_company", "=", False]],
            fields=OdooClient.PARTNER_FIELDS,
            limit=max(len(unique) * 10, 50),
        )


class ProcessedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    run_id: str
    status: RecordStatus
    source: str = "hunter"
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    name: str | None = None
    email: str | None = None
    title: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    domain: str | None = None
    seniority: str | None = None
    department: str | None = None
    twitter: str | None = None
    email_confidence: int | None = Field(default=None, ge=0, le=100)
    email_verification: str | None = None
    sources_count: int | None = Field(default=None, ge=0)
    first_source_url: str | None = None
    company_info: CompanyInfo | None = None
    name_key: str | None = None
    company_key: str | None = None
    odoo_values: dict[str, Any] | None = None


class ProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    accepted: list[ProcessedRecord]
    duplicates: list[ProcessedRecord]
    possible_duplicates: list[ProcessedRecord]
    skipped: list[ProcessedRecord]
    rejected: list[ProcessedRecord]
    counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    company: CompanyInfo | None = None

    @property
    def all_records(self) -> list[ProcessedRecord]:
        return (
            self.accepted
            + self.duplicates
            + self.possible_duplicates
            + self.skipped
            + self.rejected
        )


class _RawEmployee(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str | None = None
    email: str | None = None
    title: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    domain: str | None = None
    source: str = "hunter"
    seniority: str | None = None
    department: str | None = None
    twitter: str | None = None
    email_confidence: int | None = None
    email_verification: str | None = None
    sources_count: int | None = None
    first_source_url: str | None = None

    @field_validator(
        "name",
        "email",
        "title",
        "phone",
        "location",
        "linkedin_url",
        "company",
        "domain",
        "seniority",
        "department",
        "twitter",
        "email_verification",
        "first_source_url",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("email_confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            as_int = int(value)
            return as_int if 0 <= as_int <= 100 else None
        return None

    @field_validator("sources_count", mode="before")
    @classmethod
    def _coerce_sources_count(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            as_int = int(value)
            return as_int if as_int >= 0 else None
        return None


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def normalize_domain(domain: str | None) -> str | None:
    if domain is None:
        return None
    cleaned = domain.strip().lower()
    cleaned = cleaned.removeprefix("http://").removeprefix("https://")
    cleaned = cleaned.split("/")[0]
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned or None


def normalize_linkedin_url(url: str | None) -> str | None:
    return HunterProvider.normalize_linkedin_url(url)


def comparison_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value.strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = " ".join(text.split())
    return text.casefold() or None


def make_record_id(
    *,
    email: str | None,
    linkedin_url: str | None,
    name_key: str | None,
    company_key: str | None,
) -> str:
    if email:
        basis = f"email:{email}"
    elif linkedin_url:
        basis = f"linkedin:{linkedin_url}"
    else:
        basis = f"name_company:{name_key or ''}|{company_key or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def process_provider_result(
    result: ProviderResult,
    *,
    run_id: str,
    odoo: ContactLookup | None = None,
) -> ProcessingResult:
    run = run_id.strip()
    if not run:
        raise ValueError("run_id is required")

    records: list[ProcessedRecord] = []
    global_warnings: list[str] = []

    for skipped in result.skipped:
        records.append(
            ProcessedRecord(
                record_id=make_record_id(
                    email=normalize_email(skipped.raw_email),
                    linkedin_url=None,
                    name_key=comparison_key(skipped.raw_name),
                    company_key=None,
                ),
                run_id=run,
                status=RecordStatus.REJECTED,
                source="hunter",
                reason=skipped.reason,
                name=skipped.raw_name,
                email=skipped.raw_email,
            )
        )

    company_info = result.company
    pending: list[ProcessedRecord] = []
    for employee in result.employees:
        pending.append(
            _normalize_and_validate(employee, run_id=run, company_info=company_info)
        )

    _classify_batch(pending)
    if odoo is not None:
        _classify_against_odoo(pending, odoo)

    records.extend(pending)

    accepted = [r for r in records if r.status == RecordStatus.ACCEPTED]
    duplicates = [r for r in records if r.status == RecordStatus.DUPLICATE]
    possible = [r for r in records if r.status == RecordStatus.POSSIBLE_DUPLICATE]
    skipped_out = [r for r in records if r.status == RecordStatus.SKIPPED]
    rejected = [r for r in records if r.status == RecordStatus.REJECTED]

    for record in accepted:
        record.odoo_values = _odoo_values(record)

    counts = {
        "accepted": len(accepted),
        "duplicates": len(duplicates),
        "possible_duplicates": len(possible),
        "skipped": len(skipped_out),
        "rejected": len(rejected),
        "total": len(records),
    }

    return ProcessingResult(
        run_id=run,
        accepted=accepted,
        duplicates=duplicates,
        possible_duplicates=possible,
        skipped=skipped_out,
        rejected=rejected,
        counts=counts,
        warnings=global_warnings,
        company=company_info,
    )


def _normalize_and_validate(
    employee: Employee | dict[str, Any],
    *,
    run_id: str,
    company_info: CompanyInfo | None = None,
) -> ProcessedRecord:
    raw = (
        _RawEmployee.model_validate(employee)
        if isinstance(employee, dict)
        else _RawEmployee.model_validate(employee.model_dump())
    )

    warnings: list[str] = []
    email = normalize_email(raw.email)
    domain = normalize_domain(raw.domain)
    company = raw.company.strip() if isinstance(raw.company, str) and raw.company.strip() else raw.company
    name = raw.name.strip() if isinstance(raw.name, str) and raw.name.strip() else raw.name

    linkedin_url = None
    if raw.linkedin_url is not None:
        linkedin_url = normalize_linkedin_url(raw.linkedin_url)
        if linkedin_url is None:
            warnings.append("invalid LinkedIn URL cleared")

    name_key = comparison_key(name)
    company_key = comparison_key(company)
    record_id = make_record_id(
        email=email,
        linkedin_url=linkedin_url,
        name_key=name_key,
        company_key=company_key,
    )

    base = {
        "record_id": record_id,
        "run_id": run_id,
        "source": raw.source or "hunter",
        "warnings": warnings,
        "name": name,
        "email": email,
        "title": raw.title,
        "phone": raw.phone,
        "location": raw.location,
        "linkedin_url": linkedin_url,
        "company": company,
        "domain": domain,
        "seniority": raw.seniority,
        "department": raw.department,
        "twitter": raw.twitter,
        "email_confidence": raw.email_confidence,
        "email_verification": raw.email_verification,
        "sources_count": raw.sources_count,
        "first_source_url": raw.first_source_url,
        "company_info": company_info,
        "name_key": name_key,
        "company_key": company_key,
    }

    if not name:
        return ProcessedRecord(**base, status=RecordStatus.SKIPPED, reason="missing name")
    if len(name) > 200 or (company and len(company) > 200):
        return ProcessedRecord(**base, status=RecordStatus.REJECTED, reason="field exceeds max length")
    if email is None:
        return ProcessedRecord(**base, status=RecordStatus.SKIPPED, reason="missing email")
    if not _is_valid_email(email):
        return ProcessedRecord(**base, status=RecordStatus.REJECTED, reason="invalid email")

    if domain and "@" in email:
        email_domain = email.rsplit("@", 1)[-1]
        if email_domain != domain:
            warnings.append(
                f"email domain {email_domain!r} differs from company domain {domain!r}"
            )

    try:
        Employee(
            name=name,
            email=email,
            title=raw.title,
            phone=raw.phone,
            location=raw.location,
            linkedin_url=linkedin_url,
            company=company or "Unknown",
            domain=domain or "unknown.invalid",
            source=raw.source or "hunter",
            seniority=raw.seniority,
            department=raw.department,
            twitter=raw.twitter,
            email_confidence=raw.email_confidence,
            email_verification=raw.email_verification,
            sources_count=raw.sources_count,
            first_source_url=raw.first_source_url,
        )
    except ValidationError:
        return ProcessedRecord(**base, status=RecordStatus.REJECTED, reason="invalid field values")

    return ProcessedRecord(**base, status=RecordStatus.ACCEPTED)


def _classify_batch(records: list[ProcessedRecord]) -> None:
    active = [
        r
        for r in records
        if r.status == RecordStatus.ACCEPTED
    ]

    by_email: dict[str, list[ProcessedRecord]] = {}
    by_linkedin: dict[str, list[ProcessedRecord]] = {}
    by_name_company: dict[tuple[str, str], list[ProcessedRecord]] = {}

    for record in active:
        if record.email:
            by_email.setdefault(record.email, []).append(record)
        if record.linkedin_url:
            by_linkedin.setdefault(record.linkedin_url, []).append(record)
        if record.name_key and record.company_key:
            by_name_company.setdefault(
                (record.name_key, record.company_key),
                [],
            ).append(record)

    for record in active:
        if record.status != RecordStatus.ACCEPTED:
            continue
        conflict = _find_identifier_conflict(record, active)
        if conflict is not None:
            record.status = RecordStatus.POSSIBLE_DUPLICATE
            record.reason = (
                f"conflicting identifiers with batch record_id={conflict.record_id}"
            )

    for email, group in by_email.items():
        kept = [r for r in group if r.status == RecordStatus.ACCEPTED]
        if len(kept) <= 1:
            continue
        primary = kept[0]
        for dup in kept[1:]:
            if dup.linkedin_url and primary.linkedin_url and dup.linkedin_url != primary.linkedin_url:
                continue
            dup.status = RecordStatus.DUPLICATE
            dup.reason = f"duplicate email matches batch record_id={primary.record_id}"

    for linkedin, group in by_linkedin.items():
        kept = [r for r in group if r.status == RecordStatus.ACCEPTED]
        if len(kept) <= 1:
            continue
        primary = kept[0]
        for dup in kept[1:]:
            if dup.email and primary.email and dup.email != primary.email:
                continue
            dup.status = RecordStatus.DUPLICATE
            dup.reason = (
                f"duplicate LinkedIn URL matches batch record_id={primary.record_id}"
            )

    for group in by_name_company.values():
        reviewable = [
            r
            for r in group
            if r.status in {RecordStatus.ACCEPTED, RecordStatus.POSSIBLE_DUPLICATE}
        ]
        if len(reviewable) <= 1:
            continue
        emails = {r.email for r in reviewable if r.email}
        linkedins = {r.linkedin_url for r in reviewable if r.linkedin_url}
        if len(emails) <= 1 and len(linkedins) <= 1:
            continue
        primary = reviewable[0]
        for other in reviewable[1:]:
            if other.status == RecordStatus.ACCEPTED:
                other.status = RecordStatus.POSSIBLE_DUPLICATE
                other.reason = (
                    f"name+company match with batch record_id={primary.record_id}"
                )
            if primary.status == RecordStatus.ACCEPTED:
                primary.status = RecordStatus.POSSIBLE_DUPLICATE
                primary.reason = (
                    primary.reason
                    or f"name+company match with batch record_id={other.record_id}"
                )


def _classify_against_odoo(records: list[ProcessedRecord], odoo: ContactLookup) -> None:
    candidates = [
        r
        for r in records
        if r.status in {RecordStatus.ACCEPTED, RecordStatus.POSSIBLE_DUPLICATE}
    ]
    if not candidates:
        return

    emails = [r.email for r in candidates if r.email]
    linkedins = [r.linkedin_url for r in candidates if r.linkedin_url]
    names = [r.name for r in candidates if r.name]

    odoo_by_email = {
        str(p.get("email", "")).strip().lower(): p
        for p in odoo.search_by_emails(emails)
        if p.get("email")
    }
    odoo_by_linkedin = {
        p.get("x_linkedin_url"): p
        for p in odoo.search_by_linkedin_urls(linkedins)
        if p.get("x_linkedin_url")
    }
    odoo_name_hits = odoo.search_by_names(names)

    for record in candidates:
        if record.email and record.email in odoo_by_email:
            partner = odoo_by_email[record.email]
            record.status = RecordStatus.DUPLICATE
            record.reason = f"duplicate email matches Odoo partner id={partner.get('id')}"
            continue

        if record.linkedin_url and record.linkedin_url in odoo_by_linkedin:
            partner = odoo_by_linkedin[record.linkedin_url]
            record.status = RecordStatus.DUPLICATE
            record.reason = (
                f"duplicate LinkedIn URL matches Odoo partner id={partner.get('id')}"
            )
            continue

        if record.status != RecordStatus.ACCEPTED:
            continue

        for partner in odoo_name_hits:
            partner_name_key = comparison_key(
                partner.get("name") if isinstance(partner.get("name"), str) else None
            )
            partner_company = _partner_company_name(partner)
            partner_company_key = comparison_key(partner_company)
            if (
                record.name_key
                and partner_name_key
                and record.name_key == partner_name_key
                and record.company_key
                and partner_company_key
                and record.company_key == partner_company_key
            ):
                record.status = RecordStatus.POSSIBLE_DUPLICATE
                record.reason = (
                    f"name+company match with Odoo partner id={partner.get('id')}"
                )
                break


def _find_identifier_conflict(
    record: ProcessedRecord,
    others: Sequence[ProcessedRecord],
) -> ProcessedRecord | None:
    for other in others:
        if other is record:
            continue
        if other.status not in {
            RecordStatus.ACCEPTED,
            RecordStatus.POSSIBLE_DUPLICATE,
            RecordStatus.DUPLICATE,
        }:
            continue
        same_email = bool(record.email and other.email and record.email == other.email)
        same_linkedin = bool(
            record.linkedin_url
            and other.linkedin_url
            and record.linkedin_url == other.linkedin_url
        )
        different_email = bool(
            record.email and other.email and record.email != other.email
        )
        different_linkedin = bool(
            record.linkedin_url
            and other.linkedin_url
            and record.linkedin_url != other.linkedin_url
        )
        if same_email and different_linkedin:
            return other
        if same_linkedin and different_email:
            return other
    return None


def build_contact_comment(record: ProcessedRecord) -> str | None:
    lines: list[str] = []
    if (record.source or "").strip().lower() == "hunter":
        lines.append("Source: Hunter.io")
    elif record.source:
        lines.append(f"Source: {record.source}")
    if record.department:
        lines.append(f"Department: {record.department}")
    if record.seniority:
        lines.append(f"Seniority: {record.seniority}")
    if record.email_confidence is not None:
        lines.append(f"Email confidence: {record.email_confidence}")
    if record.email_verification:
        lines.append(f"Email verification: {record.email_verification}")
    if record.twitter:
        lines.append(f"Twitter: {record.twitter}")
    if record.sources_count is not None:
        lines.append(f"Sources: {record.sources_count}")
    if record.first_source_url:
        lines.append(f"First source: {record.first_source_url}")
    return "\n".join(lines) if lines else None


def _odoo_values(record: ProcessedRecord) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": record.name,
        "email": record.email,
        "company_name": record.company,
    }
    if record.phone:
        values["phone"] = record.phone
    if record.title:
        values["function"] = record.title
    if record.linkedin_url:
        values["x_linkedin_url"] = record.linkedin_url
    comment = build_contact_comment(record)
    if comment:
        values["comment"] = comment
    return values


def _partner_company_name(partner: dict[str, Any]) -> str | None:
    parent = partner.get("parent_id")
    if isinstance(parent, (list, tuple)) and len(parent) >= 2:
        return str(parent[1])
    if isinstance(parent, str):
        return parent
    return None


_EMAIL_ADAPTER = TypeAdapter(EmailStr)


def _is_valid_email(email: str) -> bool:
    if not _EMAIL_RE.match(email):
        return False
    try:
        _EMAIL_ADAPTER.validate_python(email)
        return True
    except ValidationError:
        return False


def _or_domain(leaves: list[list[Any]]) -> list[Any]:
    if not leaves:
        return []
    if len(leaves) == 1:
        return leaves
    return ["|"] * (len(leaves) - 1) + leaves
