"""Follow-up (nurture) rules, editable per business (GET/PUT /api/settings/nurture).

Stored in business_settings under the key "nurture" once changed. The default
sequence matches the templates drafted in docs/meta_setup.md section 4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.db.models import BusinessSetting

SETTINGS_KEY = "nurture"
Param = Literal["first_name", "product", "offer"]


class Step(BaseModel):
    # Days after the customer's last message (or after the lead arrived, if they never wrote).
    after_days: float = Field(gt=0, le=60)
    template: str = Field(min_length=1, max_length=512, pattern=r"^[a-z0-9_]+$")
    # Values for the template's {{1}}, {{2}}, ... in order.
    params: list[Param] = Field(default_factory=lambda: ["first_name", "product"], max_length=5)

    @property
    def needs_offer(self) -> bool:
        return "offer" in self.params


class NurtureConfig(BaseModel):
    enabled: bool = True
    steps: list[Step] = Field(
        default_factory=lambda: [
            Step(after_days=2, template="nurture_followup_1"),
            Step(after_days=5, template="nurture_offer", params=["first_name", "product", "offer"]),
            Step(after_days=10, template="nurture_last_check"),
        ],
        min_length=1,
        max_length=5,
    )
    # Text for {{3}} of the offer template, e.g. "free delivery in Chennai this week".
    # Steps that need an offer are skipped while this is empty.
    offer_text: str = Field(default="", max_length=200)
    max_per_lead: int = Field(default=6, ge=1, le=20)  # follow-ups per lead, ever
    # Never start a sequence for someone quiet longer than this (e.g. old leads after an upgrade).
    max_quiet_days: int = Field(default=30, ge=1, le=365)
    send_from_hour: int = Field(default=10, ge=0, le=23)  # business time zone
    send_until_hour: int = Field(default=19, ge=1, le=24)

    @model_validator(mode="after")
    def _check(self) -> NurtureConfig:
        days = [s.after_days for s in self.steps]
        if any(b <= a for a, b in zip(days, days[1:], strict=False)):
            raise ValueError("each follow-up must come later than the one before")
        if self.send_from_hour >= self.send_until_hour:
            raise ValueError("send_from_hour must be earlier than send_until_hour")
        return self

    def active_steps(self) -> list[Step]:
        return [s for s in self.steps if self.offer_text.strip() or not s.needs_offer]


def get_config(session: Session, user_id: str) -> NurtureConfig:
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    return NurtureConfig.model_validate(row.value) if row else NurtureConfig()


def save_config(session: Session, user_id: str, config: NurtureConfig) -> None:
    row = session.get(BusinessSetting, (user_id, SETTINGS_KEY))
    value = config.model_dump(mode="json")
    if row is None:
        session.add(BusinessSetting(user_id=user_id, key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    session.flush()
