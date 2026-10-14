"""Router handler for lead form (Instant Form) submissions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.capture import capture_lead_form
from app.config import get_settings
from app.db.enums import EventStatus, EventType, PricingCategory
from app.db.models import InboundEvent
from app.db.tenancy import resolve_user_id
from app.integrations.factory import get_meta_graph
from app.messaging.send_gate import OutboundContent, queue_message
from app.router.dispatcher import HandlerResult, register


@register(EventType.PAGE_LEADGEN)
def on_leadgen(session: Session, event: InboundEvent) -> HandlerResult:
    user_id = event.user_id or resolve_user_id(session)
    if user_id is None:
        return HandlerResult(EventStatus.IGNORED, "no business user exists yet")
    event.user_id = user_id

    leadgen_id = str(event.payload["leadgen"]["leadgen_id"])
    # Network errors here raise, and the worker retries with backoff.
    lead_data = get_meta_graph().fetch_lead(leadgen_id)
    capture = capture_lead_form(session, user_id, lead_data)
    lead = capture.lead

    if not lead.contact_phone:
        return HandlerResult(note=f"lead {lead.lead_id} saved; no phone number, nothing sent")
    if not lead.whatsapp_opt_in:
        return HandlerResult(note=f"lead {lead.lead_id} saved; no WhatsApp opt-in, nothing sent")

    settings = get_settings()
    first_name = (lead.name or "").split()[0] if lead.name else "there"
    product = lead.product_interest or "our products"
    result = queue_message(
        session,
        lead,
        OutboundContent(
            template_name=settings.template_lead_form_welcome,
            template_params=[first_name, product],
            template_category=PricingCategory.MARKETING,
        ),
        idempotency_key=f"leadform:{leadgen_id}",
    )
    return HandlerResult(
        note=f"lead {lead.lead_id} saved; welcome template {result.decision} {result.reason}".strip()
    )
