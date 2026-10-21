"""Numbers for the Analytics page (spec 3.2: GET /api/analytics/summary and /costs).

Definitions (docs/DECISIONS.md D-033):
- Period: the last N days, by the business's time zone; leads count in the
  period they arrived in.
- First response: from the customer's first message (or, for a lead form, the
  moment the lead arrived) to the first message we sent after it - assistant,
  automatic or salesperson.
- Replied: the customer wrote at least once after our first message.
- Qualified / hot: score at or above the warm / hot threshold (Settings).
- Bought: status "converted".
- Re-engaged by a follow-up: the customer wrote within 7 days after one.
- Costs: estimated WhatsApp cost of messages sent in the period (Meta bills
  per delivered message; check the real invoice).

Computed in Python over the period's rows: simple and fast enough for the
prototype's volumes (thousands of leads). Move to SQL aggregates at scale.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import utcnow
from app.db.enums import ConsentType, Direction, LeadSource, LeadStatus, NotificationKind, Sender
from app.db.models import ConsentEvent, Lead, LeadActivity, Message, Notification
from app.qualify.config import get_config as get_qualification

RE_ENGAGE_WINDOW = timedelta(days=7)
RESPONSE_TARGET_SECONDS = 60


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("UTC"))


def _period(days: int, now: datetime | None = None) -> tuple[datetime, datetime, ZoneInfo, list[date]]:
    tz = ZoneInfo(get_settings().business_timezone)
    now = now or utcnow()
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=days - 1)
    start = datetime.combine(first_day, datetime.min.time(), tz)
    return start, now, tz, [first_day + timedelta(days=i) for i in range(days)]


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _leads(session: Session, user_id: str, start: datetime) -> list[Lead]:
    return list(session.scalars(select(Lead).where(Lead.user_id == user_id, Lead.created_at >= start)).all())


def _messages_by_lead(session: Session, lead_ids: list[str]) -> dict[str, list[Message]]:
    out: dict[str, list[Message]] = defaultdict(list)
    for i in range(0, len(lead_ids), 500):
        chunk = lead_ids[i : i + 500]
        rows = session.scalars(
            select(Message).where(Message.lead_id.in_(chunk)).order_by(Message.timestamp)
        ).all()
        for m in rows:
            out[m.lead_id].append(m)
    return out


def _first_response_seconds(lead: Lead, messages: list[Message]) -> float | None:
    inbound = [m for m in messages if m.direction == Direction.INBOUND]
    if lead.source == LeadSource.AD_FORM:
        start = _aware(lead.created_at)  # submitting the form is their first action
    elif inbound:
        start = _aware(inbound[0].timestamp)
    else:
        return None
    for m in messages:
        if m.direction == Direction.OUTBOUND and _aware(m.timestamp) >= start:
            return max(0.0, (_aware(m.timestamp) - start).total_seconds())
    return None


def _replied(messages: list[Message]) -> bool:
    first_out = next((m for m in messages if m.direction == Direction.OUTBOUND), None)
    if first_out is None:
        return False
    return any(
        m.direction == Direction.INBOUND and _aware(m.timestamp) > _aware(first_out.timestamp)
        for m in messages
    )


def _re_engaged(messages: list[Message]) -> bool:
    follow_ups = [_aware(m.timestamp) for m in messages if m.sender == Sender.NURTURE]
    inbound = [_aware(m.timestamp) for m in messages if m.direction == Direction.INBOUND]
    return any(any(f < t <= f + RE_ENGAGE_WINDOW for t in inbound) for f in follow_ups)


def summary(session: Session, user_id: str, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    start, end, tz, day_list = _period(days, now)
    rules = get_qualification(session, user_id)
    leads = _leads(session, user_id, start)
    msgs = _messages_by_lead(session, [lead.lead_id for lead in leads])
    today = end.astimezone(tz).date()

    per_day = Counter(_aware(lead.created_at).astimezone(tz).date() for lead in leads)
    per_day_hot = Counter(
        _aware(lead.created_at).astimezone(tz).date()
        for lead in leads
        if (lead.qualification_score or 0) >= rules.hot_threshold
    )

    response_times = []
    no_response = 0
    for lead in leads:
        secs = _first_response_seconds(lead, msgs.get(lead.lead_id, []))
        if secs is None:
            if lead.source == LeadSource.AD_FORM or any(
                m.direction == Direction.INBOUND for m in msgs.get(lead.lead_id, [])
            ):
                no_response += 1
        else:
            response_times.append(secs)
    response_times.sort()

    def score(lead: Lead) -> int:
        return lead.qualification_score or 0

    captured = len(leads)
    replied = sum(1 for lead in leads if _replied(msgs.get(lead.lead_id, [])))
    qualified = sum(1 for lead in leads if score(lead) >= rules.warm_threshold)
    hot = sum(1 for lead in leads if score(lead) >= rules.hot_threshold or lead.status == LeadStatus.HOT)
    bought = sum(1 for lead in leads if lead.status == LeadStatus.CONVERTED)
    lost = sum(1 for lead in leads if lead.status == LeadStatus.LOST)

    followed_up = [
        lead for lead in leads if any(m.sender == Sender.NURTURE for m in msgs.get(lead.lead_id, []))
    ]
    follow_ups_sent = session.scalars(
        select(Message.message_id).where(
            Message.user_id == user_id, Message.sender == Sender.NURTURE, Message.timestamp >= start
        )
    ).all()
    consent = session.execute(
        select(ConsentEvent.type, ConsentEvent.source).where(
            ConsentEvent.user_id == user_id, ConsentEvent.timestamp >= start
        )
    ).all()
    handoffs = session.scalars(
        select(Notification.notification_id).where(
            Notification.user_id == user_id,
            Notification.kind == NotificationKind.HANDOFF,
            Notification.created_at >= start,
        )
    ).all()
    feedback_rows = session.scalars(
        select(LeadActivity)
        .where(
            LeadActivity.user_id == user_id,
            LeadActivity.kind == "feedback",
            LeadActivity.created_at >= start,
        )
        .order_by(LeadActivity.created_at)
    ).all()
    latest_feedback = {a.lead_id: bool(a.detail.get("agree")) for a in feedback_rows}  # last answer wins

    return {
        "period": {"days": days, "from": start, "to": end, "timezone": str(tz)},
        "leads": {
            "total": captured,
            "today": per_day.get(today, 0),
            "last_7_days": sum(per_day.get(today - timedelta(days=i), 0) for i in range(7)),
            "by_source": dict(Counter(lead.source or "unknown" for lead in leads).most_common()),
            "by_channel": dict(Counter(lead.preferred_channel or "unknown" for lead in leads).most_common()),
            "by_status": dict(Counter(lead.status for lead in leads).most_common()),
        },
        "daily": [
            {"date": d.isoformat(), "leads": per_day.get(d, 0), "hot": per_day_hot.get(d, 0)}
            for d in day_list
        ],
        "funnel": [
            {"stage": "captured", "count": captured},
            {"stage": "replied", "count": replied},
            {"stage": "qualified", "count": qualified},
            {"stage": "hot", "count": hot},
            {"stage": "bought", "count": bought},
        ],
        "rates": {
            "reply_rate_pct": _pct(replied, captured),
            "qualified_pct": _pct(qualified, captured),
            "conversion_pct": _pct(bought, captured),
            "win_rate_pct": _pct(bought, bought + lost),  # of leads marked Bought or Lost
        },
        "first_response": {
            "measured": len(response_times),
            "unanswered": no_response,
            "median_seconds": round(statistics.median(response_times), 1) if response_times else None,
            "p95_seconds": round(response_times[math.ceil(0.95 * len(response_times)) - 1], 1)
            if response_times
            else None,
            "within_target_pct": _pct(
                sum(1 for t in response_times if t <= RESPONSE_TARGET_SECONDS), len(response_times)
            ),
            "target_seconds": RESPONSE_TARGET_SECONDS,
        },
        "handoffs": len(handoffs),
        "follow_ups": {
            "sent": len(follow_ups_sent),
            "leads_followed_up": len(followed_up),
            "re_engaged": sum(1 for lead in followed_up if _re_engaged(msgs.get(lead.lead_id, []))),
            "opt_ins": sum(1 for t, _ in consent if t == ConsentType.OPT_IN),
            "opt_ins_by_source": dict(
                Counter(src or "unknown" for t, src in consent if t == ConsentType.OPT_IN)
            ),
            "opt_outs": sum(1 for t, _ in consent if t == ConsentType.OPT_OUT),
        },
        "hot_label_feedback": {
            "answered": len(latest_feedback),
            "agreed": sum(latest_feedback.values()),
            "agreement_pct": _pct(sum(latest_feedback.values()), len(latest_feedback)),
            "target_pct": 80,
        },
    }


def costs(session: Session, user_id: str, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    start, end, tz, day_list = _period(days, now)
    rules = get_qualification(session, user_id)
    rows = session.scalars(
        select(Message).where(
            Message.user_id == user_id,
            Message.direction == Direction.OUTBOUND,
            Message.timestamp >= start,
        )
    ).all()
    sent = [m for m in rows if m.delivery_status != "failed"]
    total = sum((Decimal(str(m.estimated_cost or 0)) for m in sent), Decimal("0"))

    by_day: dict[date, Decimal] = defaultdict(Decimal)
    by_channel: dict[str, Decimal] = defaultdict(Decimal)
    by_category: dict[str, dict[str, Any]] = {}
    for m in sent:
        cost = Decimal(str(m.estimated_cost or 0))
        by_day[_aware(m.timestamp).astimezone(tz).date()] += cost
        by_channel[m.channel] += cost
        cat = by_category.setdefault(m.pricing_category or "none", {"messages": 0, "cost": Decimal("0")})
        cat["messages"] += 1
        cat["cost"] += cost

    leads = _leads(session, user_id, start)
    captured = len(leads)
    qualified = sum(1 for lead in leads if (lead.qualification_score or 0) >= rules.warm_threshold)
    bought = sum(1 for lead in leads if lead.status == LeadStatus.CONVERTED)
    service_rate = Decimal(str(get_settings().wa_rate_service_inr))
    free_entry = by_category.get("free_entry", {}).get("messages", 0)

    def per(n: int) -> Decimal | None:
        return (total / n).quantize(Decimal("0.01")) if n else None

    return {
        "period": {"days": days, "from": start, "to": end, "timezone": str(tz)},
        "currency": "INR",
        "total": total.quantize(Decimal("0.01")),
        "messages": len(sent),
        "daily": [
            {"date": d.isoformat(), "cost": by_day.get(d, Decimal("0")).quantize(Decimal("0.01"))}
            for d in day_list
        ],
        "by_channel": {k: v.quantize(Decimal("0.01")) for k, v in by_channel.items()},
        "by_category": {
            k: {"messages": v["messages"], "cost": v["cost"].quantize(Decimal("0.01"))}
            for k, v in by_category.items()
        },
        "per_lead": per(captured),
        "per_qualified_lead": per(qualified),
        "per_bought_lead": per(bought),
        "saved_by_free_entry_window": (service_rate * free_entry).quantize(Decimal("0.01")),
        "note": "Estimates from the rate card in .env; Meta's invoice is the real figure.",
    }
