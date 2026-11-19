from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


load_dotenv()


class PartnerCreateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    email: EmailStr
    phone: str | None = None
    job_title: str | None = None
    city: str | None = None
    country: str | int | None = None
    company_id: int | None = None
    linkedin_url: str | None = None
    comment: str | None = None

    @field_validator(
        "name", "phone", "job_title", "city", "linkedin_url", "comment", mode="before"
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str | None) -> str:
        if not value:
            raise ValueError("name is required")
        return value


class CompanyCreateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    street: str | None = None
    city: str | None = None
    zip_code: str | None = None
    country: str | None = None
    state: str | None = None
    industry: str | None = None
    headcount: str | None = None
    description: str | None = None

    @field_validator(
        "street",
        "city",
        "zip_code",
        "country",
        "state",
        "industry",
        "headcount",
        "description",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class OdooClient:
    PARTNER_FIELDS = [
        "id",
        "name",
        "email",
        "phone",
        "function",
        "city",
        "country_id",
        "parent_id",
        "is_company",
        "x_linkedin_url",
    ]

    def __init__(self) -> None:
        self.url = os.getenv("ODOO_URL", "").rstrip("/")
        self.database = os.getenv("ODOO_DATABASE", "")
        self.api_key = os.getenv("ODOO_API_KEY", "")

        if not all([self.url, self.database, self.api_key]):
            raise ValueError("Missing Odoo configuration in .env")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"bearer {self.api_key}",
                "X-Odoo-Database": self.database,
                "Content-Type": "application/json",
                "User-Agent": "LinkedIn-Agent/1.0",
            }
        )

    def _call(self, model: str, method: str, **params: Any) -> Any:
        endpoint = f"{self.url}/json/2/{model}/{method}"

        response = self.session.post(
            endpoint,
            json=params,
            timeout=30,
        )

        if not response.ok:
            try:
                message = response.json().get("message", "Unknown error")
            except ValueError:
                message = response.text[:500]

            raise RuntimeError(
                f"Odoo API error ({response.status_code}): {message}"
            )

        return response.json()

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def normalize_linkedin_url(url: str) -> str:
        cleaned = url.strip()
        parsed = urlparse(cleaned)
        if not parsed.scheme:
            parsed = urlparse(f"https://{cleaned}")

        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]

        path = re.sub(r"/+", "/", parsed.path).rstrip("/")
        normalized = urlunparse(
            ("https", host, path, "", "", "")
        )
        return normalized

    def search_company(self, name: str) -> list[dict[str, Any]]:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Company name is required")

        return self._call(
            "res.partner",
            "search_read",
            domain=[
                ["name", "=", cleaned],
                ["is_company", "=", True],
            ],
            fields=["id", "name", "email", "phone", "website"],
            limit=1,
        )

    def search_company_by_website(self, domain: str) -> list[dict[str, Any]]:
        cleaned = domain.strip().lower().removeprefix("http://").removeprefix("https://")
        cleaned = cleaned.split("/")[0]
        if cleaned.startswith("www."):
            cleaned = cleaned[4:]
        if not cleaned:
            raise ValueError("Domain is required")

        candidates = self._call(
            "res.partner",
            "search_read",
            domain=[
                ["is_company", "=", True],
                "|",
                ["website", "ilike", cleaned],
                ["website", "ilike", f"%{cleaned}%"],
            ],
            fields=["id", "name", "email", "phone", "website"],
            limit=20,
        )
        matches: list[dict[str, Any]] = []
        for partner in candidates:
            website = partner.get("website")
            if not isinstance(website, str) or not website.strip():
                continue
            website_host = (
                website.strip()
                .lower()
                .removeprefix("http://")
                .removeprefix("https://")
                .split("/")[0]
            )
            if website_host.startswith("www."):
                website_host = website_host[4:]
            if website_host == cleaned:
                matches.append(partner)
        return matches

    def ensure_company(
        self,
        name: str,
        website: str | None = None,
        *,
        company_data: CompanyCreateData | dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> int:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Company name is required")

        if website:
            by_website = self.search_company_by_website(website)
            if by_website:
                return int(by_website[0]["id"])

        companies = self.search_company(cleaned_name)
        if companies:
            return int(companies[0]["id"])

        vals: dict[str, Any] = {"name": cleaned_name, "is_company": True}
        if website:
            host = (
                website.strip()
                .lower()
                .removeprefix("http://")
                .removeprefix("https://")
                .split("/")[0]
            )
            if host.startswith("www."):
                host = host[4:]
            vals["website"] = f"https://{host}" if host else None
            if vals["website"] is None:
                del vals["website"]

        details = self._coerce_company_data(company_data)
        if details is not None:
            self._apply_company_create_fields(vals, details, warnings)

        created_ids = self._call(
            "res.partner",
            "create",
            vals_list=[vals],
        )
        if not created_ids:
            raise RuntimeError(f"Failed to create company: {cleaned_name}")
        return int(created_ids[0])

    @staticmethod
    def _coerce_company_data(
        company_data: CompanyCreateData | dict[str, Any] | None,
    ) -> CompanyCreateData | None:
        if company_data is None:
            return None
        if isinstance(company_data, CompanyCreateData):
            return company_data
        return CompanyCreateData.model_validate(company_data)

    def _apply_company_create_fields(
        self,
        vals: dict[str, Any],
        details: CompanyCreateData,
        warnings: list[str] | None,
    ) -> None:
        if details.street:
            vals["street"] = details.street
        if details.city:
            vals["city"] = details.city
        if details.zip_code:
            vals["zip"] = details.zip_code

        country_id: int | None = None
        if details.country:
            try:
                country_id = self.resolve_country_id(details.country)
                vals["country_id"] = country_id
            except ValueError:
                if warnings is not None:
                    warnings.append(
                        f"country not found in Odoo; skipped: {details.country}"
                    )

        if details.state:
            if country_id is None:
                if warnings is not None:
                    warnings.append(
                        f"state skipped (no resolved country): {details.state}"
                    )
            else:
                try:
                    vals["state_id"] = self.resolve_state_id(country_id, details.state)
                except ValueError:
                    if warnings is not None:
                        warnings.append(
                            f"state not found in Odoo; skipped: {details.state}"
                        )

        note_lines: list[str] = []
        if details.industry:
            note_lines.append(f"Industry: {details.industry}")
        if details.headcount:
            note_lines.append(f"Headcount: {details.headcount}")
        if details.description:
            note_lines.append(f"Description: {details.description}")
        if note_lines:
            vals["comment"] = "\n".join(note_lines)

    def search_partner(
        self,
        email: str | None = None,
        linkedin_url: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[list[Any]] = []

        if email:
            conditions.append(
                ["email", "=ilike", self.normalize_email(email)]
            )

        if linkedin_url:
            conditions.append(
                ["x_linkedin_url", "=", self.normalize_linkedin_url(linkedin_url)]
            )

        if not conditions:
            raise ValueError("Email or LinkedIn URL is required")

        domain: list[Any] = (
            conditions if len(conditions) == 1 else ["|", *conditions]
        )

        return self._call(
            "res.partner",
            "search_read",
            domain=domain,
            fields=self.PARTNER_FIELDS,
            limit=10,
        )

    def find_exact_contact(
        self,
        email: str | None = None,
        linkedin_url: str | None = None,
    ) -> dict[str, Any] | None:
        if not email and not linkedin_url:
            raise ValueError("Email or LinkedIn URL is required")

        target_email = self.normalize_email(email) if email else None
        target_linkedin = (
            self.normalize_linkedin_url(linkedin_url) if linkedin_url else None
        )

        candidates = self.search_partner(email=email, linkedin_url=linkedin_url)
        for partner in candidates:
            partner_email = partner.get("email")
            if target_email and isinstance(partner_email, str) and partner_email.strip():
                if self.normalize_email(partner_email) == target_email:
                    return partner

            partner_linkedin = partner.get("x_linkedin_url")
            if (
                target_linkedin
                and isinstance(partner_linkedin, str)
                and partner_linkedin.strip()
            ):
                if self.normalize_linkedin_url(partner_linkedin) == target_linkedin:
                    return partner
        return None

    def resolve_country_id(self, country: str | int) -> int:
        if isinstance(country, int):
            if country <= 0:
                raise ValueError("country_id must be a positive integer")
            return country

        cleaned = country.strip()
        if not cleaned:
            raise ValueError("Country value is empty")

        code_domain: list[Any]
        if len(cleaned) == 2 and cleaned.isalpha():
            code_domain = [["code", "=", cleaned.upper()]]
        else:
            code_domain = [["code", "=ilike", cleaned]]

        countries = self._call(
            "res.country",
            "search_read",
            domain=code_domain,
            fields=["id", "name", "code"],
            limit=1,
        )
        if countries:
            return int(countries[0]["id"])

        countries = self._call(
            "res.country",
            "search_read",
            domain=[["name", "=ilike", cleaned]],
            fields=["id", "name", "code"],
            limit=1,
        )
        if not countries:
            raise ValueError(f"Country not found in Odoo: {cleaned}")
        return int(countries[0]["id"])

    def resolve_state_id(self, country_id: int, state: str) -> int:
        if country_id <= 0:
            raise ValueError("country_id must be a positive integer")
        cleaned = state.strip()
        if not cleaned:
            raise ValueError("State value is empty")

        code_domain: list[Any] = [
            ["country_id", "=", country_id],
            ["code", "=ilike", cleaned],
        ]
        states = self._call(
            "res.country.state",
            "search_read",
            domain=code_domain,
            fields=["id", "name", "code", "country_id"],
            limit=1,
        )
        if states:
            return int(states[0]["id"])

        states = self._call(
            "res.country.state",
            "search_read",
            domain=[
                ["country_id", "=", country_id],
                ["name", "=ilike", cleaned],
            ],
            fields=["id", "name", "code", "country_id"],
            limit=1,
        )
        if not states:
            raise ValueError(f"State not found in Odoo: {cleaned}")
        return int(states[0]["id"])

    def create_partner(self, data: PartnerCreateData | dict[str, Any]) -> int:
        partner = (
            data
            if isinstance(data, PartnerCreateData)
            else PartnerCreateData.model_validate(data)
        )

        email = self.normalize_email(str(partner.email))
        linkedin_url = (
            self.normalize_linkedin_url(partner.linkedin_url)
            if partner.linkedin_url
            else None
        )

        existing = self.find_exact_contact(email=email, linkedin_url=linkedin_url)
        if existing:
            raise ValueError(
                "Contact already exists in Odoo "
                f"(id={existing['id']}); refusing to create a duplicate"
            )

        vals: dict[str, Any] = {
            "name": partner.name,
            "email": email,
            "is_company": False,
        }

        if partner.phone:
            vals["phone"] = partner.phone
        if partner.job_title:
            vals["function"] = partner.job_title
        if partner.city:
            vals["city"] = partner.city
        if partner.company_id is not None:
            vals["parent_id"] = partner.company_id
        if linkedin_url:
            vals["x_linkedin_url"] = linkedin_url
        if partner.comment:
            vals["comment"] = partner.comment
        if partner.country is not None:
            vals["country_id"] = self.resolve_country_id(partner.country)

        created_ids = self._call(
            "res.partner",
            "create",
            vals_list=[vals],
        )
        if not created_ids:
            raise RuntimeError("Odoo create returned no partner ID")
        return int(created_ids[0])
