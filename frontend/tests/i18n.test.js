import {test} from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {scanAll, placeholders, SRC} from './helpers/i18nScan.js';
import {entries, patterns, sources} from '../src/i18n/uz/index.js';
import {tx, N_, lang, setLang, intlLocale, LANGUAGES, formatDate} from '../src/i18n/index.js';
import {formatAmount, formatAxisLabels, formatCompact, formatLongDate} from '../src/overview/format.js';

const CYRILLIC = /[А-Яа-яЁё]/;

test('Uzbek dictionary values are Latin text with the same placeholders', () => {
  const problems = [];
  for (const [file, dictionary] of Object.entries(sources)) {
    for (const [key, value] of Object.entries(dictionary)) {
      if (typeof value !== 'string' || !value.trim()) problems.push(`${file}: empty translation for «${key}»`);
      else if (CYRILLIC.test(value)) problems.push(`${file}: Cyrillic in the Uzbek text for «${key}»: ${value}`);
      else if (placeholders(key) !== placeholders(value)) problems.push(`${file}: placeholders differ for «${key}»`);
      if (key !== key.trim()) problems.push(`${file}: key with outer spaces «${key}»`);
    }
  }
  assert.deepEqual(problems, []);
});

test('one Russian text has one Uzbek translation across the dictionaries', () => {
  const seen = new Map(), conflicts = [];
  for (const [file, dictionary] of Object.entries(sources)) {
    for (const [key, value] of Object.entries(dictionary)) {
      const earlier = seen.get(key);
      if (earlier && earlier.value !== value) conflicts.push(`«${key}»: ${earlier.file} → ${earlier.value}; ${file} → ${value}`);
      else if (!earlier) seen.set(key, {file, value});
    }
  }
  assert.deepEqual(conflicts, []);
});

test('patterns are anchored regular expressions with Latin replacements', () => {
  for (const [pattern, replacement] of patterns) {
    assert.ok(pattern instanceof RegExp, String(pattern));
    assert.ok(pattern.source.startsWith('^') && pattern.source.endsWith('$'), String(pattern));
    assert.equal(typeof replacement, 'string');
    assert.ok(!CYRILLIC.test(replacement), replacement);
  }
});

test('every interface text is translatable and translated', () => {
  const problems = [];
  for (const {file, report, keys} of scanAll()) {
    const name = path.relative(SRC, file);
    for (const item of report) problems.push(`${name}:${item.line} [${item.kind}] ${item.text}`);
    for (const item of keys) if (!Object.hasOwn(entries, item.key)) problems.push(`${name}:${item.line} [no translation] ${item.key}`);
  }
  assert.equal(problems.length, 0, problems.slice(0, 80).join('\n') + (problems.length > 80 ? `\n… and ${problems.length - 80} more` : ''));
});

test('tx falls back to Russian, fills placeholders and follows the chosen language', () => {
  const previous = lang.value;
  try {
    setLang('ru');
    assert.equal(tx('Отчёты'), 'Отчёты');
    assert.equal(tx('Версия №{n}', {n: 3}), 'Версия №3');
    assert.equal(intlLocale.value, 'ru-RU');
    setLang('uz');
    assert.equal(tx('Отчёты'), 'Hisobotlar');
    assert.equal(tx('  Отчёты '), '  Hisobotlar ');
    assert.equal(tx('Текст без перевода в словаре'), 'Текст без перевода в словаре');
    assert.equal(tx(null), '');
    assert.equal(tx('ZUMA'), 'ZUMA');
    assert.equal(intlLocale.value, 'uz-Latn-UZ');
    assert.equal(N_('Отчёты'), 'Отчёты');
    setLang('de');
    assert.equal(lang.value, 'uz');
    assert.deepEqual(LANGUAGES.map(item => item.code), ['ru', 'uz']);
  } finally {setLang(previous);}
});

test('compact amounts and dates follow the interface language', () => {
  const previous = lang.value;
  const plain = values => values.map(value => value.replace(/[\u00a0\u202f]/g, ' '));
  try {
    setLang('ru');
    assert.equal(formatAmount(532000000000, 'UZS'), '5,32\u00a0млрд');
    assert.deepEqual(plain(formatAxisLabels([0, 5000000000])), ['0', '50 млн']);
    assert.equal(formatLongDate('2026-10-08'), '8 октября 2026 г.');
    setLang('uz');
    assert.equal(formatAmount(532000000000, 'UZS'), '5,32\u00a0mlrd');
    assert.equal(formatAmount(2540000, 'UZS'), '25,4\u00a0ming');
    assert.deepEqual(plain(formatAxisLabels([0, 5000000000])), ['0', '50 mln']);
    assert.equal(formatCompact(1200000000), '12 mln');
    assert.equal(formatLongDate('2026-10-08'), '8-oktabr, 2026');
    // Browsers have no Uzbek month names; the table is used instead of "M10".
    assert.equal(formatDate(new Date(Date.UTC(2027, 1, 1)), {timeZone: 'UTC', month: 'short', year: 'numeric'}), 'fev 2027');
    assert.equal(formatDate('2026-10-08T06:20:04Z', {timeZone: 'Asia/Tashkent', day: '2-digit', month: '2-digit', year: 'numeric'}), '08.10.2026');
    assert.equal(formatDate('not a date', {}), '');
  } finally {setLang(previous);}
});

test('server patterns translate roles and nested messages but keep names users entered', () => {
  const previous = lang.value;
  try {
    setLang('uz');
    assert.equal(tx('Финансовый директор · ВрИО Кассир'), 'Moliya direktori · Kassir v.b.');
    // «Касса» is also a dictionary word, but here it is the name of an account.
    assert.match(tx('Недостаточно средств на счёте «Касса» на 2026-10-08: 100 UZS.'), /^«Касса» hisobida/);
    assert.equal(tx('Не найдено название: Касса'), 'Nom topilmadi: Касса');
    setLang('ru');
    assert.equal(tx('Финансовый директор · ВрИО Кассир'), 'Финансовый директор · ВрИО Кассир');
  } finally {setLang(previous);}
});
