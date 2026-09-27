// Who may pay an approved request. The server applies the same rules; the UI only
// hides actions it would refuse. The general rights 'write' and 'pay' never pay.

export const PAY_RIGHTS = ['pay_bank', 'pay_cash'];

// Bank accounts are paid by the settlement accountant, the cash desk by the cashier.
export const payRight = r => (r.account_kind === 'cash' ? 'pay_cash' : 'pay_bank');

export const canPayAny = has => PAY_RIGHTS.some(right => has(right));

// The author, last editor and both approvers of a request never pay it.
export function canPayRequest(r, userId, has) {
  return r.status === 'approved'
    && has(payRight(r))
    && ![r.creator_id, r.last_editor_id, r.finance_approved_by, r.approved_by].includes(userId);
}
