"""Week 3 channels: comment-to-lead, private replies, spam/complaints, Instagram & Messenger DMs."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.comments import classify_comment, classify_rules
from app.db.base import utcnow
from app.db.enums import CommentIntent
from app.db.models import InboundEvent, Lead, Message, Notification
from app.db.session import session_scope
from app.dev import payloads as p
from app.integrations.factory import get_meta_messaging
from app.llm.base import LLMError
from app.llm.fake import FakeLLMClient
from app.worker.processor import run_once
from tests.conftest import signed_post

IG = "/webhooks/meta/instagram"
PAGE = "/webhooks/meta/page"


def _leads() -> list[Lead]:
    with session_scope() as s:
        return list(s.scalars(select(Lead).order_by(Lead.created_at)))


def _note() -> str:
    with session_scope() as s:
        return s.scalars(select(InboundEvent).order_by(InboundEvent.received_at.desc())).first().result_note


def _outbound(lead_id: str) -> list[Message]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.timestamp)
            )
        )


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Price please?", CommentIntent.PRODUCT_QUESTION),
        ("pp", CommentIntent.PRODUCT_QUESTION),
        ("Evlo price bro", CommentIntent.PRODUCT_QUESTION),
        ("I want this! How to order?", CommentIntent.PRODUCT_QUESTION),
        ("Follow me for free followers www.x.com", CommentIntent.SPAM),
        ("Worst product, refund my money", CommentIntent.COMPLAINT),
        ("😍😍 nice", CommentIntent.OTHER),
        ("@friend look", CommentIntent.OTHER),
    ],
)
def test_classify_rules(text: str, intent: CommentIntent) -> None:
    assert classify_rules(text) == intent


def test_classify_with_llm_and_rule_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLMClient()
    monkeypatch.setattr("app.agents.comments.get_llm_client", lambda: fake)
    fake.enqueue({"intent": "purchase_interest"})
    assert classify_comment("so cute, want one") == (CommentIntent.PURCHASE_INTEREST, "llm")
    fake.enqueue({"intent": "purchase_interest"})  # model fooled by a spam link
    assert classify_comment("buy followers at www.spam.example") == (CommentIntent.SPAM, "rules (override)")

    def boom(m, s):  # noqa: ANN001, ANN202
        raise LLMError("down")

    monkeypatch.setattr("app.agents.comments.get_llm_client", lambda: FakeLLMClient(responder=boom))
    assert classify_comment("price?") == (CommentIntent.PRODUCT_QUESTION, "rules")


def test_unscripted_fake_llm_uses_rules() -> None:
    assert classify_comment("nice pic")[1] == "rules (no LLM configured)"


def test_instagram_question_comment_gets_private_and_public_reply(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(
        client,
        IG,
        p.instagram_comment("air fryer price?", comment_id="C1", from_id="IGSID1", username="priya_k"),
    )
    assert run_once() == {"event_processed": 1, "message_sent": 2}
    [lead] = _leads()
    assert (lead.source, lead.instagram_scoped_id, lead.name) == ("comment", "IGSID1", "priya_k")
    assert lead.product_interest and "Air Fryer" in lead.product_interest
    meta = get_meta_messaging()
    [private] = meta.sent
    assert (private["type"], private["channel"], private["comment_id"]) == (
        "private_reply",
        "instagram",
        "C1",
    )
    assert "₹4,999" in private["text"] and private["text"].rstrip().endswith("?")  # must invite a reply
    assert meta.public_replies == [
        {"channel": "instagram", "comment_id": "C1", "text": "Thanks! We've sent you a message 😊"}
    ]
    comment_row = next(m for m in _all_messages(lead.lead_id) if m.direction == "inbound")
    assert comment_row.media_type == "comment"


def _all_messages(lead_id: str) -> list[Message]:
    with session_scope() as s:
        return list(s.scalars(select(Message).where(Message.lead_id == lead_id)))


def test_same_comment_twice_one_private_reply(client: TestClient, catalog) -> None:  # noqa: ANN001
    payload = p.instagram_comment("price?", comment_id="C2", from_id="IGSID2")
    signed_post(client, IG, payload)
    run_once()
    # Even if the webhook de-duplication record were lost, the comment must not get a second private reply.
    with session_scope() as s:
        for ev in s.scalars(select(InboundEvent)):
            s.delete(ev)
    signed_post(client, IG, payload)
    run_once()
    assert "already queued (idempotent)" in _note()
    assert len([m for m in get_meta_messaging().sent if m["type"] == "private_reply"]) == 1


def test_instagram_comment_then_dm_continues_same_lead(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_comment("price of kettle?", comment_id="C3", from_id="IGSID3"))
    run_once()
    signed_post(client, IG, p.instagram_message("yes, 1 kettle please", igsid="IGSID3"))
    run_once()
    [lead] = _leads()
    dm = get_meta_messaging().sent[-1]
    assert (dm["type"], dm["channel"], dm["recipient_id"]) == ("dm", "instagram", "IGSID3")
    assert [m.payload["type"] for m in _outbound(lead.lead_id)] == ["private_reply", "public_reply", "text"]


def test_facebook_comment_links_messenger_id_from_private_reply(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(
        client,
        PAGE,
        p.facebook_comment("Price please?", comment_id="FBC1", from_id="FBUSER1", from_name="Ravi K"),
    )
    run_once()
    [lead] = _leads()
    assert (lead.facebook_user_id, lead.name, lead.preferred_channel) == ("FBUSER1", "Ravi K", "messenger")
    # The fake API returns recipient_id "scoped-<comment id>" - the commenter's Messenger PSID.
    assert lead.messenger_scoped_id == "scoped-FBC1"
    signed_post(client, PAGE, p.messenger_message("I want the 4L one", psid="scoped-FBC1"))
    run_once()
    assert len(_leads()) == 1
    assert get_meta_messaging().sent[-1] == {
        **get_meta_messaging().sent[-1],
        "type": "dm",
        "recipient_id": "scoped-FBC1",
    }


def test_spam_hidden_no_lead(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_comment("free followers www.spam.example", comment_id="S1"))
    run_once()
    assert get_meta_messaging().hidden == [{"channel": "instagram", "comment_id": "S1"}]
    assert _leads() == []
    assert "comment hidden" in _note()


def test_chit_chat_ignored(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_comment("😍😍 nice"))
    assert run_once() == {"event_ignored": 1}
    assert _leads() == [] and get_meta_messaging().sent == []


def test_own_comment_ignored(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_comment("Thanks for asking! price?", from_id=p.DEFAULT_IG_ACCOUNT_ID))
    run_once()
    assert _note() == "our own comment"


def test_complaint_hands_off_with_alert(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, PAGE, p.facebook_comment("Worst service, never arrived. Refund!", comment_id="FBC9"))
    assert run_once() == {"event_processed": 1, "message_sent": 1, "alert_logged": 1}
    [lead] = _leads()
    assert lead.is_human_takeover
    assert "sorry to hear" in get_meta_messaging().sent[0]["text"]
    with session_scope() as s:
        [n] = s.scalars(select(Notification)).all()
        assert n.kind == "handoff" and "Complaint" in n.body


def test_private_reply_after_seven_days_blocked(client: TestClient, catalog) -> None:  # noqa: ANN001
    payload = p.facebook_comment("Price please?", comment_id="OLD1")
    payload["entry"][0]["changes"][0]["value"]["created_time"] = int(
        (utcnow() - timedelta(days=8)).timestamp()
    )
    signed_post(client, PAGE, payload)
    run_once()
    assert get_meta_messaging().sent == []
    assert "7 days" in _note()


def test_instagram_dm_new_lead_and_window(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_message("induction cooktop price?", igsid="IGDM1"))
    run_once()
    [lead] = _leads()
    assert (lead.source, lead.instagram_scoped_id, lead.preferred_channel) == ("dm", "IGDM1", "instagram")
    assert "₹2,699" in get_meta_messaging().sent[0]["text"]
    # Make the last DM older than 24h: a new automatic DM is no longer allowed.
    with session_scope() as s:
        for m in s.scalars(select(Message).where(Message.direction == "inbound")):
            m.timestamp = utcnow() - timedelta(hours=25)
    from app.messaging.send_gate import OutboundContent, queue_message

    with session_scope() as s:
        result = queue_message(
            s,
            s.get(Lead, lead.lead_id),
            OutboundContent(text="hi"),
            idempotency_key="late",
            channel="instagram",
        )
    assert result.reason == "24-hour instagram messaging window is closed"


def test_instagram_media_dm_hands_off(client: TestClient, catalog) -> None:  # noqa: ANN001
    payload = p.instagram_message("x", igsid="IGDM2")
    msg = payload["entry"][0]["messaging"][0]["message"]
    msg.pop("text")
    msg["attachments"] = [{"type": "audio", "payload": {"url": "https://example.com/a.mp4"}}]
    signed_post(client, IG, payload)
    run_once()
    [lead] = _leads()
    assert lead.is_human_takeover
    assert get_meta_messaging().sent[0]["text"].startswith("Thanks for the voice note!")


def test_stop_on_instagram(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_message("STOP", igsid="IGDM3"))
    run_once()
    [lead] = _leads()
    assert lead.opted_out
    signed_post(client, IG, p.instagram_comment("price?", from_id="IGDM3"))
    run_once()
    assert len(get_meta_messaging().sent) == 1  # only the opt-out confirmation


# ------------------------------------------- found in the Week 3 sandbox run (regression tests)


def test_content_product_links_api(client: TestClient, catalog, auth_headers: dict) -> None:  # noqa: ANN001
    assert client.get("/api/settings/content-products", headers=auth_headers).json() == {"links": {}}
    bad = client.put("/api/settings/content-products", json={"links": {"M1": "NOPE"}}, headers=auth_headers)
    assert bad.status_code == 422 and "NOPE" in bad.text
    ok = client.put(
        "/api/settings/content-products", json={"links": {"M1": "WP-RO-UV-8L"}}, headers=auth_headers
    )
    assert ok.status_code == 200
    assert client.get("/api/settings/content-products", headers=auth_headers).json() == {
        "links": {"M1": "WP-RO-UV-8L"}
    }


def test_vague_comment_on_linked_post_answers_about_that_product(
    client: TestClient, catalog, auth_headers: dict
) -> None:  # noqa: ANN001
    client.put(
        "/api/settings/content-products", json={"links": {"MEDIA9": "WP-RO-UV-8L"}}, headers=auth_headers
    )
    signed_post(client, IG, p.instagram_comment("How much is this?", comment_id="CV1", media_id="MEDIA9"))
    run_once()
    text = get_meta_messaging().sent[0]["text"]
    assert "PureSip RO+UV 8L Water Purifier" in text.split("\n")[1] and "₹12,999" in text
    [lead] = _leads()
    assert lead.product_interest == "PureSip RO+UV 8L Water Purifier"
    assert lead.qualify_slots["category"] == "Water Purifiers"


def test_vague_comment_on_unlinked_post_says_so(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_comment("How much is this?", media_id="MEDIA10"))
    run_once()
    assert "post MEDIA10 has no linked product" in _note()


def test_ctwa_ad_linked_product(client: TestClient, catalog, auth_headers: dict) -> None:  # noqa: ANN001
    client.put(
        "/api/settings/content-products", json={"links": {"AD55": "IC-2000-TCH"}}, headers=auth_headers
    )
    signed_post(
        client,
        "/webhooks/whatsapp",
        p.whatsapp_ctwa("919800022222", "Hello! Can I get more info on this?", ad_id="AD55"),
    )
    run_once()
    from app.integrations.factory import get_whatsapp_sender

    assert "FlameFree 2000W Induction Cooktop – ₹2,699" in get_whatsapp_sender().sent[0]["body"]


def test_comment_time_comes_from_webhook_entry(client: TestClient, catalog) -> None:  # noqa: ANN001
    payload = p.instagram_comment("price?", comment_id="CT1")
    payload["entry"][0]["time"] = 1760000000
    signed_post(client, IG, payload)
    run_once()
    with session_scope() as s:
        comment = s.scalar(select(Message).where(Message.media_type == "comment"))
        assert int(comment.timestamp.timestamp()) == 1760000000


def test_no_false_budget_note(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_message("I want the 6L air fryer", igsid="IGB1"))
    run_once()
    signed_post(client, IG, p.instagram_message("budget 15000, Chennai", igsid="IGB1"))
    run_once()
    assert "nothing within the stated budget" not in _note()


def test_message_without_product_uses_earlier_interest(client: TestClient, catalog) -> None:  # noqa: ANN001
    signed_post(client, IG, p.instagram_message("I want the 6L air fryer", igsid="IGB2"))
    run_once()
    signed_post(client, IG, p.instagram_message("ok thanks", igsid="IGB2"))
    run_once()
    assert "earlier interest" in _note()
    assert "Air Fryer" in get_meta_messaging().sent[-1]["text"]


def test_budget_message_without_product_keeps_earlier_interest(client: TestClient, catalog) -> None:  # noqa: ANN001
    """Found in the Week 4 run: 'Budget 15000. Coimbatore delivery this week?' was answered with a kettle."""
    igsid = "IGMEENA"
    signed_post(client, IG, p.instagram_message("The 6L air fryer. Need 2", igsid=igsid))
    run_once()
    signed_post(
        client,
        IG,
        p.instagram_message("Budget 15000. Coimbatore delivery this week? Want to order", igsid=igsid),
    )
    run_once()
    reply = get_meta_messaging().sent[-1]["text"]
    assert "Air Fryer" in reply.split("\n")[1] and "Kettle" not in reply
    assert "earlier interest" in _note()
