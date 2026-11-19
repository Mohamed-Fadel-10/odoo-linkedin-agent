from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from .base import CompanyInfo, Employee, ProviderResult, SkippedRecord


load_dotenv()

logger = logging.getLogger(__name__)

HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)
_LINKEDIN_IN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/([^/?#\s]+)/?",
    re.IGNORECASE,
)


class HunterError(Exception):
    pass


class HunterAuthError(HunterError):
    pass


class HunterCreditsExhaustedError(HunterError):
    pass


class HunterRateLimitError(HunterError):
    pass


class HunterTimeoutError(HunterError):
    pass


class HunterNetworkError(HunterError):
    pass


class HunterResponseError(HunterError):
    pass


class HunterPaginationError(HunterResponseError):
    pass


DEFAULT_LIMIT = 10
MAX_LIMIT = 100
FREE_PLAN_RESULT_CAP = 10


def resolve_hunter_limit(limit: int | None = None) -> int:
    if limit is not None:
        value = limit
    else:
        raw = os.getenv("HUNTER_LIMIT", "").strip()
        if not raw:
            raw = os.getenv("HUNTER_PAGE_SIZE", str(DEFAULT_LIMIT)).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"HUNTER_LIMIT must be an integer between 1 and {MAX_LIMIT}, got {raw!r}"
            ) from exc
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(
            f"HUNTER_LIMIT must be between 1 and {MAX_LIMIT}, got {value}"
        )
    return value


class HunterProvider:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        limit: int | None = None,
        page_size: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("HUNTER_API_KEY", "")).strip()
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.limit = resolve_hunter_limit(limit if limit is not None else page_size)
        self.session = session or requests.Session()

    @property
    def page_size(self) -> int:
        return self.limit

    @page_size.setter
    def page_size(self, value: int) -> None:
        self.limit = resolve_hunter_limit(value)

    def find_employees(self, company: str, domain: str) -> ProviderResult:
        company_label = company.strip()
        domain_clean = domain.strip().lower().removeprefix("http://").removeprefix("https://")
        domain_clean = domain_clean.split("/")[0]

        if not company_label:
            raise ValueError("Company name is required")
        if not self._is_valid_domain(domain_clean):
            raise ValueError(f"Invalid domain format: {domain_clean!r}")
        if not self.api_key:
            raise HunterAuthError("Hunter API key is missing")

        payload = self._domain_search(domain_clean, company_label)
        return self._map_response(payload, company=company_label, domain=domain_clean)

    @staticmethod
    def _is_valid_domain(domain: str) -> bool:
        return bool(domain) and bool(_DOMAIN_RE.match(domain))

    @staticmethod
    def normalize_linkedin_url(value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None
        match = _LINKEDIN_IN_RE.search(raw)
        if not match:
            parsed = urlparse(raw if "://" in raw else f"https://{raw}")
            if "linkedin.com" not in (parsed.hostname or "").lower():
                return None
            match = _LINKEDIN_IN_RE.search(raw)
            if not match:
                return None
        slug = match.group(1).strip("/")
        if not slug or "/" in slug:
            return None
        return f"https://www.linkedin.com/in/{slug}"

    def _domain_search(self, domain: str, company: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            payload = self._domain_search_page(
                domain, company, limit=self.limit, offset=0
            )
        except HunterPaginationError as exc:
            raise HunterPaginationError(
                f"Hunter plan limit: limit+offset must stay within the plan cap "
                f"(free plans typically allow at most {FREE_PLAN_RESULT_CAP} results; "
                f"lower HUNTER_LIMIT, currently {self.limit})."
            ) from exc
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "discovery_timing hunter_ms=%s domain=%s limit=%s",
                elapsed_ms,
                domain,
                self.limit,
            )

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise HunterResponseError("Hunter response data must be an object")
        emails = data.get("emails") or []
        if not isinstance(emails, list):
            raise HunterResponseError("Hunter response emails must be a list")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        base_data = dict(data)
        base_data["emails"] = emails
        return {
            "data": base_data,
            "meta": {
                **meta,
                "limit": self.limit,
                "offset": 0,
                "fetched": len(emails),
            },
        }

    def _domain_search_page(
        self,
        domain: str,
        company: str,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        headers = {
            "X-API-KEY": self.api_key,
            "User-Agent": "LinkedIn-Agent/1.0",
            "Accept": "application/json",
        }
        params = {
            "domain": domain,
            "company": company,
            "limit": limit,
            "offset": offset,
            "type": "personal",
        }

        attempts = self.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self.session.get(
                    HUNTER_DOMAIN_SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                last_error = HunterTimeoutError("Hunter request timed out")
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise last_error from exc
            except requests.RequestException as exc:
                last_error = HunterNetworkError("Hunter network error")
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise last_error from exc

            status = response.status_code

            if status == 401:
                raise HunterAuthError("Hunter authentication failed")

            if status in {403, 429} or status >= 500:
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                if status == 429:
                    raise HunterCreditsExhaustedError("Hunter usage limit reached")
                if status == 403:
                    raise HunterRateLimitError("Hunter rate limit reached")
                raise HunterNetworkError(f"Hunter server error ({status})")

            if status >= 400:
                body_text = ""
                try:
                    err_payload = response.json()
                    if isinstance(err_payload, dict):
                        body_text = json.dumps(err_payload).lower()
                except ValueError:
                    body_text = ""
                if "pagination" in body_text:
                    raise HunterPaginationError(
                        f"Hunter plan limit: limit+offset above the plan cap "
                        f"(free plans typically allow at most {FREE_PLAN_RESULT_CAP} "
                        f"results; lower HUNTER_LIMIT)."
                    )
                raise HunterResponseError(f"Hunter request failed ({status})")

            try:
                payload = response.json()
            except ValueError as exc:
                raise HunterResponseError("Hunter returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise HunterResponseError("Hunter returned unexpected JSON shape")
            return payload

        if last_error:
            raise last_error
        raise HunterNetworkError("Hunter request failed")

    def _map_response(
        self,
        payload: dict[str, Any],
        *,
        company: str,
        domain: str,
    ) -> ProviderResult:
        data = payload.get("data") or {}
        emails = data.get("emails") or []
        if not isinstance(emails, list):
            raise HunterResponseError("Hunter response emails must be a list")

        location = self._company_location(data)
        company_info = self._map_company(data)
        employees: list[Employee] = []
        skipped: list[SkippedRecord] = []
        merged_count = 0

        by_email: dict[str, int] = {}
        by_linkedin: dict[str, int] = {}

        for item in emails:
            if not isinstance(item, dict):
                skipped.append(SkippedRecord(reason="invalid record shape"))
                continue

            raw_email = item.get("value")
            raw_name = self._build_name(item.get("first_name"), item.get("last_name"))
            if not raw_name:
                skipped.append(
                    SkippedRecord(
                        reason="missing name",
                        raw_email=str(raw_email) if raw_email is not None else None,
                        raw_name=None,
                    )
                )
                continue

            linkedin_url = self.normalize_linkedin_url(
                item.get("linkedin") if isinstance(item.get("linkedin"), str) else None
            )
            extras = self._map_person_extras(item)

            try:
                employee = Employee(
                    name=raw_name,
                    email=str(raw_email).strip() if raw_email is not None else "",
                    title=self._optional_str(item.get("position"), 200),
                    phone=self._optional_str(item.get("phone_number"), 50),
                    location=location,
                    linkedin_url=linkedin_url,
                    company=company,
                    domain=domain,
                    source="hunter",
                    **extras,
                )
            except ValidationError:
                skipped.append(
                    SkippedRecord(
                        reason="invalid email",
                        raw_email=str(raw_email) if raw_email is not None else None,
                        raw_name=raw_name,
                    )
                )
                continue

            email_key = str(employee.email).lower()
            linkedin_key = employee.linkedin_url

            existing_idx: int | None = None
            if email_key in by_email:
                existing_idx = by_email[email_key]
            elif linkedin_key and linkedin_key in by_linkedin:
                existing_idx = by_linkedin[linkedin_key]

            if existing_idx is not None:
                existing = employees[existing_idx]
                if self._identifiers_conflict(existing, employee):
                    idx = len(employees)
                    employees.append(employee)
                    by_email[email_key] = idx
                    if linkedin_key:
                        by_linkedin[linkedin_key] = idx
                    continue

                employees[existing_idx] = self._merge_employee(existing, employee)
                merged_count += 1
                merged = employees[existing_idx]
                by_email[str(merged.email).lower()] = existing_idx
                if merged.linkedin_url:
                    by_linkedin[merged.linkedin_url] = existing_idx
                continue

            idx = len(employees)
            employees.append(employee)
            by_email[email_key] = idx
            if linkedin_key:
                by_linkedin[linkedin_key] = idx

        return ProviderResult(
            employees=employees,
            skipped=skipped,
            merged_count=merged_count,
            company=company_info,
        )

    @staticmethod
    def _company_location(data: dict[str, Any]) -> str | None:
        parts = [
            part.strip()
            for part in (data.get("city"), data.get("state"), data.get("country"))
            if isinstance(part, str) and part.strip()
        ]
        return ", ".join(parts) if parts else None

    @classmethod
    def _map_company(cls, data: dict[str, Any]) -> CompanyInfo | None:
        industry = data.get("industry")
        if isinstance(industry, dict):
            industry = industry.get("industry")

        headcount = data.get("headcount")
        if isinstance(headcount, (int, float)) and not isinstance(headcount, bool):
            headcount = str(int(headcount))
        elif isinstance(headcount, list):
            headcount = ", ".join(
                str(item).strip()
                for item in headcount
                if item is not None and str(item).strip()
            ) or None

        postal = data.get("postal_code")
        if postal is None:
            postal = data.get("postalCode")
        street = data.get("street")
        if street is None:
            street = data.get("streetAddress")

        try:
            info = CompanyInfo(
                organization=cls._optional_str(data.get("organization"), 200),
                description=cls._optional_str(data.get("description"), 2000),
                industry=cls._optional_str(industry, 200),
                headcount=cls._optional_str(headcount, 50),
                country=cls._optional_str(data.get("country"), 100),
                state=cls._optional_str(data.get("state"), 100),
                city=cls._optional_str(data.get("city"), 100),
                postal_code=cls._optional_str(postal, 30),
                street=cls._optional_str(street, 200),
            )
        except ValidationError:
            return None
        if all(value is None for value in info.model_dump().values()):
            return None
        return info

    @classmethod
    def _map_person_extras(cls, item: dict[str, Any]) -> dict[str, Any]:
        confidence = item.get("confidence")
        email_confidence: int | None = None
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            as_int = int(confidence)
            if 0 <= as_int <= 100:
                email_confidence = as_int

        verification = item.get("verification")
        email_verification: str | None = None
        if isinstance(verification, dict):
            email_verification = cls._optional_str(verification.get("status"), 50)
        else:
            email_verification = cls._optional_str(verification, 50)

        sources = item.get("sources")
        sources_count: int | None = None
        first_source_url: str | None = None
        if isinstance(sources, list):
            sources_count = len(sources)
            if sources and isinstance(sources[0], dict):
                first_source_url = cls._optional_str(sources[0].get("uri"), 500)

        return {
            "seniority": cls._optional_str(item.get("seniority"), 50),
            "department": cls._optional_str(item.get("department"), 100),
            "twitter": cls._optional_str(item.get("twitter"), 100),
            "email_confidence": email_confidence,
            "email_verification": email_verification,
            "sources_count": sources_count,
            "first_source_url": first_source_url,
        }

    @staticmethod
    def _optional_str(value: object, max_length: int) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned[:max_length]

    @staticmethod
    def _build_name(first: object, last: object) -> str | None:
        parts = [
            part.strip()
            for part in (first, last)
            if isinstance(part, str) and part.strip()
        ]
        return " ".join(parts) if parts else None

    @staticmethod
    def _identifiers_conflict(existing: Employee, incoming: Employee) -> bool:
        email_a = str(existing.email).lower()
        email_b = str(incoming.email).lower()
        linkedin_a = existing.linkedin_url
        linkedin_b = incoming.linkedin_url

        if email_a == email_b:
            return bool(linkedin_a and linkedin_b and linkedin_a != linkedin_b)
        if linkedin_a and linkedin_b and linkedin_a == linkedin_b:
            return email_a != email_b
        return False

    @staticmethod
    def _merge_employee(primary: Employee, incoming: Employee) -> Employee:
        data = primary.model_dump()
        incoming_data = incoming.model_dump()
        for key, value in incoming_data.items():
            if key in {"email", "source", "company", "domain"}:
                continue
            if data.get(key) in (None, "") and value not in (None, ""):
                data[key] = value
        return Employee.model_validate(data)
