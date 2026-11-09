// Run Brave's real template mangler on the selected Chromium Settings page.
import assert from 'node:assert/strict';
import {readFile, writeFile, mkdir, mkdtemp} from 'node:fs/promises';
import {registerHooks} from 'node:module';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {JSDOM} from 'jsdom';

const brave = path.resolve(process.argv[2]);
const metadata = JSON.parse(await readFile(path.join(brave, 'package.json'), 'utf8'));
const version = metadata.config.projects.chrome.tag;
const resource = 'chrome/browser/resources/settings/settings_main/settings_main.html';
const response = await fetch(`https://raw.githubusercontent.com/chromium/chromium/${version}/${resource}`,
                             {signal: AbortSignal.timeout(60000)});
assert.equal(response.status, 200, 'Matching Chromium Settings template must be available');
const original = await response.text();
const work = path.resolve(import.meta.dirname, '../../work');
await mkdir(work, {recursive: true});
const directory = await mkdtemp(path.join(work, 'settings-main-'));
const input = path.join(directory, 'settings_main.html');
const output = path.join(directory, 'generated.html');
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
const generated = await readFile(output, 'utf8');
const before = JSDOM.fragment(original);
const after = JSDOM.fragment(generated);
assert.ok(before.querySelector('settings-people-page-index'), 'Native Google account view exists');
assert.equal(after.querySelector('settings-people-page-index')?.outerHTML,
             before.querySelector('settings-people-page-index').outerHTML,
             'Google account view must be preserved');
assert.equal(after.querySelector('settings-brave-sync-page-index'), null);
assert.equal(after.getElementById('braveSync'), null);
assert.ok(after.querySelector('settings-brave-extensions-page-index'));
assert.ok(after.querySelector('settings-brave-system-page-index'));
assert.ok(!generated.includes('this.routes_.BRAVE_SYNC'));
assert.ok(generated.includes('this.routes_.BRAVE_LEO_ASSISTANT'));
console.log(`Google account view and Settings main template pass on Chromium ${version}`);
