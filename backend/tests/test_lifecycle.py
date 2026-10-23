"""Week 6: one full lifecycle per entry path (spec Section 5, Week 6).

Each test goes through the real webhooks, worker, agents, Send Gate, outbox,
dashboard API and analytics - the same path a customer and a salesperson take.
  1. Click-to-WhatsApp ad  -> hot -> opt-in -> salesperson takes over -> Bought
  2. Lead form             -> welcome -> follow-up 3 days later -> reply -> STOP -> Lost
  3. Instagram comment     -> private reply -> DMs -> hot -> salesperson replies -> Bought
"""

from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.consent import OPT_IN_QUESTION
from app.db.base import utcnow
from app.db.models import Lead, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.factory import get_meta_messaging, get_whatsapp_sender
from app.nurture.agent import run_nurture
from app.worker.processor import run_once
from tests.conftest import signed_post


def _lead(**where: str) -> Lead:
    with session_scope() as s:
        q = select(Lead)
        for k, v in where.items():
            q = q.where(getattr(Lead, k) == v)
        return s.scalar(q)


def _outbound(lead_id: str) -> list[Message]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.timestamp)
            )
        )


def _alert_kinds(lead_id: str) -> list[str]:
    with session_scope() as s:
        return list(s.scalars(select(Notification.kind).where(Notification.lead_id == lead_id)))


def _wa(client: TestClient, phone: str, text: str, later: int = 0) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(phone, text, at=int(time.time()) + later))
    run_once()


def test_ctwa_ad_to_bought(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    phone = "919800011111"
    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_ctwa(phone, "Hi! Is the 6L air fryer available?", name="Kavya"),
    )
    run_once()
    lead = _lead(contact_phone="+" + phone)
    assert lead.source == "ctwa_ad"
    [first] = _outbound(lead.lead_id)
    assert "CrispAir 6L" in first.text
    assert (first.pricing_category, first.estimated_cost) == ("free_entry", Decimal("0"))  # 72h free window

    _wa(client, phone, "Need 2, delivery Chennai this week, budget 15000", later=5)
    lead = _lead(lead_id=lead.lead_id)
    assert lead.status == "hot" and "hot_lead" in _alert_kinds(lead.lead_id)
    assert _outbound(lead.lead_id)[-1].text.endswith(OPT_IN_QUESTION)
    _wa(client, phone, "Yes", later=10)
    assert _lead(lead_id=lead.lead_id).whatsapp_opt_in

    needs_you = client.get("/api/leads?view=needs_you", headers=auth_headers).json()
    assert lead.lead_id in [x["lead_id"] for x in needs_you]
    assert client.post(f"/api/leads/{lead.lead_id}/takeover", headers=auth_headers).status_code == 200
    sent = client.post(
        f"/api/leads/{lead.lead_id}/message",
        json={"text": "Hi Kavya, Ravi here - 2 x 6L, delivery Thursday in Chennai. Shall I book?"},
        headers=auth_headers,
    ).json()
    assert sent["decision"] == "queued"
    run_once()
    assert get_whatsapp_sender().sent[-1]["body"].startswith("Hi Kavya, Ravi here")
    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "converted"}, headers=auth_headers)

    detail = client.get(f"/api/leads/{lead.lead_id}", headers=auth_headers).json()
    assert detail["status"] == "converted" and detail["follow_up"]["reason"] == "Marked as Bought."
    summary = client.get("/api/analytics/summary", headers=auth_headers).json()
    assert {f["stage"]: f["count"] for f in summary["funnel"]}["bought"] == 1
    assert summary["first_response"]["within_target_pct"] == 100.0
    costs = client.get("/api/analytics/costs", headers=auth_headers).json()
    assert Decimal(costs["total"]) == 0  # everything inside the free ad window


def test_lead_form_to_lost(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    signed_post(client, "/webhooks/meta/page", p.leadgen(leadgen_id="777000123"))
    run_once()
    lead = _lead(meta_lead_id="777000123")
    assert lead.whatsapp_opt_in and lead.opt_in_source == "lead_form"
    [welcome] = _outbound(lead.lead_id)
    assert welcome.template_name == "lead_form_welcome" and welcome.estimated_cost == Decimal("0.8631")

    later = utcnow() + timedelta(days=3)  # nobody answered for 3 days
    assert run_nurture(as_of=later, now=later) == {"sent": 1}
    follow_up = _outbound(lead.lead_id)[-1]
    assert (follow_up.sender, follow_up.template_name) == ("nurture", "nurture_followup_1")

    phone = lead.contact_phone.lstrip("+")
    three_days = int(later.timestamp()) - int(time.time()) + 3600
    _wa(client, phone, "Yes, tell me more about air fryers", later=three_days)
    replies = [m for m in _outbound(lead.lead_id) if m.sender == "agent"]
    assert replies and "CrispAir" in replies[-1].text  # the follow-up re-engaged them; the assistant answered

    _wa(client, phone, "STOP", later=three_days + 60)
    assert _lead(lead_id=lead.lead_id).opted_out
    much_later = later + timedelta(days=20)
    assert run_nurture(as_of=much_later, now=much_later).get("sent") is None

    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "lost"}, headers=auth_headers)
    closed = client.get("/api/leads?view=closed", headers=auth_headers).json()
    assert [x["lead_id"] for x in closed] == [lead.lead_id]
    summary = client.get("/api/analytics/summary", headers=auth_headers).json()
    assert summary["follow_ups"]["re_engaged"] == 1 and summary["follow_ups"]["opt_outs"] == 1
    assert summary["rates"]["win_rate_pct"] == 0.0


def test_instagram_comment_to_bought(client: TestClient, catalog, auth_headers) -> None:  # noqa: ANN001
    igsid = "700000000000555"
    signed_post(
        client,
        "/webhooks/meta/instagram",
        p.instagram_comment("price of air fryer?", from_id=igsid, username="priya_k"),
    )
    run_once()
    lead = _lead(instagram_scoped_id=igsid)
    kinds = [(m.payload or {}).get("type") for m in _outbound(lead.lead_id)]
    assert kinds == ["private_reply", "public_reply"]

    for i, text in enumerate(
        ["I want the 6L one, 2 pcs", "budget 15000, Chennai, need it today. order karna hai"]
    ):
        time.sleep(0.01 * i)
        signed_post(client, "/webhooks/meta/instagram", p.instagram_message(text, igsid=igsid))
        run_once()
    lead = _lead(lead_id=lead.lead_id)
    assert lead.status == "hot" and lead.qualification_score >= 70

    detail = client.get(f"/api/leads/{lead.lead_id}", headers=auth_headers).json()
    ig = next(o for o in detail["reply_options"] if o["channel"] == "instagram")
    assert ig["free_text"] and not ig["templates"]
    sent = client.post(
        f"/api/leads/{lead.lead_id}/message",
        json={"channel": "instagram", "text": "Hi Priya! Ravi from the store - booking your 2 x 6L today."},
        headers=auth_headers,
    ).json()
    assert sent["decision"] == "queued"
    run_once()
    assert get_meta_messaging().sent[-1]["text"].startswith("Hi Priya! Ravi")
    client.patch(f"/api/leads/{lead.lead_id}", json={"status": "converted"}, headers=auth_headers)
    assert _lead(lead_id=lead.lead_id).status == "converted"
    summary = client.get("/api/analytics/summary", headers=auth_headers).json()
    assert summary["leads"]["by_source"] == {"comment": 1}
