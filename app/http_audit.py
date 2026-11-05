"""Durable technical HTTP audit, independent of the financial write guard."""
from sqlalchemy.orm import Session

from .db import Audit, engine


def persist_http_audit(payload):
    """Append one captured HTTP event and commit before the response is returned.

    This insert never reads balances, approval rounds or business state. Financial
    writes and their business audit keep their existing guarded transactions.
    Use a separate session: the request handler's transaction is already closed.
    """
    with Session(engine) as session, session.begin():
        session.add(Audit(**payload))
