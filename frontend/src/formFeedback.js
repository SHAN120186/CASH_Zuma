const COMMON_LABELS = {username:'Логин', password:'Пароль', title:'Название', name:'Название', amount:'Сумма', currency:'Валюта',
  date:'Дата', start:'Начало периода', end:'Конец периода', period_start:'Начало периода', period_end:'Конец периода',
  account_id:'Счёт', category_id:'Статья', counterparty:'Контрагент', purpose:'Назначение платежа', reference:'Номер документа'};

function labelText(label) {
  const copy = label.cloneNode?.(true);
  if (copy) {
    for (const node of copy.querySelectorAll('input,select,textarea,button,small,svg,[role="alert"]')) node.remove();
    return copy.textContent;
  }
  return label.textContent;
}

export function controlLabel(control) {
  const explicit = control.getAttribute?.('aria-label');
  const legend = ['radio','checkbox'].includes(control.type) ? control.closest?.('fieldset')?.querySelector('legend')?.textContent : '';
  const associated = Array.from(control.labels || []).map(labelText).filter(Boolean).join(' ');
  const value = explicit || legend || associated || COMMON_LABELS[control.name] || COMMON_LABELS[control.id] || control.placeholder || 'Обязательное поле';
  return String(value).replace(/\s+/g,' ').replace(/\s*\*\s*$/,'').trim();
}

export function controlProblem(control) {
  const label = controlLabel(control), validity = control.validity || {};
  if (validity.valueMissing) return {label, message:`Не заполнено: ${label}.`};
  if (validity.badInput || validity.typeMismatch || validity.patternMismatch) return {label, message:`Проверьте формат поля «${label}».`};
  if (validity.rangeUnderflow || validity.rangeOverflow) return {label, message:`Значение поля «${label}» вне допустимого диапазона.`};
  if (validity.tooShort || validity.tooLong) return {label, message:`Проверьте длину поля «${label}».`};
  return {label, message:`Проверьте поле «${label}».`};
}

export function invalidFormFields(form) {
  const seen = new Set(), fields = [];
  for (const control of Array.from(form?.elements || [])) {
    if (control.disabled || control.willValidate === false || control.validity?.valid !== false) continue;
    const problem = controlProblem(control), key = control.type === 'radio' && control.name ? 'radio:'+control.name : control;
    if (seen.has(key)) continue;
    seen.add(key); fields.push({...problem, control});
  }
  return fields;
}

export function focusInvalidField(control) {
  if (!control?.isConnected) return;
  for (let node = control.parentElement; node; node = node.parentElement) {
    if (node.tagName === 'DETAILS') node.open = true;
  }
  control.scrollIntoView?.({behavior:'smooth',block:'center'});
  control.focus?.({preventScroll:true});
}
