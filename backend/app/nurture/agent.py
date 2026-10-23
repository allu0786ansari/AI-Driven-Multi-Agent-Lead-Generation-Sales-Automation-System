"""Nurture Agent: approved WhatsApp templates for leads that went quiet (proposal 3d).

Who gets follow-ups (all must hold):
  WhatsApp number, recorded WhatsApp opt-in, not opted out, no person handling
  the conversation, not Contacted / Bought / Lost, personal data not erased,
  follow-ups switched on for the business.

When: step N is due `after_days` after the customer's last message (or after
the lead arrived, if they never wrote), moved into business hours. A message
from the customer ends the sequence; it starts again from step 1 if they go
quiet again. At most `max_per_lead` follow-ups per lead, ever.

How: through the Send Gate as a marketing template (sender "nurture"), with an
idempotency key per lead + quiet period + step, so a follow-up is never sent
twice even if the job runs twice. Runs inside the worker every
NURTURE_CHECK_MINUTES (docs/DECISIONS.md D-029).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import Channel, LeadStatus, PricingCategory, Sender
from app.db.models import Lead, Message
from app.db.session import session_scope
from app.leads.service import record
from app.messaging.send_gate import Decision, OutboundContent, queue_message
from app.nurture.config import NurtureConfig, Step, get_config

log = logging.getLogger(__name__)

DONE = 99  # nurture_step value: nothing more to send until the customer writes again
MIN_GAP = timedelta(hours=20)  # between two follow-ups, even when catching up after downtime
NO_FOLLOW_UP_STATUSES = (LeadStatus.CONTACTED, LeadStatus.CONVERTED, LeadStatus.LOST)


@dataclass(frozen=True)
class FollowUp:
    """What happens next for one lead (shown on the lead page)."""

    due_at: datetime | None  # None: no follow-up planned
    step: int  # 0-based index into the active steps
    template: str | None
    sent: int  # follow-ups sent to this lead so far (ever)
    reason: str  # why there is no follow-up (empty when one is planned)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))


def in_send_hours(when: datetime, config: NurtureConfig, tz: ZoneInfo | None = None) -> datetime:
    """`when`, or the start of the next allowed sending hour (business time zone)."""
    tz = tz or ZoneInfo(get_settings().business_timezone)
    local = _aware(when).astimezone(tz)
    if config.send_from_hour <= local.hour < config.send_until_hour:
        return when
    start = local.replace(hour=config.send_from_hour, minute=0, second=0, microsecond=0)
    if local.hour >= config.send_until_hour:
        start += timedelta(days=1)
    return start.astimezone(ZoneInfo("UTC"))


def first_name(lead: Lead) -> str:
    name = (lead.name or "").strip()
    if not name or name.startswith("+") or name[0].isdigit():
        return "there"
    return name.split()[0]


def product_label(lead: Lead) -> str:
    slots = lead.qualify_slots or {}
    return lead.product_interest or slots.get("product") or slots.get("category") or "our products"


def template_params(step: Step, lead: Lead, config: NurtureConfig) -> list[str]:
    values = {
        "first_name": first_name(lead),
        "product": product_label(lead),
        "offer": config.offer_text.strip(),
    }
    return [values[p] for p in step.params]


def ineligible_reason(lead: Lead, config: NurtureConfig) -> str:
    if not config.enabled:
        return "Follow-ups are switched off in Settings."
    if lead.erased_at is not None:
        return "Personal data erased."
    if lead.status in (LeadStatus.CONVERTED, LeadStatus.LOST):  # the final word, whatever else applies
        return f"Marked as {'Bought' if lead.status == LeadStatus.CONVERTED else 'Lost'}."
    if not lead.contact_phone:
        return "No WhatsApp number."
    if lead.opted_out:
        return "The customer opted out."
    if not lead.whatsapp_opt_in:
        if lead.opt_in_asked_at is not None:
            return "No WhatsApp opt-in (asked in the chat; no YES yet)."
        return "No WhatsApp opt-in yet. The assistant asks once in the chat."
    if lead.is_human_takeover:
        return "A person is handling this conversation."
    if lead.status in NO_FOLLOW_UP_STATUSES:
        return f"Marked as {'Bought' if lead.status == LeadStatus.CONVERTED else lead.status.title()}."
    return ""


def _counts(session: Session, lead: Lead, since: datetime) -> tuple[int, int, datetime | None]:
    """(follow-ups since `since`, follow-ups ever, time of the last one)."""
    base = select(func.count()).where(Message.lead_id == lead.lead_id, Message.sender == Sender.NURTURE)
    since_count = session.scalar(base.where(Message.timestamp > since)) or 0
    total = session.scalar(base) or 0
    last = session.scalar(
        select(func.max(Message.timestamp)).where(
            Message.lead_id == lead.lead_id, Message.sender == Sender.NURTURE
        )
    )
    return since_count, total, last


def next_follow_up(
    session: Session, lead: Lead, config: NurtureConfig, now: datetime | None = None
) -> FollowUp:
    now = now or utcnow()
    anchor = _aware(lead.last_inbound_at or lead.created_at)
    since, total, last = _counts(session, lead, anchor)
    reason = ineligible_reason(lead, config)
    if reason:
        return FollowUp(None, since, None, total, reason)
    if since == 0 and now - anchor > timedelta(days=config.max_quiet_days):
        reason = (
            f"Quiet for more than {config.max_quiet_days} days; "
            "follow-ups start again if the customer writes."
        )
        return FollowUp(None, since, None, total, reason)
    if total >= config.max_per_lead:
        return FollowUp(None, since, None, total, f"Reached the limit of {config.max_per_lead} follow-ups.")
    steps = config.active_steps()
    if since >= len(steps):
        return FollowUp(
            None, since, None, total, "All follow-ups sent. They start again if the customer writes."
        )
    step = steps[since]
    due = anchor + timedelta(days=step.after_days)
    if last is not None:
        due = max(due, _aware(last) + MIN_GAP)
    return FollowUp(in_send_hours(due, config), since, step.template, total, "")


def _eligible():  # noqa: ANN202
    return select(Lead).where(
        Lead.whatsapp_opt_in.is_(True),
        Lead.opted_out.is_(False),
        Lead.is_human_takeover.is_(False),
        Lead.contact_phone.is_not(None),
        Lead.erased_at.is_(None),
        Lead.status.not_in(NO_FOLLOW_UP_STATUSES),
        Lead.nurture_step < DONE,
    )


def _candidates(session: Session, check_time: datetime, user_id: str | None):  # noqa: ANN202
    q = _eligible().where(Lead.nurture_next_at.is_(None) | (Lead.nurture_next_at <= check_time))
    if user_id:
        q = q.where(Lead.user_id == user_id)
    return session.scalars(q.order_by(Lead.created_at).limit(500)).all()


def planned(session: Session, user_id: str, limit: int = 20) -> list[tuple[Lead, FollowUp]]:
    """The next follow-ups, worked out now (read-only; the worker's schedule may be minutes old)."""
    config = get_config(session, user_id)
    out = []
    for lead in session.scalars(_eligible().where(Lead.user_id == user_id).limit(1000)).all():
        plan = next_follow_up(session, lead, config)
        if plan.due_at is not None:
            out.append((lead, plan))
    out.sort(key=lambda x: x[1].due_at)
    return out[:limit]


def run_nurture(
    as_of: datetime | None = None, user_id: str | None = None, now: datetime | None = None
) -> dict[str, int]:
    """One pass: send follow-ups that are due, schedule the rest.

    `as_of` pretends it is later than it is when deciding what is due (demo:
    "what would happen in 3 days"); messages are still stamped with the real
    time so the outbox sends them now. `now` replaces the clock (tests).
    """
    now = now or utcnow()
    check_time = as_of or now
    counts: dict[str, int] = {}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    with session_scope() as s:
        configs: dict[str, NurtureConfig] = {}
        for lead in _candidates(s, check_time, user_id):
            config = configs.setdefault(lead.user_id, get_config(s, lead.user_id))
            plan = next_follow_up(s, lead, config, check_time)
            if plan.due_at is None:
                lead.nurture_step = DONE
                lead.nurture_next_at = None
                bump("done")
                continue
            if plan.due_at > check_time:
                lead.nurture_step = plan.step
                lead.nurture_next_at = plan.due_at
                bump("scheduled")
                continue
            step = config.active_steps()[plan.step]
            anchor = _aware(lead.last_inbound_at or lead.created_at)
            result = queue_message(
                s,
                lead,
                OutboundContent(
                    template_name=step.template,
                    template_params=template_params(step, lead, config),
                    template_category=PricingCategory.MARKETING,
                ),
                idempotency_key=f"nurture:{lead.lead_id}:{anchor.isoformat()}:{plan.step}",
                sender=Sender.NURTURE,
                channel=Channel.WHATSAPP,
                now=now,
            )
            if result.decision == Decision.QUEUED and result.reason:  # "already queued (idempotent)"
                lead.nurture_next_at = check_time + timedelta(days=1)
                bump("duplicate")
            elif result.decision == Decision.QUEUED:
                lead.nurture_step = plan.step + 1
                lead.nurture_next_at = None  # the next pass schedules the following step
                record(s, lead, "system", "follow_up", template=step.template, step=plan.step + 1)
                bump("sent")
            else:
                lead.nurture_next_at = check_time + timedelta(days=1)  # try again tomorrow
                bump("blocked")
                log.info("follow-up not sent", extra={"lead_id": lead.lead_id, "reason": result.reason})
    if counts.get("sent"):
        log.info("follow-ups queued", extra=counts)
    return counts
