"""Background worker: dispatch, ignore rules, retries with backoff, stale claims."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import utcnow
from app.db.enums import EventStatus, EventType
from app.db.models import InboundEvent, Lead
from app.db.session import session_scope
from app.dev import payloads as p
from app.router.dispatcher import HandlerResult, clear_handlers, dispatch, register, registered_types
from app.worker.processor import Worker, process_pending
from tests.conftest import signed_post


def _all() -> list[InboundEvent]:
    with session_scope() as s:
        return list(s.scalars(select(InboundEvent).order_by(InboundEvent.received_at)))


def _make_due(event_id: str) -> None:
    with session_scope() as s:
        s.get(InboundEvent, event_id).next_attempt_at = utcnow() - timedelta(seconds=1)


def test_registered_handlers_week2() -> None:
    assert registered_types() == ["page.leadgen", "whatsapp.message", "whatsapp.status"]


def test_unhandled_event_marked_ignored(client: TestClient) -> None:
    # Instagram comments get a handler in Week 3.
    signed_post(client, "/webhooks/meta/instagram", p.instagram_comment())
    assert process_pending() == {"ignored": 1}
    [ev] = _all()
    assert ev.status == EventStatus.IGNORED
    assert ev.attempts == 1
    assert "no handler" in ev.result_note
    assert ev.error is None
    assert ev.processed_at is not None


def test_echo_always_ignored_even_with_handler(client: TestClient) -> None:
    calls: list[str] = []

    @register(EventType.INSTAGRAM_MESSAGE_ECHO)
    def _h(session, event):  # noqa: ANN001, ANN202
        calls.append(event.id)

    signed_post(client, "/webhooks/meta/instagram", p.instagram_message(is_echo=True))
    process_pending()
    assert calls == []
    assert _all()[0].status == EventStatus.IGNORED


def test_handler_success_and_writes_committed(client: TestClient, user) -> None:  # noqa: ANN001
    clear_handlers()  # replace the real handler with a test one (restored after the test)

    @register(EventType.WHATSAPP_MESSAGE)
    def _h(session, event):  # noqa: ANN001, ANN202
        msg = event.payload["message"]
        session.add(Lead(user_id=event.user_id, contact_phone=msg["from"], source="dm"))
        return HandlerResult()

    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text(from_phone="911111111111"))
    assert process_pending() == {"processed": 1}
    with session_scope() as s:
        lead = s.scalar(select(Lead))
        assert lead.contact_phone == "911111111111"
        assert lead.user_id == user.user_id
    assert _all()[0].error is None


def test_failing_handler_retries_with_backoff_then_fails(client: TestClient, user) -> None:  # noqa: ANN001
    attempts: list[int] = []
    clear_handlers()

    @register(EventType.WHATSAPP_MESSAGE)
    def _h(session, event):  # noqa: ANN001, ANN202
        # Writes made before the failure must be rolled back.
        session.add(Lead(user_id=event.user_id, contact_phone="should-not-persist"))
        session.flush()
        attempts.append(event.attempts)
        raise RuntimeError("boom")

    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text())
    [ev] = _all()

    before = utcnow()
    assert process_pending() == {"retry": 1}
    ev = _all()[0]
    assert ev.status == EventStatus.RETRY
    assert "RuntimeError: boom" in ev.error
    # Backoff base 5s (conftest): first retry ~5s later, so not due yet.
    assert ev.next_attempt_at >= before + timedelta(seconds=4)
    assert process_pending() == {}

    _make_due(ev.id)
    assert process_pending() == {"retry": 1}
    second = _all()[0]
    assert second.next_attempt_at >= utcnow() + timedelta(seconds=9)  # 10s backoff on attempt 2

    _make_due(ev.id)
    assert process_pending() == {"failed": 1}  # WORKER_MAX_ATTEMPTS=3 in tests
    final = _all()[0]
    assert final.status == EventStatus.FAILED
    assert final.attempts == 3
    assert attempts == [1, 2, 3]
    with session_scope() as s:
        assert s.scalar(select(Lead)) is None


def test_stale_processing_event_is_reclaimed(client: TestClient) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text())
    [ev] = _all()
    with session_scope() as s:
        row = s.get(InboundEvent, ev.id)
        row.status = EventStatus.PROCESSING
        row.next_attempt_at = utcnow() - timedelta(minutes=1)  # recently claimed: leave alone
    assert process_pending() == {}
    with session_scope() as s:
        s.get(InboundEvent, ev.id).next_attempt_at = utcnow() - timedelta(minutes=10)  # stale
    assert process_pending() == {"ignored": 1}


def test_processed_events_not_picked_again(client: TestClient) -> None:
    signed_post(client, "/webhooks/whatsapp", p.whatsapp_text())
    assert process_pending() == {"ignored": 1}
    assert process_pending() == {}


def test_dispatch_unknown_type_ignored() -> None:
    ev = InboundEvent(event_type="unknown", payload={}, platform_event_id="x", endpoint="t", trace_id="t")
    assert dispatch(None, ev).status == EventStatus.IGNORED  # type: ignore[arg-type]


def test_register_twice_rejected() -> None:
    import pytest

    clear_handlers()

    @register(EventType.PAGE_LEADGEN)
    def _a(session, event):  # noqa: ANN001, ANN202
        return None

    with pytest.raises(ValueError):

        @register(EventType.PAGE_LEADGEN)
        def _b(session, event):  # noqa: ANN001, ANN202
            return None


def test_worker_loop_processes_on_wake(client: TestClient) -> None:
    signed_post(client, "/webhooks/meta/instagram", p.instagram_comment())

    async def run() -> None:
        worker = Worker(poll_seconds=30)  # long poll: only wake() can trigger quickly
        worker.start()
        worker.wake()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if _all()[0].status == EventStatus.IGNORED:
                break
        await worker.stop()

    asyncio.run(run())
    assert _all()[0].status == EventStatus.IGNORED


def test_webhook_wakes_running_worker(monkeypatch) -> None:  # noqa: ANN001
    """With the worker enabled, an incoming webhook is processed without waiting for a poll."""
    import time

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "worker_enabled", True)
    monkeypatch.setattr(get_settings(), "worker_poll_seconds", 30.0)
    with TestClient(create_app()) as c:
        signed_post(c, "/webhooks/meta/instagram", p.instagram_comment())
        deadline = time.time() + 3
        while time.time() < deadline and _all()[0].status != EventStatus.IGNORED:
            time.sleep(0.05)
    assert _all()[0].status == EventStatus.IGNORED
