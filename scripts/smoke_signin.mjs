import {spawn, spawnSync} from 'node:child_process';
import {mkdir, readFile} from 'node:fs/promises';
import path from 'node:path';

const [executable, profileArgument] = process.argv.slice(2);
if (!executable || !profileArgument) throw new Error('Expected executable and isolated profile');
const profile = path.resolve(profileArgument);
await mkdir(profile, {recursive:true});
const environment = {...process.env};
for (const name of ['GOOGLE_API_KEY','GOOGLE_DEFAULT_CLIENT_ID','GOOGLE_DEFAULT_CLIENT_SECRET']) delete environment[name];
const child = spawn(executable, ['--headless','--no-first-run','--disable-gpu',
  '--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],
  {env:environment,windowsHide:true,stdio:'ignore'});
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
let socket;
let browserPid;
let closed=false;
try {
  let port;
  for (let attempt=0;attempt<90;attempt++) {
    if (child.exitCode !== null && child.exitCode !== 0) throw new Error('Packaged browser launch failed');
    try {port=Number((await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);break;}
    catch {await pause(1000);}
  }
  if (!port) throw new Error('Packaged browser did not start');
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  socket = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;
  const pending=new Map();
  socket.onmessage = event=>{const message=JSON.parse(event.data);if(message.id) pending.get(message.id)?.(message);};
  const request = (method,params={})=>new Promise((resolve,reject)=>{
    const id=++sequence;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`${method} timed out`));},15000);
    pending.set(id,message=>{clearTimeout(timer);pending.delete(id);message.error?reject(new Error(message.error.message)):resolve(message.result);});
    socket.send(JSON.stringify({id,method,params}));
  });
  const processes=await request('SystemInfo.getProcessInfo');
  browserPid=processes.processInfo.find(process=>process.type==='browser')?.id;
  await request('Browser.getVersion');
  await request('Browser.close');
  closed=true;
  let allowed;
  for (let attempt=0;attempt<60;attempt++) {
    try {allowed=JSON.parse(await readFile(path.join(profile,'Default','Preferences'),'utf8')).signin?.allowed;}
    catch {}
    if (allowed !== undefined) break;
    await pause(250);
  }
  if (allowed !== true) throw new Error('Packaged browser disabled Google sign-in without runtime environment overrides');
  console.log(`Google sign-in enabled without user configuration: ${version.Browser}`);
} finally {
  socket?.close();
  if (!closed && browserPid) spawnSync('taskkill.exe',['/PID',String(browserPid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  if (child.exitCode === null && !closed) child.kill();
}
