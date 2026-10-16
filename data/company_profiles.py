"""Dated, company-researched factor sets, including explicit acquisition gaps.

A factor is one economic series and one model column. Different return lags
are not counted as different factors. Unbound series are requirements, never
permission to silently substitute a generic market variable.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1)]
FactorId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]


class _ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CompanyFactor(_ProfileModel):
    id: FactorId
    label: Text
    # Set only when the actual series is in the verified data catalogue.
    series_id: FactorId | None = None
    transform: Literal["return_1d", "difference_1d", "level"] = "return_1d"
    frequency: Literal["daily", "weekly", "monthly", "quarterly", "event"]
    max_age_days: Annotated[int, Field(gt=0, le=370)]
    mechanism: Text
    acquisition: Text
    source_urls: Annotated[list[Text], Field(min_length=1)]

    @property
    def feature_name(self) -> str:
        return f"{self.id}__{self.transform}"


class CompanyProfile(_ProfileModel):
    ticker: Annotated[str, Field(pattern=r"^[0-9]{4}$")]
    name: Text
    valid_from: date
    thesis: Text
    evidence: Text
    factors: Annotated[list[CompanyFactor], Field(min_length=7, max_length=10)]

    @model_validator(mode="after")
    def distinct_factors(self) -> Self:
        ids = [factor.id for factor in self.factors]
        bindings = [factor.series_id for factor in self.factors if factor.series_id]
        if len(ids) != len(set(ids)) or len(bindings) != len(set(bindings)):
            raise ValueError("company factors must represent distinct series")
        return self


class CompanyProfilesConfig(_ProfileModel):
    version: Literal[1] = 1
    profiles: list[CompanyProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_versions(self) -> Self:
        keys = [(profile.ticker, profile.valid_from) for profile in self.profiles]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate ticker/profile effective date")
        return self

    def resolve(self, ticker: str, on: date) -> CompanyProfile | None:
        applicable = [
            profile
            for profile in self.profiles
            if profile.ticker == ticker and profile.valid_from <= on
        ]
        return max(applicable, key=lambda item: item.valid_from, default=None)
