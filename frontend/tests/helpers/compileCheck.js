// Compiles Vue single-file components without writing a build:
// node tests/helpers/compileCheck.js src/App.vue src/requests/RequestCard.vue
import fs from 'node:fs';
import path from 'node:path';
import {parse, compileScript, compileTemplate} from 'vue/compiler-sfc';

let failed = 0;
for (const target of process.argv.slice(2)) {
  const file = path.resolve(target), source = fs.readFileSync(file, 'utf8');
  try {
    const {descriptor, errors} = parse(source, {filename: file});
    if (errors.length) throw errors[0];
    const script = compileScript(descriptor, {id: file, inlineTemplate: true});
    if (descriptor.template) {
      const template = compileTemplate({source: descriptor.template.content, filename: file, id: file,
        compilerOptions: {bindingMetadata: script.bindings}});
      if (template.errors.length) throw new Error(template.errors.map(String).join('\n'));
    }
    console.log('ok', target);
  } catch (error) {
    failed++;
    console.log('FAILED', target, '\n ', String(error.message || error).split('\n').slice(0, 6).join('\n  '));
  }
}
process.exitCode = failed ? 1 : 0;
