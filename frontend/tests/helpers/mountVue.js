import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import * as Vue from 'vue';
import {parse, compileScript, compileTemplate} from 'vue/compiler-sfc';

// Run the real SFC script and template with Vue's renderer, without a browser,
// HTTP server, production data or a second implementation of component logic.
export async function compileComponent(url, overrides = {}) {
  const filename = fileURLToPath(url), source = fs.readFileSync(url, 'utf8');
  const {descriptor} = parse(source, {filename});
  const script = compileScript(descriptor, {id: filename});
  const modules = [];
  const imports = [...script.content.matchAll(/^import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2;?$/gm)];
  let content = script.content;
  for (const [statement, bindings, , specifier] of imports) {
    let module;
    if (specifier === 'vue') module = Vue;
    else if (overrides[specifier]) module = {default: overrides[specifier]};
    else if (specifier.endsWith('.vue')) module = {default: {render: () => Vue.h('component-stub')}};
    else if (specifier.endsWith('.json')) module = {default: JSON.parse(fs.readFileSync(new URL(specifier, url), 'utf8'))};
    else module = await import(new URL(specifier, url).href);
    const index = modules.push(module) - 1;
    const variable = bindings.startsWith('{') ? bindings.replace(/\s+as\s+/g, ': ') : bindings;
    content = content.replace(statement, `const ${variable} = modules[${index}]${bindings.startsWith('{') ? '' : '.default'};`);
  }
  const component = new Function('modules', content.replace('export default', 'return'))(modules);
  const template = compileTemplate({source: descriptor.template.content, filename, id: filename,
    compilerOptions: {bindingMetadata: script.bindings, hoistStatic: false}});
  if (template.errors.length) throw new Error(template.errors.join('\n'));
  const render = template.code.replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"];?/g,
    (_, names) => 'const {' + names.replace(/\s+as\s+/g, ': ') + '} = Vue;')
    .replace('export function render', 'return function render');
  component.render = new Function('Vue', render)(Vue);
  return component;
}

function element(tag) {
  const node = {tag, tagName: tag.toUpperCase(), children: [], parent: null, props: {}, text: '',
    scrollLeft: 0, scrollTop: 0, clientLeft: 0, clientTop: 0,
    clientWidth: 0, clientHeight: 0, offsetWidth: 0, offsetHeight: 0, scrollWidth: 0,
    value: '', selectedIndex: -1, addEventListener() {}, removeEventListener() {}, scrollIntoView() {this.scrolled = true;},
    querySelector(selector) {
      // The renderer supports the tag, class and attribute selectors used by the
      // real component; a test supplies layout boxes rather than a fake scroll function.
      const match = selector.match(/^(?:([a-z][\w-]*)|\.([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/i);
      if (!match || !match.slice(1).some(Boolean)) throw new Error(`Unsupported test selector: ${selector}`);
      const [, wantedTag, wantedClass, attribute, value] = match;
      const matches = child => child.tag && (!wantedTag || child.tag === wantedTag) &&
        (!wantedClass || String(child.props.class || '').split(/\s+/).includes(wantedClass)) &&
        (!attribute || attribute in child.props && (value === undefined || String(child.props[attribute]) === value));
      return this.children.map(child => find(child, matches)).find(Boolean) ?? null;
    },
    getBoundingClientRect() {
      const box = this.layout || {}, width = box.width ?? this.offsetWidth, height = box.height ?? this.offsetHeight;
      let left = box.left ?? 0, top = box.top ?? 0;
      for (let parent = this.parent; parent; parent = parent.parent) {
        left -= parent.scrollLeft || 0; top -= parent.scrollTop || 0;
      }
      return {x:left, y:top, left, top, right:left + width, bottom:top + height, width, height};
    },
    getAttribute(key) {return this.props[key];}, getRootNode() {return globalThis.document;}};
  Object.defineProperty(node, 'options', {get() {return node.children.filter(child => child.tag === 'option');}});
  Object.defineProperty(node, 'firstElementChild', {get() {return node.children.find(child => child.tag) ?? null;}});
  return node;
}

export const text = node => (node.text || '') + (node.children || []).map(text).join('');
export const find = (node, check) => check(node) ? node : (node.children || []).map(child => find(child, check)).find(Boolean);
export const all = (node, check) => [...(check(node) ? [node] : []), ...(node.children || []).flatMap(child => all(child, check))];
export async function settle() {for (let i = 0; i < 4; i++) {await Promise.resolve(); await Vue.nextTick();}}

export function mount(component, props) {
  globalThis.Document ??= class Document {};
  globalThis.ShadowRoot ??= class ShadowRoot {};
  globalThis.document ??= {activeElement: null, querySelector: () => ({scrollIntoView() {}})};
  const renderer = Vue.createRenderer({
    createElement: element, createText: value => ({text: value}), createComment: value => ({comment: value}),
    setText: (node, value) => {node.text = value;}, setComment: (node, value) => {node.comment = value;},
    setElementText: (node, value) => {node.text = value; node.children = [];},
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null,
    insert(node, parent, anchor = null) {
      if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1);
      node.parent = parent;
      const at = anchor ? parent.children.indexOf(anchor) : -1;
      parent.children.splice(at < 0 ? parent.children.length : at, 0, node);
    },
    remove(node) {if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null;},
    patchProp(node, key, previous, value) {
      node.props[key] = value;
      if (key === 'value') {node._value = value; node.value = value;}
      if (key === 'type') node.type = value;
    },
  });
  const container = element('root'), app = renderer.createApp(component, props);
  app.config.errorHandler = error => {throw error;};
  app.mount(container);
  return {app, container, state: app._instance.setupState, unmount: () => app.unmount()};
}
