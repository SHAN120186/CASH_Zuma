// Who may pay an approved request. The server applies the same rules; the UI only
// hides actions it would refuse. The general rights 'write' and 'pay' never pay.

export const PAY_RIGHTS = ['pay_bank', 'pay_cash'];

// Bank accounts are paid by the settlement accountant, the cash desk by the cashier.
export const payRight = r => (r.account_kind === 'cash' ? 'pay_cash' : 'pay_bank');

export const canPayAny = has => PAY_RIGHTS.some(right => has(right));

// These overview panels are reserved for the company's financial leadership.
// A grant or a temporary approval/payment duty does not change the employee's role.
export function canSeeRequestPanels(user) {
  if (!user) return false;
  const holding = Object.hasOwn(user, 'holding_role') ? user.holding_role : user.role;
  const company = Object.hasOwn(user, 'company_role') ? user.company_role : user.role;
  return ['admin', 'founder'].includes(holding) || ['finance', 'director'].includes(company);
}

// The author, last editor, checking accountant and both approvers of a request never pay it.
export function canPayRequest(r, userId, has) {
  return r.status === 'approved'
    && (!r.cash_funding || r.cash_funding.state === 'received')
    && has(payRight(r))
    && ![r.creator_id, r.last_editor_id, r.checked_by, r.finance_approved_by, r.approved_by].filter(x => x != null).includes(userId);
}
