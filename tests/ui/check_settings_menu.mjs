// Execute the maintained Lit menu override against Chromium's real template.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import path from 'node:path';
import {JSDOM} from 'jsdom';

const brave = path.resolve(process.argv[2]);
const metadata = JSON.parse(await readFile(path.join(brave, 'package.json'), 'utf8'));
const version = metadata.config.projects.chrome.tag;
const resource = 'chrome/browser/resources/settings/settings_menu/settings_menu.html';
const response = await fetch(`https://raw.githubusercontent.com/chromium/chromium/${version}/${resource}`,
                             {signal: AbortSignal.timeout(60000)});
assert.equal(response.status, 200, 'Matching Chromium Settings template must be available');
const template = await response.text();
const override = await readFile(path.join(brave, 'chromium_src', `${resource}.lit_mangler.ts`), 'utf8');
const importLine = "import { mangle } from 'lit_mangler'";
assert.equal(override.split(importLine).length, 2);
const code = stripTypeScriptTypes(override.replace(importLine, ''));
const apply = new Function('mangle', code);

for (const extraMarkup of ['', '<a id="future-unrelated-item" href="/future">Future</a>']) {
  const fragment = JSDOM.fragment(template + extraMarkup);
  const people = fragment.getElementById('people');
  assert.ok(people, 'Chromium account settings entry is present');
  const originalPeople = people.outerHTML;
  let calls = 0;
  apply(callback => { calls++; callback(fragment); });
  assert.equal(calls, 1);
  assert.equal(fragment.getElementById('people').outerHTML, originalPeople,
               'Google account menu entry must be preserved');
  assert.equal(fragment.querySelector('[href="/braveSync"]'), null);
  assert.equal(fragment.getElementById('braveSync'), null);
  assert.equal(fragment.getElementById('leoAssistant').nextElementSibling.id, 'search');
  assert.equal(fragment.getElementById('search').nextElementSibling.id, 'extensions');
  for (const id of ['people', 'leoAssistant', 'search', 'extensions', 'about-menu']) {
    assert.equal(fragment.querySelectorAll(`[id="${id}"]`).length, 1, `${id} must occur once`);
  }
  if (extraMarkup) assert.ok(fragment.getElementById('future-unrelated-item'));
}

console.log(`Google account entry and Settings navigation pass on Chromium ${version}`);
