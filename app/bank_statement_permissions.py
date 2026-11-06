"""Narrow capabilities for bank statements, distinct from request payments."""
from fastapi import HTTPException

from .security import perms_of, require_permission
from .section_access import can_edit_section


def can_post_statement(user):
    """Existing writers or bank payers with an editable ledger section."""
    return bool({'write', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'ledger')


def require_existing_bank_fact_link(user, section_error):
    """Correct evidence for an existing Ledger; never authorize a new payment.

    The holding administrator keeps this audited management operation without
    gaining operational rights. Other writers still need bank-payment authority.
    """
    require_permission(user, 'write' if user.role == 'admin' else 'pay_bank')
    if not can_edit_section(user, 'ledger'):
        raise HTTPException(403, section_error)
