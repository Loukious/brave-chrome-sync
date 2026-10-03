import {spawn, spawnSync} from 'node:child_process';
import {access, mkdir, readFile} from 'node:fs/promises';
import path from 'node:path';

const [executable, launcher, directory] = process.argv.slice(2);
if (!executable || !launcher || !directory) throw new Error('Expected browser, launcher and isolated LOCALAPPDATA');
const root = path.resolve(directory);
await mkdir(root, {recursive: false});
const profile = path.join(root, 'BraveChromeSync', 'User Data');
const options = {env: {...process.env, LOCALAPPDATA: root}, windowsHide: true, stdio: 'ignore'};
const flags = ['--no-startup-window', '--keep-alive-for-test', '--no-first-run',
  '--disable-gpu', '--disable-background-networking', '--remote-debugging-port=0'];
const browser = spawn(executable, flags, options);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
try {
  let port;
  for (let attempt = 0; attempt < 90; attempt++) {
    if (browser.exitCode !== null) throw new Error(`Direct browser exited: ${browser.exitCode}`);
    try {port = Number((await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break;}
    catch {await pause(1000);}
  }
  if (!port) throw new Error('Direct browser did not use the shared profile');
  const before = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const shortcut = spawn(launcher, flags, options);
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Launcher did not exit')), 30000);
    shortcut.on('error', error => {clearTimeout(timer); reject(error);});
    shortcut.on('exit', code => {clearTimeout(timer); resolve(code);});
  });
  if (code !== 0) throw new Error(`Launcher failed: ${code}`);
  await pause(1000);
  if (browser.exitCode !== null) throw new Error('Original browser exited after launcher invocation');
  const after = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  if (before.webSocketDebuggerUrl !== after.webSocketDebuggerUrl) throw new Error('Launcher created a different browser');
  let upstreamProfileExists = false;
  try {await access(path.join(root, 'BraveSoftware', 'Brave-Browser', 'User Data')); upstreamProfileExists = true;}
  catch {}
  if (upstreamProfileExists) throw new Error('A second default profile was created');
  console.log('Direct browser and stable launcher share the same profile and running browser.');
} finally {
  if (browser.exitCode === null) spawnSync('taskkill.exe', ['/PID', String(browser.pid), '/T', '/F'],
    {windowsHide: true, stdio: 'ignore'});
}
