"""Pydantic models for all pipeline LLM outputs (used by instructor)."""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class Entity(BaseModel):
    type: Literal["customer", "contact", "employee", "product", "supplier", "region", "person"]
    value: str


class AnalysisResult(BaseModel):
    query_type: Literal["smalltalk", "lookup", "list", "metric", "ranking", "comparison"]
    is_why_question: bool = False
    intent: str
    entities: list[Entity] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump()


class MongoQuery(BaseModel):
    collection: str
    operation: Literal["aggregate", "find"]
    pipeline: Optional[list[dict]] = None    # required when operation == "aggregate"
    filter: Optional[dict] = None            # used when operation == "find"
    projection: Optional[dict] = None
    limit: Optional[int] = None

    @model_validator(mode="after")
    def check_fields(self) -> "MongoQuery":
        if self.operation == "aggregate":
            if not self.pipeline:
                raise ValueError("aggregate operation requires a non-empty pipeline")
        if self.operation == "find":
            if self.filter is None:
                self.filter = {}
            if self.limit is None:
                self.limit = 50
        return self

    def to_dict(self) -> dict:
        d = self.model_dump(exclude_none=True)
        # rename Python reserved-word alias back to "filter"
        return d
