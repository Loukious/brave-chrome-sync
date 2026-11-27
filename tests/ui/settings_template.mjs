import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile, writeFile} from 'node:fs/promises';
import {registerHooks, stripTypeScriptTypes} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {JSDOM} from 'jsdom';

export async function mangleSettingsTemplate(brave, page) {
  const metadata = JSON.parse(await readFile(path.join(brave, 'package.json'), 'utf8'));
  const version = metadata.config.projects.chrome.tag;
  const resource = `chrome/browser/resources/settings/${page}/${page}.html.ts`;
  const response = await fetch(`https://raw.githubusercontent.com/chromium/chromium/${version}/${resource}`,
                               {signal: AbortSignal.timeout(60000)});
  assert.equal(response.status, 200, 'Matching Chromium Settings template must be available');
  const original = await response.text();
  const work = path.resolve(import.meta.dirname, '../../work');
  await mkdir(work, {recursive: true});
  const directory = await mkdtemp(path.join(work, `${page}-`));
  const input = path.join(directory, `${page}.html.ts`);
  const output = path.join(directory, 'generated.html.ts');
  await writeFile(input, original);

  const manglerUrl = pathToFileURL(path.join(brave, 'tools/chromium_src/lit_mangler/lit_mangler.ts')).href;
  const dependencies = new Map([
    ['lit_mangler', manglerUrl],
    ['typescript', import.meta.resolve('typescript')],
    ['jsdom', import.meta.resolve('jsdom')],
  ]);
  const hooks = registerHooks({
    resolve(specifier, context, nextResolve) {
      if (dependencies.has(specifier)) return {shortCircuit: true, url: dependencies.get(specifier)};
      return nextResolve(specifier, context);
    },
  });
  try {
    const {load, write} = await import(manglerUrl);
    load(input);
    await import(pathToFileURL(path.join(brave, 'chromium_src', `${resource}.lit_mangler.ts`)).href);
    write(output);
  } finally {
    hooks.deregister();
  }
  return {version, original, generated: await readFile(output, 'utf8')};
}

// Evaluate the template's conditionals before checking the resulting markup.
export function renderSettingsTemplate(source, element) {
  const litImport = /^import \{html\} from 'chrome:\/\/resources\/lit\/v3_0\/lit\.rollup\.js';$/m;
  assert.match(source, litImport);
  const javascript = stripTypeScriptTypes(source.replace(litImport, ''))
      .replace('export function getHtml', 'function getHtml');
  const html = (strings, ...values) => String.raw({raw: strings}, ...values);
  const render = new Function('html', `${javascript}\nreturn getHtml;`)(html);
  return JSDOM.fragment(render.call(element));
}
