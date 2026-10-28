"""Operator-only recovery of abandoned running turns.

Run ONLY after stopping all API workers and confirming no tool execution remains.
A crashed turn is marked interrupted, never replayed or declared completed.
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from agent.config import Settings
from services.db import Message, PlanExecutionClaim, Turn, database


def recover_abandoned(database_url: str, *, minimum_age_minutes: int = 10,
                      apply: bool = False) -> list[str]:
    if minimum_age_minutes < 1:
        raise ValueError("minimum_age_minutes must be positive")
    engine, Session = database(database_url)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minimum_age_minutes)
    recovered = []
    try:
        with Session.begin() as db:
            turns = db.scalars(select(Turn).where(Turn.state == "running")
                               .order_by(Turn.id)).all()
            for turn in turns:
                # Turn has no created_at yet. Check age using the linked
                # assistant placeholder's created_at timestamp.
                message = db.get(Message, turn.assistant_message_id)
                if message is None:
                    continue
                created = message.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > cutoff:
                    continue
                recovered.append(turn.id)
                if apply:
                    turn.state = "interrupted"
                    message.status = "interrupted"
                    message.content = ""
                    claim = db.get(PlanExecutionClaim, turn.conversation_id)
                    if claim is not None and claim.turn_id == turn.id:
                        db.delete(claim)
            # Even a dry run never writes database state.
        return recovered
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--minimum-age-minutes", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-workers-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.confirm_workers_stopped:
        parser.error("--apply requires --confirm-workers-stopped")
    ids = recover_abandoned(args.database_url or Settings().database_url,
                            minimum_age_minutes=args.minimum_age_minutes,
                            apply=args.apply)
    print(("RECOVERED" if args.apply else "DRY_RUN"), len(ids), "turn(s)")
    for turn_id in ids:
        print(turn_id)


if __name__ == "__main__":
    main()
