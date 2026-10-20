import {tx,N_} from './i18n/index.js';

// Fallback field names (Russian source text) translated when a message is built.
const COMMON_LABELS = {username:N_('Логин'), password:N_('Пароль'), title:N_('Название'), name:N_('Название'), amount:N_('Сумма'), currency:N_('Валюта'),
  date:N_('Дата'), start:N_('Начало периода'), end:N_('Конец периода'), period_start:N_('Начало периода'), period_end:N_('Конец периода'),
  account_id:N_('Счёт'), category_id:N_('Статья'), counterparty:N_('Контрагент'), purpose:N_('Назначение платежа'), reference:N_('Номер документа')};
const commonLabel = key => Object.hasOwn(COMMON_LABELS, key ?? '') ? tx(COMMON_LABELS[key]) : '';

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
  // Visible names come from the rendered form, which is already in the interface language.
  const value = explicit || legend || associated || commonLabel(control.name) || commonLabel(control.id) || control.placeholder || tx('Обязательное поле');
  return String(value).replace(/\s+/g,' ').replace(/\s*\*\s*$/,'').trim();
}

export function controlProblem(control) {
  const label = controlLabel(control), validity = control.validity || {};
  if (validity.valueMissing) return {label, message:tx('Не заполнено: {label}.', {label})};
  if (validity.badInput || validity.typeMismatch || validity.patternMismatch) return {label, message:tx('Проверьте формат поля «{label}».', {label})};
  if (validity.rangeUnderflow || validity.rangeOverflow) return {label, message:tx('Значение поля «{label}» вне допустимого диапазона.', {label})};
  if (validity.tooShort || validity.tooLong) return {label, message:tx('Проверьте длину поля «{label}».', {label})};
  return {label, message:tx('Проверьте поле «{label}».', {label})};
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
