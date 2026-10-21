"""Follow-ups from the command line.

    python -m scripts.nurture                    # who gets a follow-up next, and why not for the others
    python -m scripts.nurture run                # send the ones that are due now
    python -m scripts.nurture run --days-ahead 3 # send what would be due in 3 days (demo / testing)

With --days-ahead the follow-ups are sent now, as if 3 days had passed. The
server (if running) delivers them within seconds; otherwise they wait in the
outbox until it starts. Uses the same database as the server (backend/.env).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db.base import utcnow
from app.db.models import Lead
from app.db.session import session_scope
from app.nurture.agent import next_follow_up, run_nurture
from app.nurture.config import get_config


def _who(lead: Lead) -> str:
    return lead.name or lead.contact_phone or lead.instagram_scoped_id or lead.lead_id[:8]


def show() -> int:
    tz = ZoneInfo(get_settings().business_timezone)
    with session_scope() as s:
        leads = s.scalars(select(Lead).where(Lead.erased_at.is_(None)).order_by(Lead.created_at)).all()
        if not leads:
            print("No leads yet.")
            return 0
        planned, other = [], []
        for lead in leads:
            plan = next_follow_up(s, lead, get_config(s, lead.user_id))
            (planned if plan.due_at else other).append((lead, plan))
        print(f"Follow-ups planned ({len(planned)}):")
        for lead, plan in sorted(planned, key=lambda x: x[1].due_at):
            when = plan.due_at.astimezone(tz).strftime("%a %d %b %H:%M")
            print(f"  {when}  {_who(lead):<24} {plan.template}  (follow-up {plan.step + 1})")
        print(f"\nNo follow-up ({len(other)}):")
        for lead, plan in other:
            print(f"  {_who(lead):<24} {plan.reason}")
    return 0


def run(days_ahead: float) -> int:
    as_of = utcnow() + timedelta(days=days_ahead) if days_ahead else None
    counts = run_nurture(as_of=as_of)
    label = f" (as if {days_ahead:g} days later)" if days_ahead else ""
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to do"
    print(f"Follow-up pass{label}: {summary}")
    if counts.get("sent"):
        print("Queued follow-ups are delivered by the running server within a few seconds.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", nargs="?", choices=["show", "run"], default="show")
    parser.add_argument("--days-ahead", type=float, default=0.0, help="pretend this many days have passed")
    args = parser.parse_args(argv)
    if args.days_ahead < 0 or args.days_ahead > 60:
        print("--days-ahead must be between 0 and 60", file=sys.stderr)
        return 2
    return run(args.days_ahead) if args.command == "run" else show()


if __name__ == "__main__":
    raise SystemExit(main())
