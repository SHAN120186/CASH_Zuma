// Interface language: Russian is the source text, Uzbek (Latin) is a translation.
// A translated string is looked up by its exact Russian text, so a missing
// translation falls back to Russian instead of showing a key. Financial data,
// names entered by users and document contents are never translated.
import {ref, computed} from 'vue';
import {entries, patterns} from './uz/index.js';

export const LANGUAGES = Object.freeze([
  Object.freeze({code: 'ru', short: 'RU', name: 'Русский', intl: 'ru-RU'}),
  Object.freeze({code: 'uz', short: 'UZ', name: 'Oʻzbekcha', intl: 'uz-Latn-UZ'}),
]);
const STORAGE_KEY = 'cash-zuma-language';

function storage() {
  try {return globalThis.localStorage ?? globalThis.window?.localStorage ?? null;} catch {return null;}
}
function stored() {
  try {return storage()?.getItem?.(STORAGE_KEY) === 'uz' ? 'uz' : 'ru';} catch {return 'ru';}
}

export const lang = ref(stored());
export const intlLocale = computed(() => (LANGUAGES.find(item => item.code === lang.value) || LANGUAGES[0]).intl);

function applyDocument() {
  try {const root = globalThis.document?.documentElement; if (root) root.lang = lang.value;} catch { /* no document in tests */ }
}
applyDocument();

export function setLang(code) {
  if (!LANGUAGES.some(item => item.code === code) || code === lang.value) return;
  lang.value = code;
  try {storage()?.setItem?.(STORAGE_KEY, code);} catch { /* private mode: the choice lasts for this page */ }
  applyDocument();
}

function fill(text, params) {
  return params ? text.replace(/\{(\w+)\}/g, (match, name) => (Object.hasOwn(params, name) && params[name] != null ? String(params[name]) : match)) : text;
}

// Translate one Russian interface string. Parameters fill {name} placeholders:
// tx('Версия №{n}', {n: 3}). Messages that arrive from the server with numbers
// or names inside are matched by the patterns of the dictionary.
export function tx(text, params) {
  if (text === null || text === undefined) return '';
  const source = String(text);
  if (lang.value !== 'uz' || !/[А-Яа-яЁё]/.test(source)) return fill(source, params);
  if (Object.hasOwn(entries, source)) return fill(entries[source], params);
  const trimmed = source.trim();
  if (trimmed !== source && Object.hasOwn(entries, trimmed)) return source.replace(trimmed, fill(entries[trimmed], params));
  for (const [pattern, replacement] of patterns) {
    if (pattern.test(source)) return fill(source.replace(pattern, (...groups) => {
      const values = groups.slice(1, -2);
      return replacement.replace(/\$(\d)/g, (_, index) => {
        const value = values[Number(index) - 1] ?? '';
        // A captured Russian label (status, role) is translated as well when known.
        return Object.hasOwn(entries, value) ? entries[value] : value;
      });
    }), params);
  }
  return fill(source, params);
}

// Marks a Russian string kept in data (menus, help, option lists) that is
// translated later with tx(). It returns the text unchanged; the dictionary
// test checks that every marked string has an Uzbek translation.
export const N_ = text => text;

export const isUzbek = computed(() => lang.value === 'uz');

// Dates. Browsers ship no Uzbek month names (Chromium prints "M10"), so named
// months come from this table; numeric dates use the dd.mm.yyyy form that is
// standard in both languages.
const UZ_MONTHS = {
  long: ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'],
  short: ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avg', 'sen', 'okt', 'noy', 'dek'],
};
const dateFormats = new Map();
function dateFormat(options) {
  const key = JSON.stringify(options);
  if (!dateFormats.has(key)) dateFormats.set(key, new Intl.DateTimeFormat('ru-RU', options));
  return dateFormats.get(key);
}
// formatDate(date, Intl options): Russian as Intl ru-RU; Uzbek with "8-oktabr, 2026",
// "8-okt", "okt 2026" and numeric parts as in Russian.
export function formatDate(date, options = {}) {
  const value = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(value.getTime())) return '';
  const named = options.month === 'long' || options.month === 'short';
  if (lang.value !== 'uz' || !named) return dateFormat(options).format(value);
  const parts = Object.fromEntries(dateFormat({...options, month: 'numeric'}).formatToParts(value).map(part => [part.type, part.value]));
  const month = UZ_MONTHS[options.month][Number(parts.month) - 1];
  let text = parts.day ? `${Number(parts.day)}-${month}` : month;
  if (parts.year) text += parts.day ? `, ${parts.year}` : ` ${parts.year}`;
  if (parts.hour) text += ` ${parts.hour}:${parts.minute}`;
  return text;
}
