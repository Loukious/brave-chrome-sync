import {spawn, spawnSync} from 'node:child_process';
import {mkdir, readFile} from 'node:fs/promises';
import path from 'node:path';

const [executable, profileArgument] = process.argv.slice(2);
if (!executable || !profileArgument) throw new Error('Expected executable and isolated profile');
const profile = path.resolve(profileArgument);
await mkdir(profile, {recursive:true});
const netLog = path.join(profile, 'sync-route.json');
const environment = {...process.env};
for (const name of ['GOOGLE_API_KEY','GOOGLE_DEFAULT_CLIENT_ID','GOOGLE_DEFAULT_CLIENT_SECRET']) delete environment[name];
const child = spawn(executable, ['--headless','--no-first-run','--disable-gpu',
  '--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,
  `--log-net-log=${netLog}`,'--net-log-capture-mode=Default','about:blank'],
  {env:environment,windowsHide:true,stdio:'ignore'});
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
let socket;
let browserPid;
let closed = false;
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
  const request = (method,params={},sessionId)=>new Promise((resolve,reject)=>{
    const id=++sequence;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`${method} timed out`));},45000);
    pending.set(id,message=>{clearTimeout(timer);pending.delete(id);message.error?reject(new Error(message.error.message)):resolve(message.result);});
    socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });
  browserPid=(await request('SystemInfo.getProcessInfo')).processInfo.find(process=>process.type==='browser')?.id;
  const {targetId}=await request('Target.createTarget',{url:'about:blank',background:true});
  const {sessionId}=await request('Target.attachToTarget',{targetId,flatten:true});
  // An anonymous request exercises the same network delegate as authenticated Sync.
  // The response is irrelevant: the test checks that Brave does not proxy the route.
  const probe='sync_route_smoke='+Date.now();
  const url='https://clients4.google.com/chrome-sync/command/?'+probe;
  const result=await request('Runtime.evaluate',{expression:`(async()=>{
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),25000);
    try {await fetch(${JSON.stringify(url)},{method:'POST',mode:'no-cors',credentials:'omit',body:'',signal:controller.signal});}
    catch {} finally {clearTimeout(timer);}
    return true;
  })()`,awaitPromise:true,returnByValue:true},sessionId);
  if (result.exceptionDetails || result.result.value !== true) throw new Error('Could not exercise Google Sync network routing');
  await request('Browser.close');
  closed=true;
  let log;
  for (let attempt=0;attempt<90;attempt++) {
    try {log=JSON.parse(await readFile(netLog,'utf8'));break;} catch {await pause(250);}
  }
  if (!log) throw new Error('Browser did not finish its Sync routing log');
  const requests=log.events.flatMap(event=>{
    const candidate=event.params?.url;
    if (typeof candidate !== 'string' || !candidate.includes(probe)) return [];
    try {return [new URL(candidate)];} catch {return [];}
  }).filter(url=>url.pathname==='/chrome-sync/command/');
  if (!requests.length || requests.some(url=>url.hostname!=='clients4.google.com'))
    throw new Error('Packaged browser rerouted Google Sync to a proxy or did not exercise the route');
  console.log(`Google Sync requests reach Google directly: ${version.Browser}`);
} finally {
  socket?.close();
  if (!closed && browserPid) spawnSync('taskkill.exe',['/PID',String(browserPid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
  if (child.exitCode === null && !closed) child.kill();
}
