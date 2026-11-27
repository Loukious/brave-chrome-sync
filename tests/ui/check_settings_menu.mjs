import assert from 'node:assert/strict';
import path from 'node:path';
import {mangleSettingsTemplate, renderSettingsTemplate} from './settings_template.mjs';

const brave = path.resolve(process.argv[2]);
const {version, original, generated} = await mangleSettingsTemplate(brave, 'settings_menu');
const element = {
  settingsRefresh2026Enabled_: false,
  shouldHideMenuItem_: () => false,
  showAiPageMenuItem_: () => true,
  hideBottomMenuSeparator_: () => false,
};
const people = renderSettingsTemplate(original, element).getElementById('people');
assert.ok(people, 'Chromium account settings entry is present');
assert.equal(renderSettingsTemplate(generated, element).getElementById('people')?.outerHTML,
             people.outerHTML, 'Google account menu entry must be preserved');

for (const settingsRefresh2026Enabled_ of [false, true]) {
  const menu = renderSettingsTemplate(generated, {...element, settingsRefresh2026Enabled_});
  assert.equal(menu.querySelector('[href="/braveSync"]'), null);
  assert.equal(menu.getElementById('braveSync'), null);
  assert.equal(menu.getElementById('leoAssistant').nextElementSibling.id, 'search');
  assert.equal(menu.getElementById('search').nextElementSibling.id, 'extensions');
  for (const id of ['leoAssistant', 'search', 'extensions', 'about-menu']) {
    assert.equal(menu.querySelectorAll(`[id="${id}"]`).length, 1, `${id} must occur once`);
  }
}
console.log(`Google account entry and Settings navigation pass on Chromium ${version}`);
