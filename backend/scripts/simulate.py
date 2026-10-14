"""Send realistic, correctly signed Meta webhook events to a running server.

    python -m scripts.simulate whatsapp --text "Price of the air fryer?"
    python -m scripts.simulate ctwa
    python -m scripts.simulate all
    python -m scripts.simulate --list

Signs each body with META_APP_SECRET from backend/.env, exactly like Meta does,
so the server's signature check is exercised too.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import get_settings
from app.dev import payloads as p
from app.webhooks.signature import SIGNATURE_HEADER, compute_signature


def _send(client: httpx.Client, payload: dict[str, Any], secret: str, label: str, sign: bool = True) -> int:
    body = json.dumps(payload).encode("utf-8")
    path = p.ENDPOINT_FOR_OBJECT[payload["object"]]
    headers = {"Content-Type": "application/json"}
    headers[SIGNATURE_HEADER] = compute_signature(body, secret) if sign else "sha256=" + "0" * 64
    resp = client.post(path, content=body, headers=headers)
    print(f"{label:<22} POST {path:<26} -> {resp.status_code} {resp.text}")
    return resp.status_code


def _scenarios(args: argparse.Namespace) -> dict[str, Callable[[], list[tuple[str, dict[str, Any], bool]]]]:
    text = args.text

    def dup() -> list[tuple[str, dict[str, Any], bool]]:
        payload = p.whatsapp_text(text=text or "Duplicate delivery test")
        return [("duplicate (1st)", payload, True), ("duplicate (2nd)", payload, True)]

    def status_flow() -> list[tuple[str, dict[str, Any], bool]]:
        """Delivery receipts for the most recent message the server sent."""
        time.sleep(1.5)  # give the server's outbox a moment to send earlier replies
        latest = _latest_sent_message()
        if latest is None:
            print("status: no sent message found yet - run another scenario first")
            return []
        mid, phone = latest
        return [
            (f"status {s}", p.whatsapp_status(mid, s, recipient=phone), True) for s in ("delivered", "read")
        ]

    return {
        "whatsapp": lambda: [
            ("whatsapp message", p.whatsapp_text(args.phone, text or "Hi, price of air fryer?"), True)
        ],
        "ctwa": lambda: [
            (
                "ctwa ad message",
                p.whatsapp_ctwa("919876543211", text or "Hi! Is the 6 litre air fryer available?"),
                True,
            )
        ],
        "voice": lambda: [("whatsapp voice note", p.whatsapp_audio("919800000077"), True)],
        "stop": lambda: [("opt-out (STOP)", p.whatsapp_text("919800000099", "STOP", name="Opt Out"), True)],
        "leadgen": lambda: [("lead form", p.leadgen(), True)],
        "leadgen-no-optin": lambda: [
            ("lead form, no opt-in", p.leadgen(leadgen_id="000" + str(int(time.time()))), True)
        ],
        "fb-comment": lambda: [("facebook comment", p.facebook_comment(text or "Price please?"), True)],
        "ig-comment": lambda: [
            ("instagram comment", p.instagram_comment(text or "How much is this?", media_id=args.media), True)
        ],
        "messenger": lambda: [
            ("messenger message", p.messenger_message(text or "Do you deliver to Chennai?"), True)
        ],
        "ig-dm": lambda: [("instagram DM", p.instagram_message(text or "Is the 6L one available?"), True)],
        "echo": lambda: [("instagram echo", p.instagram_message("our own reply", is_echo=True), True)],
        "reaction": lambda: [("facebook reaction", p.facebook_reaction(), True)],
        "duplicate": dup,
        "bad-signature": lambda: [("bad signature", p.whatsapp_text(text="forged"), False)],
        # ---- Week 3 ----
        "ig-spam": lambda: [
            (
                "instagram spam comment",
                p.instagram_comment("Follow me for free followers www.example.com"),
                True,
            )
        ],
        "fb-complaint": lambda: [
            ("facebook complaint", p.facebook_comment("Worst service, my order never arrived. Refund!"), True)
        ],
        "ig-hot": lambda: _ig_hot_conversation(),
        "wa-hot": lambda: [
            (
                f"hot whatsapp {i + 1}",
                p.whatsapp_text(args.lead_phone or "919811100000", t, name="Hot Lead"),
                True,
            )
            for i, t in enumerate(
                [
                    "Hi, I want the 6 litre air fryer",
                    "Need 2 pcs, budget 15000, delivery to Chennai this week. Want to order",
                ]
            )
        ],
        "human": lambda: [
            (
                "asks for a person",
                p.whatsapp_text(args.lead_phone or "919811100001", "Can someone call me please?"),
                True,
            )
        ],
        "status": status_flow,  # last: needs a message sent by an earlier scenario
    }


def _ig_hot_conversation() -> list[tuple[str, dict[str, Any], bool]]:
    """Comment -> (private reply) -> the same person continues in Instagram DMs and becomes hot."""
    igsid = "700000000000777"
    return [
        (
            "hot: ig comment",
            p.instagram_comment("price of air fryer?", from_id=igsid, username="priya_k"),
            True,
        ),
        ("hot: ig dm 1", p.instagram_message("I want the 6L one, 2 pcs", igsid=igsid), True),
        (
            "hot: ig dm 2",
            p.instagram_message("budget 15000, Chennai, need it today. order karna hai", igsid=igsid),
            True,
        ),
    ]


def _latest_sent_message() -> tuple[str, str] | None:
    from sqlalchemy import select

    from app.db.enums import Direction
    from app.db.models import Lead, Message
    from app.db.session import session_scope

    with session_scope() as s:
        row = s.execute(
            select(Message.platform_message_id, Lead.contact_phone)
            .join(Lead, Lead.lead_id == Message.lead_id)
            .where(Message.direction == Direction.OUTBOUND, Message.platform_message_id.is_not(None))
            .order_by(Message.timestamp.desc())
            .limit(1)
        ).first()
    return (row[0], row[1].lstrip("+")) if row else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenario", nargs="?", default="all", help="Scenario name, or 'all'")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--text", default=None, help="Message/comment text")
    parser.add_argument("--phone", default="919876543210", help="Sender WhatsApp number (digits only)")
    parser.add_argument(
        "--media", default=None, help="Instagram media ID for ig-comment (to test product links)"
    )
    parser.add_argument(
        "--lead-phone", default=None, help="Customer number for wa-hot / human (use a new one to repeat)"
    )
    parser.add_argument("--list", action="store_true", help="List scenarios")
    args = parser.parse_args(argv)

    scenarios = _scenarios(args)
    if args.list:
        print("Scenarios: " + ", ".join(scenarios) + ", all")
        return 0
    names = list(scenarios) if args.scenario == "all" else [args.scenario]
    unknown = [n for n in names if n not in scenarios]
    if unknown:
        print(f"Unknown scenario: {unknown[0]}. Use --list.", file=sys.stderr)
        return 2

    secret = get_settings().meta_app_secret
    failures = 0
    try:
        with httpx.Client(base_url=args.url, timeout=10) as client:
            for name in names:
                for label, payload, sign in scenarios[name]():
                    code = _send(client, payload, secret, label, sign)
                    expected_ok = sign
                    if (code == 200) != expected_ok:
                        failures += 1
    except httpx.ConnectError:
        print(f"Cannot reach {args.url}. Is the server running (uvicorn app.main:app)?", file=sys.stderr)
        return 1
    if failures:
        print(f"{failures} request(s) did not get the expected response.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
