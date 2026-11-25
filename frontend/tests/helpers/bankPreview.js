export function readyPostingSummary({credit, debit, opening, closing, count}) {
  const empty = {count:0, credit_total:'0.00', debit_total:'0.00'};
  return {
    scope:'preview', partial_allowed:true, closing_matches:true, blocking_reason:'',
    ready:{count, credit_total:credit, debit_total:debit},
    linked:{...empty}, posted:{...empty}, pending:{...empty},
    bank_closing:closing, ledger_closing:opening,
    projected_closing:closing, conservative_closing:closing,
  };
}
