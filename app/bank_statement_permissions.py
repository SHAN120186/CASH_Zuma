"""Narrow capability for confirmed bank-statement movements, not generic writes."""
from .security import perms_of
from .section_access import can_edit_section


def can_post_statement(user):
    """Existing writers or bank payers with an editable ledger section."""
    return bool({'write', 'pay_bank'} & perms_of(user)) and can_edit_section(user, 'ledger')
