import assert from 'node:assert/strict';
import path from 'node:path';
import {mangleSettingsTemplate, renderSettingsTemplate} from './settings_template.mjs';

const brave = path.resolve(process.argv[2]);
const {version, original, generated} = await mangleSettingsTemplate(brave, 'settings_main');
const element = {
  settingsRefresh2026Enabled_: false,
  routes_: {},
  renderPlugin_: () => true,
  showPage_: () => true,
  showAiPage_: () => true,
  shouldShowAll_: () => true,
  showManagedHeader_: () => false,
};
const before = renderSettingsTemplate(original, element);
const after = renderSettingsTemplate(generated, element);

const googleAccount = before.querySelector('settings-people-page-index');
assert.ok(googleAccount, 'Native Google account view exists');
assert.equal(after.querySelector('settings-people-page-index')?.outerHTML,
             googleAccount.outerHTML, 'Google account view must be preserved');
assert.equal(after.querySelector('settings-brave-sync-page-index'), null);
assert.equal(after.getElementById('braveSync'), null);
assert.ok(after.querySelector('settings-brave-extensions-page-index'));
assert.ok(after.querySelector('settings-brave-system-page-index'));
assert.equal(after.getElementById('performance'), null);
assert.ok(!generated.includes('this.routes_.BRAVE_SYNC'));
assert.ok(generated.includes('this.routes_.BRAVE_LEO_ASSISTANT'));
console.log(`Google account view and Settings main template pass on Chromium ${version}`);
