import {spawn, spawnSync} from 'node:child_process';
import {mkdir, readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const [executable, launcher, profileArgument, channel] = process.argv.slice(2);
if (!executable || !launcher || !profileArgument || !channel) throw new Error('Expected browser, launcher, isolated profile and channel');
const profile = path.resolve(profileArgument);
await mkdir(profile, {recursive: false});
const browser = spawn(executable, ['--no-first-run', '--disable-gpu', '--disable-background-networking',
  '--remote-debugging-port=0', '--window-position=-32000,-32000',
  `--user-data-dir=${profile}`, 'about:blank'], {windowsHide: true, stdio: 'ignore'});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const reader = path.join(path.dirname(fileURLToPath(import.meta.url)), 'window_properties.ps1');
try {
  let port;
  let window;
  for (let attempt = 0; attempt < 60; attempt++) {
    if (browser.exitCode !== null) throw new Error(`Browser exited: ${browser.exitCode}`);
    try {port = Number((await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]);}
    catch {}
    const result = spawnSync('pwsh', ['-NoProfile', '-File', reader, '-ProcessIds', String(browser.pid)],
      {windowsHide: true, encoding: 'utf8', timeout: 10000});
    if (result.status !== 0) throw new Error(`Window property trace failed: ${result.stderr}`);
    if (result.stdout.trim()) {
      const value = JSON.parse(result.stdout);
      window = (Array.isArray(value) ? value : [value]).find(item => item.AppId === `Loukious.BraveChromeSync.${channel}`);
    }
    if (port && window) break;
    await pause(1000);
  }
  if (!port || !window) throw new Error('Installed browser did not register the installer taskbar identity');
  const [program, ...args] = window.RelaunchArguments;
  if (path.resolve(program).toLowerCase() !== path.resolve(launcher).toLowerCase() ||
      !args.includes(`--user-data-dir=${profile}`) || !args.includes('--profile-directory=Default')) {
    throw new Error('Taskbar launch command does not preserve the stable launcher and profile');
  }
  const before = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const taskbar = spawn(program, [...args, 'about:blank'], {windowsHide: true, stdio: 'ignore'});
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Taskbar launcher timed out')), 30000);
    taskbar.on('error', error => {clearTimeout(timer); reject(error);});
    taskbar.on('exit', code => {clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`Taskbar launcher exited: ${code}`));});
  });
  await pause(1000);
  const after = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  if (before.webSocketDebuggerUrl !== after.webSocketDebuggerUrl || browser.exitCode !== null) throw new Error('Taskbar launch did not reuse the running browser');
  console.log('Native taskbar identity matches the installer; its launch command reuses the stable launcher, profile and browser.');
} finally {
  if (browser.exitCode === null) spawnSync('taskkill.exe', ['/PID', String(browser.pid), '/T', '/F'],
    {windowsHide: true, stdio: 'ignore'});
}
