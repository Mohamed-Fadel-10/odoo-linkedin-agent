from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CompanyInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    organization: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    industry: str | None = Field(default=None, max_length=200)
    headcount: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=30)
    street: str | None = Field(default=None, max_length=200)

    @field_validator(
        "organization",
        "description",
        "industry",
        "headcount",
        "country",
        "state",
        "city",
        "postal_code",
        "street",
        mode="before",
    )
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class Employee(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    company: str = Field(..., min_length=1, max_length=200)
    domain: str = Field(..., min_length=1, max_length=255)
    source: str = Field(default="hunter", max_length=50)
    seniority: str | None = Field(default=None, max_length=50)
    department: str | None = Field(default=None, max_length=100)
    twitter: str | None = Field(default=None, max_length=100)
    email_confidence: int | None = Field(default=None, ge=0, le=100)
    email_verification: str | None = Field(default=None, max_length=50)
    sources_count: int | None = Field(default=None, ge=0)
    first_source_url: str | None = Field(default=None, max_length=500)

    @field_validator(
        "title",
        "phone",
        "location",
        "linkedin_url",
        "seniority",
        "department",
        "twitter",
        "email_verification",
        "first_source_url",
        mode="before",
    )
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class SkippedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    raw_email: str | None = None
    raw_name: str | None = None


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employees: list[Employee]
    skipped: list[SkippedRecord]
    merged_count: int = 0
    company: CompanyInfo | None = None


class Provider(Protocol):
    def find_employees(self, company: str, domain: str) -> ProviderResult: ...
