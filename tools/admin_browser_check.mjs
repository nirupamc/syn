import fs from 'node:fs';

const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const target = targets.find(item => item.type === 'page');
if (!target) throw new Error('No Chrome page target');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
const exceptions = [];
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    const {resolve, reject} = pending.get(message.id);
    pending.delete(message.id);
    return message.error ? reject(new Error(message.error.message)) : resolve(message.result);
  }
  if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails?.exception?.description || message.params.exceptionDetails?.text || 'Runtime exception');
  if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') exceptions.push(message.params.entry.text);
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
});
const evaluate = async expression => (await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true})).result.value;
const waitFor = async (expression, timeout = 12000) => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error(`Timed out: ${expression}`);
};
const secretLine = fs.readFileSync('.env', 'utf8').split(/\r?\n/).find(line => /^(SYN_)?ADMIN_SECRET=/.test(line));
const secret = secretLine?.split('=').slice(1).join('=').trim();
if (!secret) throw new Error('Admin secret not configured');

await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', {width: 1728, height: 900, deviceScaleFactor: 1, mobile: false});
await send('Page.navigate', {url: 'http://127.0.0.1:8001/admin/ui'});
await waitFor("document.readyState === 'complete'");
await evaluate(`document.getElementById('secret-input').value=${JSON.stringify(secret)}; document.querySelector('#auth-form button[type=submit]').click()`);
await new Promise(resolve => setTimeout(resolve, 4000));
const authState = await evaluate(`({authenticated:document.body.classList.contains('authenticated'), error:document.getElementById('auth-error')?.textContent || '', ready:document.readyState})`);
if (!authState.authenticated) throw new Error(`Authentication UI did not advance: ${JSON.stringify({authState, exceptions})}`);
await waitFor("document.getElementById('inf-model').textContent !== 'N/A'");

const sections = ['overview','users','clients','api-keys','models','backends','routing','usage','observability','settings'];
const results = [];
for (const id of sections) {
  await evaluate(`document.querySelector('[data-section="${id}"]').click()`);
  await new Promise(resolve => setTimeout(resolve, 2600));
  results.push(await evaluate(`(() => {
    const active = [...document.querySelectorAll('.section.active')];
    const section = document.getElementById('${id}');
    const rect = section.getBoundingClientRect();
    return {id:'${id}',activeCount:active.length,activeId:active[0]?.id,visible:rect.height>0,
      horizontalOverflow:section.scrollWidth>section.clientWidth+2,textLength:section.innerText.length,
      errorToast:[...document.querySelectorAll('.toast.error')].map(x=>x.textContent)};
  })()`));
}

for (const [button, modal] of [['create-user','modal-create-user'],['create-client','modal-create-client'],['create-key','modal-create-key']]) {
  await evaluate(`document.getElementById('${button}').click()`);
  if (button === 'create-client') {
    try { await waitFor(`document.getElementById('${modal}').open`, 7000); } catch (_) { /* reported below */ }
  } else {
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  const opened = await evaluate(`document.getElementById('${modal}').open`);
  results.push({workflow: modal, opened});
  if (opened) await evaluate(`document.getElementById('${modal}').close()`);
}

await evaluate("setTheme('light'); document.querySelector('[data-section=api-keys]').click()");
await new Promise(resolve => setTimeout(resolve, 1800));
let shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
fs.writeFileSync('.tmp-admin-keys.png', Buffer.from(shot.data, 'base64'));
await evaluate("document.querySelector('[data-section=observability]').click()");
await new Promise(resolve => setTimeout(resolve, 1200));
shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
fs.writeFileSync('.tmp-admin-observability.png', Buffer.from(shot.data, 'base64'));
await evaluate("document.querySelector('[data-section=overview]').click(); setTheme('light')");
await new Promise(resolve => setTimeout(resolve, 350));
shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
fs.writeFileSync('.tmp-admin-light.png', Buffer.from(shot.data, 'base64'));
await evaluate("setTheme('dark')");
await new Promise(resolve => setTimeout(resolve, 350));
shot = await send('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
fs.writeFileSync('.tmp-admin-dark.png', Buffer.from(shot.data, 'base64'));

const final = await evaluate(`({theme:document.documentElement.dataset.theme,runtime:document.getElementById('inf-model').textContent,
  synModel:document.getElementById('inf-syn-model').textContent,backend:document.getElementById('inf-backend').textContent,
  status:document.getElementById('inf-status').textContent,bodyOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,
  secretPersisted:Object.keys(localStorage).some(k=>k!=='syn-theme')})`);
socket.close();
console.log(JSON.stringify({sections:results, final, exceptions}, null, 2));
