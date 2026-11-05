"""Pydantic request/response models. Identifiers are validated against a strict whitelist pattern."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_PATTERN = r"^[A-Za-z0-9_\-]{1,40}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(Strict):
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class SeriesQuery(Strict):
    item_id: str = Field(pattern=ID_PATTERN)
    store_id: str = Field(pattern=ID_PATTERN)


class SalesQuery(SeriesQuery):
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    granularity: Literal["day", "week"] = "week"


class ProductQuery(Page):
    q: str | None = Field(None, max_length=60)
    cat_id: str | None = Field(None, pattern=ID_PATTERN)


class RiskQuery(Page):
    store_id: str | None = Field(None, pattern=ID_PATTERN)
    min_prob: float = Field(0.0, ge=0, le=1)
    sort: Literal["prob", "revenue_at_risk", "cover_days"] = "prob"


class InventoryQuery(Page):
    store_id: str | None = Field(None, pattern=ID_PATTERN)
    item_id: str | None = Field(None, pattern=ID_PATTERN)
    q: str | None = Field(None, max_length=60)


class POQuery(Page):
    status: Literal["open", "received", "partial"] | None = None
    supplier_id: str | None = Field(None, pattern=ID_PATTERN)


class BacktestQuery(SeriesQuery):
    horizon: int = 7

    @field_validator("horizon")
    @classmethod
    def _h(cls, v):
        if v not in (7, 14, 28):
            raise ValueError("horizon must be 7, 14 or 28")
        return v


class Assumptions(Strict):
    """User-adjustable business assumptions. Defaults match the simulation's cost parameters."""
    holding_rate_annual: float = Field(0.25, ge=0.0, le=2.0)
    order_cost: float = Field(12.0, ge=0.0, le=10000)
    stockout_cost_multiplier: float = Field(1.0, gt=0, le=20)
    goodwill: float = Field(0.25, ge=0, le=5)
    service_level: float = Field(0.95, gt=0.5, lt=0.9999)
    mode: Literal["cost", "service"] = "cost"
    batching: bool = True
    review_days: float = Field(7.0, ge=1, le=60)
    budget: float | None = Field(None, ge=0)
    storage_capacity_volume: float | None = Field(None, ge=0)
    supplier_capacity_units: float | None = Field(None, ge=0, description="applied to every supplier in the plan")
    demand_multiplier: float = Field(1.0, gt=0, le=10)
    lead_time_add_days: float = Field(0.0, ge=-10, le=60)


class RecommendRequest(Strict):
    store_id: str = Field(pattern=ID_PATTERN)
    item_ids: list[str] | None = Field(None, max_length=200)
    assumptions: Assumptions = Assumptions()
    include_sensitivity: bool = False

    @field_validator("item_ids")
    @classmethod
    def _ids(cls, v):
        import re
        if v is not None and any(not re.match(ID_PATTERN, x) for x in v):
            raise ValueError("invalid item id")
        return v


class Scenario(Strict):
    name: str = Field(max_length=60)
    assumptions: Assumptions


class ScenarioRequest(Strict):
    store_id: str = Field(pattern=ID_PATTERN)
    item_ids: list[str] | None = Field(None, max_length=200)
    scenarios: list[Scenario] = Field(min_length=1, max_length=8)


class AskRequest(Strict):
    question: str = Field(min_length=2, max_length=500)
    item_id: str | None = Field(None, pattern=ID_PATTERN)
    store_id: str | None = Field(None, pattern=ID_PATTERN)


class Citation(BaseModel):
    id: str
    kind: str
    title: str
    source: str
    values: dict


class AskResponse(BaseModel):
    answer: str
    supported: bool
    intent: str
    citations: list[Citation]
    provider: str
    data_kind: str
    notes: list[str] = []


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
