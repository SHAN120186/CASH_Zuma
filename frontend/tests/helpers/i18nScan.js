// Finds interface text that is not prepared for the Uzbek translation.
//
// Rules checked in every .vue and .js file under src/ (except src/i18n):
// * template text and static attributes must not contain Russian text: use {{tx('…')}} and :title="tx('…')";
// * a Russian string literal in code must be the first argument of tx('…') or N_('…'),
//   or its line must carry an `i18n-ignore` comment (text that is matched, never shown);
// * every tx/N_ key must have an Uzbek translation.
//
// CLI: node tests/helpers/i18nScan.js [src/path …] prints the remaining work per file.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parse, babelParse} from 'vue/compiler-sfc';

const CYRILLIC = /[А-Яа-яЁё]/;
const MARKERS = new Set(['tx', 'N_']);
export const SRC = fileURLToPath(new URL('../../src/', import.meta.url));

export function sourceFiles(roots = [SRC]) {
  const out = [];
  const walk = target => {
    const stat = fs.statSync(target);
    if (stat.isDirectory()) {
      if (path.resolve(target) === path.resolve(SRC, 'i18n')) return;
      for (const name of fs.readdirSync(target).sort()) walk(path.join(target, name));
    } else if (/\.(vue|js)$/.test(target)) out.push(path.resolve(target));
  };
  for (const root of roots) walk(root);
  return out;
}

function literalValue(node) {
  if (node.type === 'StringLiteral') return node.value;
  if (node.type === 'TemplateLiteral' && !node.expressions.length) return node.quasis.map(q => q.value.cooked).join('');
  return null;
}

function walkJs(ast, visit) {
  const stack = [[ast, null, null]];
  while (stack.length) {
    const [node, parent, key] = stack.pop();
    if (!node || typeof node.type !== 'string') continue;
    visit(node, parent, key);
    for (const [name, value] of Object.entries(node)) {
      if (['loc', 'start', 'end', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(name)) continue;
      if (Array.isArray(value)) for (const child of value) stack.push([child, node, name]);
      else if (value && typeof value.type === 'string') stack.push([value, node, name]);
    }
  }
}

// Checks one piece of JavaScript. `lineOf(line)` maps a line of `code` to the file line.
function scanJs(code, lineOf, lines, report, keys, {expression = false} = {}) {
  let ast;
  const attempts = expression ? [[`(${code}\n)`, true], [code, false]] : [[code, false]];
  for (const [text, wrapped] of attempts) {
    try {
      ast = babelParse(text, {sourceType: 'module', plugins: [], errorRecovery: false});
      if (wrapped) ast._wrapped = true;
      break;
    } catch { ast = null; }
  }
  if (!ast) {
    if (CYRILLIC.test(code)) report.push({line: lineOf(1), kind: 'unparsed code', text: code.slice(0, 120)});
    return;
  }
  walkJs(ast, (node, parent, key) => {
    if (node.type === 'RegExpLiteral') return;
    if (node.type !== 'StringLiteral' && node.type !== 'TemplateLiteral') return;
    const raw = node.type === 'StringLiteral' ? node.value : node.quasis.map(q => q.value.cooked).join('${…}');
    if (!CYRILLIC.test(raw)) return;
    const line = lineOf(node.loc.start.line);
    const marked = parent?.type === 'CallExpression' && key === 'arguments' && parent.arguments[0] === node &&
      parent.callee.type === 'Identifier' && MARKERS.has(parent.callee.name);
    if (marked) {
      const value = literalValue(node);
      if (value === null) report.push({line, kind: 'dynamic key', text: raw.slice(0, 120)});
      else keys.push({line, key: value});
      return;
    }
    if (parent?.type === 'TemplateLiteral') return;
    if (/i18n-ignore/.test(lines[line - 1] || '')) return;
    report.push({line, kind: 'code text', text: raw.slice(0, 120)});
  });
}

function scanTemplate(node, lines, report, keys) {
  if (!node) return;
  if (node.type === 2) {
    if (CYRILLIC.test(node.content)) report.push({line: node.loc.start.line, kind: 'template text', text: node.content.trim().slice(0, 120)});
    return;
  }
  if (node.type === 5) {
    const exp = node.content;
    scanJs(exp.content, n => exp.loc.start.line + n - 1, lines, report, keys, {expression: true});
    return;
  }
  if (node.type === 1) {
    for (const prop of node.props || []) {
      if (prop.type === 6) {
        if (prop.value && CYRILLIC.test(prop.value.content)) report.push({line: prop.loc.start.line, kind: `attribute ${prop.name}`, text: prop.value.content.slice(0, 120)});
      } else if (prop.type === 7 && prop.exp && prop.name !== 'slot') {
        let code = prop.exp.content;
        if (prop.name === 'for') code = code.replace(/^[\s\S]*?\s+(?:in|of)\s+/, '');
        scanJs(code, n => prop.exp.loc.start.line + n - 1, lines, report, keys, {expression: true});
      }
    }
  }
  for (const child of node.children || []) scanTemplate(child, lines, report, keys);
}

export function scanFile(file) {
  const source = fs.readFileSync(file, 'utf8'), lines = source.split('\n');
  const report = [], keys = [];
  if (file.endsWith('.vue')) {
    const {descriptor, errors} = parse(source, {filename: file});
    if (errors.length) report.push({line: 1, kind: 'parse error', text: String(errors[0].message || errors[0])});
    for (const block of [descriptor.script, descriptor.scriptSetup].filter(Boolean)) {
      const offset = block.loc.start.line - 1;
      scanJs(block.content, n => n + offset, lines, report, keys);
    }
    if (descriptor.template?.ast) for (const child of descriptor.template.ast.children) scanTemplate(child, lines, report, keys);
  } else {
    scanJs(source, n => n, lines, report, keys);
  }
  return {file, report: report.sort((a, b) => a.line - b.line), keys};
}

export function scanAll(roots) {
  return sourceFiles(roots).map(scanFile);
}

export function placeholders(text) {
  return [...String(text).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(',');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const {entries} = await import('../../src/i18n/uz/index.js');
  const roots = process.argv.slice(2).map(item => path.resolve(item));
  let total = 0, missing = 0;
  for (const {file, report, keys} of scanAll(roots.length ? roots : undefined)) {
    const absent = keys.filter(item => !Object.hasOwn(entries, item.key));
    if (!report.length && !absent.length) continue;
    console.log(`\n${path.relative(process.cwd(), file)}: ${report.length} untranslated, ${absent.length} keys without Uzbek text`);
    for (const item of report) console.log(`  ${item.line}: [${item.kind}] ${item.text.replace(/\s+/g, ' ')}`);
    for (const item of absent) console.log(`  ${item.line}: [no translation] ${item.key}`);
    total += report.length; missing += absent.length;
  }
  console.log(`\nTotal: ${total} untranslated, ${missing} keys without Uzbek text`);
  process.exitCode = total || missing ? 1 : 0;
}
