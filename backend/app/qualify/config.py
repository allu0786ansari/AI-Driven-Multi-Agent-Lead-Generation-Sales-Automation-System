"""Qualification rules, editable per business (GET/PUT /api/settings/qualification).

Defaults below are a starting point (proposal open question: "what makes a
lead hot for this business"). They are stored in business_settings under the
key "qualification" once changed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.db.models import BusinessSetting

SETTINGS_KEY = "qualification"
Slot = Literal["product", "quantity", "timeline", "city", "budget"]


class Question(BaseModel):
    slot: Slot
    question: str = Field(min_length=3, max_length=200)


class Weights(BaseModel):
    product: int = 20
    budget_known: int = 10
    budget_fits: int = 10  # budget covers the cheapest matching product
    timeline_soon: int = 25  # now / this week
    timeline_this_month: int = 15
    timeline_later: int = 5
    quantity_known: int = 10
    bulk: int = 5
    city: int = 10
    intent_high: int = 15
    intent_medium: int = 8
    engaged: int = 5  # sent at least 2 messages
    negative_sentiment: int = -10


class QualificationConfig(BaseModel):
    questions: list[Question] = Field(
        default_factory=lambda: [
            Question(slot="product", question="Which product are you interested in?"),
            Question(slot="quantity", question="How many would you need?"),
            Question(slot="timeline", question="When are you planning to buy?"),
            Question(slot="city", question="Which city should we deliver to?"),
            Question(slot="budget", question="Do you have a budget in mind?"),
        ]
    )
    weights: Weights = Field(default_factory=Weights)
    bulk_threshold: int = Field(default=3, ge=2)
    hot_threshold: int = Field(default=70, ge=1, le=100)
    warm_threshold: int = Field(default=40, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> QualificationConfig:
        if self.warm_threshold >= self.hot_threshold:
            raise ValueError("warm_threshold must be lower than hot_threshold")
        slots = [q.slot for q in self.questions]
        if len(slots) != len(set(slots)):
            raise ValueError("each slot can have only one question")
        return self


def get_config(session: Session, user_id: str) -> QualificationConfig:
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    return QualificationConfig.model_validate(row.value) if row else QualificationConfig()


def save_config(session: Session, user_id: str, config: QualificationConfig) -> None:
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    value = config.model_dump(mode="json")
    if row is None:
        session.add(BusinessSetting(user_id=user_id, key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.flush()
