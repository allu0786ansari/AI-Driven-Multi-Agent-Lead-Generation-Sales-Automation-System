"""Week 5: opt-out in several languages, opting back in, the chat opt-in question, and follow-ups."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.consent import OPT_IN_QUESTION, is_opt_back_in, is_opt_out, normalize, yes_or_no
from app.db.base import utcnow
from app.db.enums import ConsentType, DeliveryStatus, LeadSource, LeadStatus, OptInSource, Sender
from app.db.models import ConsentEvent, Lead, LeadActivity, Message
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.factory import get_whatsapp_sender
from app.messaging.send_gate import Decision, OutboundContent, queue_message
from app.nurture.agent import DONE, in_send_hours, next_follow_up, run_nurture
from app.nurture.config import NurtureConfig, Step, get_config, save_config
from app.worker.processor import run_once
from tests.conftest import signed_post

PHONE = "919812345678"
IST = "Asia/Kolkata"


# ----------------------------------------------------------------- words


@pytest.mark.parametrize(
    "text",
    [
        "STOP", "stop!!", "Stop please", "UNSUBSCRIBE", "Stop promotions", "Stop messages", "opt-out",
        "band karo", "BAND KARO 🙏", "message mat bhejo", "बंद करो", "मत भेजो", "நிறுத்து",
        "anupa vendam", "Don't message me", "Do not message me.", "no more messages pls",
        # found by the held-out evaluation set (Week 5)
        "stop sending these offers", "Unsubscribe me", "no more promotions please", "dont send messages",
        "please stop the whatsapp messages",
    ],
)  # fmt: skip
def test_opt_out_words(text: str) -> None:
    assert is_opt_out(text)


@pytest.mark.parametrize(
    "text",
    [
        "cancel my order", "stop, I want the 6L one", "band ho gaya kya?", "is the shop open now?",
        "the mixer stopped working", "vendam", "nahi chahiye", "", None,
        "don't send me the 4L, send the 6L", "no more questions, I'll take it", "stop the order",
        "can you stop the delivery till monday", "stop this", "Can I stop the order and get a smaller one?",
        "no more offers on the kettle? price is high, any discount on this product",
    ],
)  # fmt: skip
def test_not_opt_out(text: str | None) -> None:
    assert not is_opt_out(text)


def test_normalize_keeps_indian_scripts() -> None:
    # Python's \\w drops the anusvara, which turned "बंद" into "बद" (would never match).
    assert normalize("बंद!!") == "बंद"
    assert normalize("  நிறுத்து 🙏 ") == "நிறுத்து"


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("Yes", True), ("haan ji", True), ("ok 👍", True), ("हाँ", True), ("சரி", True), ("Sure!", True),
        ("No thanks", False), ("nahi", False), ("இல்லை", False), ("not now", False),
        ("yes but what is the price?", None), ("6L", None),
    ],
)  # fmt: skip
def test_yes_or_no(text: str, answer: bool | None) -> None:
    assert yes_or_no(text) is answer


def test_start_words() -> None:
    assert (
        is_opt_back_in("START") and is_opt_back_in("start please") and not is_opt_back_in("start delivery?")
    )


# ----------------------------------------------------------------- helpers


def _lead(phone: str = PHONE) -> Lead:
    with session_scope() as s:
        return s.scalar(select(Lead).where(Lead.contact_phone == "+" + phone))


def _outbound(lead_id: str) -> list[Message]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.timestamp)
            )
        )


def _consents(lead_id: str) -> list[ConsentEvent]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(ConsentEvent).where(ConsentEvent.lead_id == lead_id).order_by(ConsentEvent.timestamp)
            )
        )


def _say(client: TestClient, text: str, phone: str = PHONE, later: int = 0) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(phone, text, at=int(time.time()) + later))
    run_once()


FULL_DETAILS = "Need 2 CrispAir 6L air fryer, budget 15000, delivery Chennai this week"


# ------------------------------------------------------------ opt-out flows


def test_template_stop_button_opts_out(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_button(PHONE, "Stop promotions"))
    run_once()
    lead = _lead()
    assert lead.opted_out
    assert [c.type for c in _consents(lead.lead_id)] == [ConsentType.OPT_OUT]


def test_start_opts_back_in(client: TestClient, catalog) -> None:  # noqa: ANN001
    _say(client, "STOP")
    _say(client, "START", later=5)
    lead = _lead()
    assert not lead.opted_out and lead.whatsapp_opt_in and lead.opt_in_source == OptInSource.KEYWORD
    assert [c.type for c in _consents(lead.lead_id)] == [ConsentType.OPT_OUT, ConsentType.OPT_IN]
    assert "Welcome back" in _outbound(lead.lead_id)[-1].text


def test_opted_out_customer_who_writes_again_gets_answers_but_no_templates(
    client: TestClient, catalog
) -> None:  # noqa: ANN001
    _say(client, "stop")
    _say(client, "kettle price?", later=5)
    lead = _lead()
    replies = _outbound(lead.lead_id)
    assert len(replies) == 2 and "QuickBoil" in replies[1].text
    with session_scope() as s:
        lead = s.get(Lead, lead.lead_id)
        lead.whatsapp_opt_in = True  # even with an old opt-in, nothing the business starts may go out
        result = queue_message(
            s, lead, OutboundContent(template_name="nurture_followup_1", template_params=["A", "B"]),
            idempotency_key="t1",
        )  # fmt: skip
        assert (result.decision, result.reason) == (Decision.BLOCKED, "lead opted out")


def test_opted_out_customer_is_not_answered_before_writing_again(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        now = utcnow()
        lead = Lead(
            user_id=user.user_id, contact_phone="+919800000001", opted_out=True, opted_out_at=now,
            last_inbound_at=now - timedelta(minutes=1), service_window_expires_at=now + timedelta(hours=23),
        )  # fmt: skip
        s.add(lead)
        s.flush()
        result = queue_message(s, lead, OutboundContent(text="Hello"), idempotency_key="x")
        assert result.decision == Decision.BLOCKED


# ------------------------------------------------------- opt-in question


def _ctwa(client: TestClient, text: str, phone: str = PHONE) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_ctwa(phone, text, name="Kavya Iyer"))
    run_once()


def test_opt_in_question_asked_once_when_nothing_left_to_ask_and_yes_is_recorded(
    client: TestClient, catalog
) -> None:  # noqa: ANN001
    _ctwa(client, FULL_DETAILS)
    lead = _lead()
    [reply] = _outbound(lead.lead_id)
    assert reply.text.endswith(OPT_IN_QUESTION)
    assert "Which one would you like" not in reply.text  # one question, not two (found in the browser run)
    assert lead.opt_in_asked_at is not None and not lead.whatsapp_opt_in

    _say(client, "Yes 👍", later=5)
    lead = _lead()
    assert lead.whatsapp_opt_in and lead.opt_in_source == OptInSource.CTWA_MESSAGE
    [consent] = _consents(lead.lead_id)
    assert (consent.type, consent.wording_shown) == (ConsentType.OPT_IN, OPT_IN_QUESTION)
    assert "offers and reminders" in _outbound(lead.lead_id)[-1].text

    _say(client, "Is the 6L in stock?", later=10)
    assert not _outbound(lead.lead_id)[-1].text.endswith(OPT_IN_QUESTION)  # never asked twice


def test_no_to_the_opt_in_question(client: TestClient, catalog) -> None:  # noqa: ANN001
    _ctwa(client, FULL_DETAILS)
    _say(client, "nahi", later=5)
    lead = _lead()
    assert not lead.whatsapp_opt_in and _consents(lead.lead_id) == []
    assert "won't send you offers" in _outbound(lead.lead_id)[-1].text
    with session_scope() as s:
        [act] = s.scalars(select(LeadActivity).where(LeadActivity.kind == "consent")).all()
        assert act.detail["answer"] == "no"


def test_yes_without_a_question_is_just_a_message(client: TestClient, catalog) -> None:  # noqa: ANN001
    _ctwa(client, "air fryer price?")  # qualifying questions still open: no opt-in question yet
    lead = _lead()
    assert lead.opt_in_asked_at is None
    _say(client, "yes", later=5)
    assert not _lead().whatsapp_opt_in


def test_no_opt_in_question_when_follow_ups_are_off(client: TestClient, catalog) -> None:  # noqa: ANN001
    with session_scope() as s:
        save_config(s, catalog.user_id, NurtureConfig(enabled=False))
    _ctwa(client, FULL_DETAILS)
    assert _lead().opt_in_asked_at is None


# ------------------------------------------------------------ follow-ups


def _opted_in_lead(user_id: str, *, quiet_since: datetime, phone: str = "+919811122233", **kw) -> str:  # noqa: ANN003
    fields = dict(
        user_id=user_id, name="Priya Raman", contact_phone=phone, whatsapp_opt_in=True,
        opt_in_source=OptInSource.LEAD_FORM, opt_in_at=quiet_since, source=LeadSource.AD_FORM,
        product_interest="Air Fryers", created_at=quiet_since, last_inbound_at=quiet_since,
        status=LeadStatus.WARM,
    )  # fmt: skip
    with session_scope() as s:
        lead = Lead(**{**fields, **kw})
        s.add(lead)
        s.flush()
        return lead.lead_id


def _tick(when: datetime) -> dict[str, int]:
    """A follow-up pass with the clock set to `when`."""
    return run_nurture(as_of=when, now=when)


def _follow_ups(lead_id: str) -> list[Message]:
    return [m for m in _outbound(lead_id) if m.sender == Sender.NURTURE]


# Monday 12:00 IST - inside sending hours, so due times are not moved.
BASE = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)


def test_first_follow_up_after_two_quiet_days(user) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    assert _tick(BASE + timedelta(days=1)) == {"scheduled": 1}
    with session_scope() as s:
        assert s.get(Lead, lead_id).nurture_next_at == BASE + timedelta(days=2)
    assert _follow_ups(lead_id) == []

    assert _tick(BASE + timedelta(days=2, minutes=1)) == {"sent": 1}
    [m] = _follow_ups(lead_id)
    assert (m.template_name, m.payload["params"], m.pricing_category) == (
        "nurture_followup_1", ["Priya", "Air Fryers"], "marketing",
    )  # fmt: skip
    assert m.estimated_cost == Decimal("0.8631")
    with session_scope() as s:
        assert s.get(Lead, lead_id).nurture_step == 1
        assert s.scalar(select(LeadActivity.kind).where(LeadActivity.lead_id == lead_id)) == "follow_up"

    # Same pass again: nothing new (the offer step is skipped without offer text; last check at day 10)
    assert _tick(BASE + timedelta(days=2, minutes=2)) == {"scheduled": 1}
    assert len(_follow_ups(lead_id)) == 1


def test_whole_sequence_with_offer_then_done(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        save_config(s, user.user_id, NurtureConfig(offer_text="free delivery this week"))
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    for day in (2, 5, 10):
        assert _tick(BASE + timedelta(days=day, minutes=1)).get("sent") == 1
    names = [m.template_name for m in _follow_ups(lead_id)]
    assert names == ["nurture_followup_1", "nurture_offer", "nurture_last_check"]
    assert _follow_ups(lead_id)[1].payload["params"] == ["Priya", "Air Fryers", "free delivery this week"]
    assert _tick(BASE + timedelta(days=30)) == {"done": 1}
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert lead.nurture_step == DONE
        assert "All follow-ups sent" in next_follow_up(s, lead, get_config(s, user.user_id)).reason
    assert _tick(BASE + timedelta(days=40)) == {}  # done leads are not looked at again


def test_customer_reply_restarts_the_sequence(client: TestClient, catalog) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(catalog.user_id, quiet_since=utcnow() - timedelta(days=3), phone="+" + PHONE)
    assert run_nurture().get("sent") == 1
    run_once()  # the outbox sends it as a WhatsApp template
    [sent] = get_whatsapp_sender().sent
    assert (sent["type"], sent["name"], sent["params"]) == (
        "template",
        "nurture_followup_1",
        ["Priya", "Air Fryers"],
    )
    assert _follow_ups(lead_id)[0].delivery_status == DeliveryStatus.SENT
    _say(client, "Yes, tell me more", later=5)  # the customer answers the follow-up
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert lead.nurture_step == 0 and lead.nurture_next_at is None
        plan = next_follow_up(s, lead, get_config(s, catalog.user_id))
        assert plan.template == "nurture_followup_1"  # starts again from the first one
        assert plan.due_at >= lead.last_inbound_at + timedelta(days=2)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"whatsapp_opt_in": False}, "No WhatsApp opt-in yet"),
        ({"opted_out": True}, "opted out"),
        ({"is_human_takeover": True}, "A person is handling"),
        ({"status": LeadStatus.CONTACTED}, "Marked as Contacted"),
        ({"status": LeadStatus.CONVERTED}, "Marked as Bought"),
        ({"erased_at": BASE}, "erased"),
    ],
)
def test_who_gets_no_follow_up(user, change: dict, reason: str) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        for k, v in change.items():
            setattr(lead, k, v)
    assert _tick(BASE + timedelta(days=3)).get("sent") is None
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        assert reason in next_follow_up(s, lead, get_config(s, user.user_id)).reason


def test_follow_ups_switched_off(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        save_config(s, user.user_id, NurtureConfig(enabled=False))
    _opted_in_lead(user.user_id, quiet_since=BASE)
    assert _tick(BASE + timedelta(days=3)) == {"done": 1}


def test_limit_per_lead(user) -> None:  # noqa: ANN001
    with session_scope() as s:
        save_config(s, user.user_id, NurtureConfig(max_per_lead=1))
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    _tick(BASE + timedelta(days=2, minutes=1))
    with session_scope() as s:  # the customer wrote and went quiet again: the limit still applies
        s.get(Lead, lead_id).last_inbound_at = BASE + timedelta(days=3)
        s.get(Lead, lead_id).nurture_step = 0
    assert _tick(BASE + timedelta(days=9)) == {"done": 1}
    assert len(_follow_ups(lead_id)) == 1


def test_no_new_sequence_for_long_quiet_customers(user) -> None:  # noqa: ANN001
    """Found when upgrading the Week 4 database: old leads would get a sudden "checking in"."""
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    assert _tick(BASE + timedelta(days=31)) == {"done": 1}
    assert _follow_ups(lead_id) == []
    with session_scope() as s:
        plan = next_follow_up(s, s.get(Lead, lead_id), get_config(s, user.user_id), BASE + timedelta(days=31))
        assert "Quiet for more than 30 days" in plan.reason
    # a sequence that already started finishes normally
    other = _opted_in_lead(user.user_id, quiet_since=BASE, phone="+919811100099")
    _tick(BASE + timedelta(days=2, minutes=1))
    assert _tick(BASE + timedelta(days=31)).get("sent") == 1
    assert len(_follow_ups(other)) == 2


def test_sending_hours() -> None:
    cfg = NurtureConfig()  # 10:00-19:00 IST
    ist = lambda h, m=0: datetime(2026, 10, 19, h, m, tzinfo=UTC) - timedelta(hours=5, minutes=30)  # noqa: E731
    assert in_send_hours(ist(12), cfg) == ist(12)
    assert in_send_hours(ist(8), cfg) == ist(10)
    assert in_send_hours(ist(23, 30), cfg) == ist(10) + timedelta(days=1)
    assert in_send_hours(ist(19), cfg) == ist(10) + timedelta(days=1)


def test_due_at_night_waits_for_the_morning(user) -> None:  # noqa: ANN001
    night = datetime(2026, 10, 19, 17, 0, tzinfo=UTC)  # 22:30 IST
    lead_id = _opted_in_lead(user.user_id, quiet_since=night)
    assert _tick(night + timedelta(days=2, minutes=5)) == {"scheduled": 1}
    with session_scope() as s:
        due = s.get(Lead, lead_id).nurture_next_at
    assert due == datetime(2026, 10, 22, 4, 30, tzinfo=UTC)  # 10:00 IST next morning


def test_follow_up_never_sent_twice_by_parallel_runs(user) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE)
    _tick(BASE + timedelta(days=2, minutes=1))
    with session_scope() as s:  # a second process that had read the old state
        s.get(Lead, lead_id).nurture_step = 0
    _tick(BASE + timedelta(days=2, minutes=1))
    assert len(_follow_ups(lead_id)) == 1


def test_takeover_blocks_follow_up_at_the_gate(user) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE, is_human_takeover=True)
    with session_scope() as s:
        result = queue_message(
            s, s.get(Lead, lead_id), OutboundContent(template_name="nurture_followup_1"),
            idempotency_key="n1", sender=Sender.NURTURE,
        )  # fmt: skip
    assert result.reason == "human takeover active"


def test_config_rules() -> None:
    with pytest.raises(ValueError, match="later than the one before"):
        NurtureConfig(steps=[Step(after_days=3, template="a"), Step(after_days=2, template="b")])
    with pytest.raises(ValueError, match="earlier than"):
        NurtureConfig(send_from_hour=19, send_until_hour=10)
    assert [s.template for s in NurtureConfig().active_steps()] == [
        "nurture_followup_1",
        "nurture_last_check",
    ]


# ------------------------------------------------------------------- API


def test_settings_api_and_upcoming(client: TestClient, user, auth_headers) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=utcnow() - timedelta(days=1))
    cfg = client.get("/api/settings/nurture", headers=auth_headers).json()
    assert cfg["enabled"] and len(cfg["steps"]) == 3
    cfg["offer_text"] = "10% off this week"
    assert client.put("/api/settings/nurture", json=cfg, headers=auth_headers).status_code == 200
    bad = {**cfg, "steps": [{"after_days": 5, "template": "a"}, {"after_days": 1, "template": "b"}]}
    assert client.put("/api/settings/nurture", json=bad, headers=auth_headers).status_code == 422

    # Listed right away, before any follow-up pass ran (found in the Week 5 browser run)
    early = client.get("/api/nurture/upcoming", headers=auth_headers).json()
    assert [u["lead_id"] for u in early["upcoming"]] == [lead_id]
    assert client.post("/api/nurture/run", headers=auth_headers).json() == {"scheduled": 1}
    up = client.get("/api/nurture/upcoming", headers=auth_headers).json()
    assert [u["lead_id"] for u in up["upcoming"]] == [lead_id] and up["upcoming"][0]["step"] == 1

    detail = client.get(f"/api/leads/{lead_id}", headers=auth_headers).json()
    assert detail["follow_up"]["template"] == "nurture_followup_1" and detail["follow_up"]["reason"] == ""


def test_lead_page_says_why_there_is_no_follow_up(client: TestClient, user, auth_headers) -> None:  # noqa: ANN001
    lead_id = _opted_in_lead(user.user_id, quiet_since=BASE, whatsapp_opt_in=False)
    detail = client.get(f"/api/leads/{lead_id}", headers=auth_headers).json()
    assert detail["follow_up"]["due_at"] is None
    assert "assistant asks once in the chat" in detail["follow_up"]["reason"]


def test_reply_options_after_opt_out(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    _say(client, "stop")
    lead_id = _lead().lead_id

    def wa() -> dict:
        d = client.get(f"/api/leads/{lead_id}", headers=auth_headers).json()
        return next(o for o in d["reply_options"] if o["channel"] == "whatsapp")

    assert (wa()["free_text"], wa()["templates"]) == (False, False)
    _say(client, "what is the kettle price", later=5)
    assert (wa()["free_text"], wa()["templates"]) == (True, False)
    sent = client.post(
        f"/api/leads/{lead_id}/message", json={"text": "Hi! It is in stock."}, headers=auth_headers
    ).json()
    assert sent["decision"] == "queued"
    assert sent["message"]["delivery_status"] == DeliveryStatus.QUEUED
