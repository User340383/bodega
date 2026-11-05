"""Validated persistence inputs for the analysis integration boundary."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class BaselineEstimate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    itemName: str = Field(min_length=1, max_length=100)
    windowDays: Literal[7]
    loggedDays: int = Field(ge=0, strict=True)
    averageDailyQuantity: Decimal = Field(ge=0, allow_inf_nan=False)
    trend: Literal['UP', 'DOWN', 'STABLE', 'INSUFFICIENT_DATA']
    suggestedRestockQty: int = Field(ge=0, strict=True)
    computedAt: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priorAverageDailyQuantity: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator('itemName')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Item name cannot be blank')
        return ' '.join(value.split())
