import {spawn, spawnSync} from 'node:child_process';
import {mkdir, readFile} from 'node:fs/promises';
import path from 'node:path';

const [executable, profileArgument] = process.argv.slice(2);
if (!executable || !profileArgument) throw new Error('Expected browser executable and isolated profile');
const profile = path.resolve(profileArgument);
await mkdir(profile, {recursive: true});
const browser = spawn(executable, ['--headless', '--no-first-run', '--disable-gpu',
  '--disable-background-networking', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'], {windowsHide: true, stdio: 'ignore'});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const sockets = [];

async function connect(url) {
  const socket = new WebSocket(url);
  sockets.push(socket);
  await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
  let sequence = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) pending.get(message.id)?.(message);
  };
  socket.onclose = () => {
    for (const complete of pending.values()) complete({error: {message: 'Browser connection closed'}});
  };
  return (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error(`${method} timed out`));}, 20000);
    pending.set(id, message => {
      clearTimeout(timer); pending.delete(id);
      message.error ? reject(new Error(`${method}: ${message.error.message}`)) : resolve(message.result);
    });
    socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
}

try {
  let port;
  for (let attempt = 0; attempt < 90; attempt++) {
    if (browser.exitCode !== null) throw new Error(`Browser exited: ${browser.exitCode}`);
    try {port = Number((await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break;}
    catch {await pause(1000);}
  }
  if (!port) throw new Error('Browser DevTools endpoint did not start');
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const clients = await Promise.all([connect(version.webSocketDebuggerUrl), connect(version.webSocketDebuggerUrl)]);
  for (const request of clients) {
    await request('Target.setDiscoverTargets', {discover: true, filter: [{}]});
    await request('Target.setAutoAttach', {autoAttach: true, waitForDebuggerOnStart: false, flatten: true});
  }
  for (let iteration = 0; iteration < 25; iteration++) {
    const request = clients[iteration % 2];
    const other = clients[(iteration + 1) % 2];
    const {targetId} = await request('Target.createTarget', {url: 'about:blank', background: true});
    const {sessionId} = await request('Target.attachToTarget', {targetId, flatten: true});
    await request('Runtime.evaluate', {expression: 'document.body.innerHTML = "<iframe src=about:blank></iframe>"'}, sessionId);
    await other('Target.getTargets', {filter: [{}]});
    await request('Target.closeTarget', {targetId});
    await other('Target.setDiscoverTargets', {discover: false});
    await other('Target.setDiscoverTargets', {discover: true, filter: [{}]});
    await other('Target.getTargets', {filter: [{}]});
  }
  if (browser.exitCode !== null) throw new Error(`Browser crashed: ${browser.exitCode}`);
  await clients[0]('Browser.getVersion');
  console.log('DevTools discovery, two clients, iframe creation and 25 tab-close cycles passed.');
} finally {
  for (const socket of sockets) socket.close();
  if (browser.exitCode === null) spawnSync('taskkill.exe', ['/PID', String(browser.pid), '/T', '/F'],
    {windowsHide: true, stdio: 'ignore'});
}
