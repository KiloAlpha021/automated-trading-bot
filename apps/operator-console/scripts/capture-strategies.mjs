import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { platform, release } from "node:os";
import assert from "node:assert/strict";

const origin = process.argv[2] ?? "http://127.0.0.1:4175";
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const output = resolve(process.argv[3]);
await mkdir(output, { recursive: true });
const profile = await mkdtemp(join(output, "chrome-profile-"));
const chrome = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--force-color-profile=srgb", "--force-device-scale-factor=1", "--lang=en-GB",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
], { stdio: "ignore", windowsHide: true });

let socket;
try {
  let port;
  for (let i = 0; i < 100; i++) {
    try { port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split(/\r?\n/)[0]; break; }
    catch { await new Promise(resolveDelay => setTimeout(resolveDelay, 100)); }
  }
  assert.ok(port, "Chrome debugging endpoint unavailable");
  const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json());
  socket = new WebSocket(targets.find(target => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolveOpen, rejectOpen) => { socket.addEventListener("open", resolveOpen, { once: true }); socket.addEventListener("error", rejectOpen, { once: true }); });
  let next = 0;
  let loads = 0;
  const pending = new Map();
  const network = [];
  const exceptions = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Page.loadEventFired") loads++;
    if (message.method === "Network.requestWillBeSent") network.push(message.params.request.url);
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
    if (pending.has(message.id)) {
      const item = pending.get(message.id); pending.delete(message.id); clearTimeout(item.timer);
      if (message.error) item.reject(Error(message.error.message)); else item.resolve(message.result);
    }
  });
  const send = (method, params = {}) => new Promise((resolveCall, rejectCall) => {
    const id = ++next;
    const timer = setTimeout(() => { pending.delete(id); rejectCall(Error(`Timeout ${method}`)); }, 20000);
    pending.set(id, { resolve: resolveCall, reject: rejectCall, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await send("Emulation.setTimezoneOverride", { timezoneId: "Europe/London" });
  await send("Emulation.setLocaleOverride", { locale: "en-GB" });
  const version = await send("Browser.getVersion");
  const settle = `(async()=>{document.documentElement.classList.add('capture-stable');await document.fonts.ready;await Promise.all([400,500,600,700].map(w=>document.fonts.load(w+' 16px Inter')));await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return true})()`;
  const loaded = async operation => {
    const before = loads; await operation();
    for (let i = 0; i < 400 && loads === before; i++) await new Promise(resolveDelay => setTimeout(resolveDelay, 50));
    assert.ok(loads > before, "Document load event missing"); await evaluate(settle);
  };
  const navigate = path => loaded(() => send("Page.navigate", { url: origin + path }));
  const capture = async (name, path, width, height) => {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false, screenWidth: width, screenHeight: height });
    await navigate(path);
    const state = await evaluate(`(()=>{const box=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};const style=e=>{const c=getComputedStyle(e);return Object.fromEntries(['backgroundColor','backgroundImage','color','fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','boxShadow','opacity','overflow','borderWidth','borderColor','borderRadius','transform','fill','stroke','strokeWidth'].map(k=>[k,c[k]]))};const attrs=e=>Object.fromEntries([...e.attributes].map(a=>[a.name,a.value]));return {url:location.href,viewport:[innerWidth,innerHeight],extent:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],consoleRect:box(document.querySelector('.console-frame')),devicePixelRatio,zoom:visualViewport.scale,locale:navigator.language,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,fontStatus:document.fonts.status,weights:[400,500,600,700].map(w=>({weight:w,loaded:document.fonts.check(w+' 16px Inter')})),images:[...document.images].map(i=>({src:i.getAttribute('src'),complete:i.complete&&i.naturalWidth>0,rect:box(i)})),regions:[...document.querySelectorAll('.strategies-page [data-node-id]')].map(e=>({id:e.getAttribute('data-node-id'),tag:e.tagName,text:e.textContent,rect:box(e),style:style(e),attributes:attrs(e)})),headings:[...document.querySelectorAll('.strategies-page h1,.strategies-page h2')].map(e=>({text:e.textContent,rect:box(e),style:style(e)})),paths:[...document.querySelectorAll('.strategy-performance-chart path')].map(e=>({d:e.getAttribute('d'),transform:e.getAttribute('transform'),style:style(e)})),links:[...document.querySelectorAll('nav a')].map(a=>({href:a.getAttribute('href'),current:a.getAttribute('aria-current'),text:a.textContent})),displayControls:[...document.querySelectorAll('[data-display-only="true"]')].map(e=>({text:e.textContent,disabled:e.disabled,ariaDisabled:e.getAttribute('aria-disabled'),rect:box(e)})),text:document.querySelector('.console-frame').innerText};})()`);
    state.browser = version; state.os = { platform: platform(), release: release() }; state.colourSpace = "sRGB";
    assert.equal(state.devicePixelRatio, 1); assert.equal(state.zoom, 1); assert.ok(state.weights.every(item => item.loaded)); assert.ok(state.images.every(item => item.complete));
    assert.equal(state.links.length, 12); assert.equal(state.links.filter(item => item.current === "page").length, 1); assert.equal(state.links.find(item => item.current === "page").href, "/strategies");
    assert.equal(state.displayControls.length, 9); assert.ok(state.displayControls.every(item => item.ariaDisabled === "true"));
    for (const id of ["230:2563", "230:2564", "230:2565", "230:2566", "230:2567", "230:2568", "230:2569", "230:2570", "230:2571"]) assert.ok(state.regions.some(region => region.id === id));
    const screenshot = await send("Page.captureScreenshot", { format: "png", fromSurface: true });
    await writeFile(join(output, `${name}.png`), Buffer.from(screenshot.data, "base64"));
    await writeFile(join(output, `${name}.json`), JSON.stringify(state, null, 2));
    return state;
  };

  const nativeA = await capture("strategies-native-a", "/strategies", 1536, 1024);
  const nativeB = await capture("strategies-native-b", "/strategies", 1536, 1024);
  assert.deepEqual(nativeA.consoleRect, [0, 0, 1536, 1024]); assert.deepEqual(nativeB.consoleRect, [0, 0, 1536, 1024]);
  const fitted = await capture("strategies-fit", "/strategies?fit=window", 1365, 768);
  assert.deepEqual(fitted.consoleRect, [106.5, 0, 1152, 768]);

  const direct = [];
  for (const route of nativeA.links) { await navigate(route.href); direct.push(await evaluate(`({path:location.pathname,active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')})`)); }
  assert.ok(direct.every(item => item.path === item.active));
  await navigate("/strategies");
  const keyboard = [];
  const key = async (keyValue, code, virtual, modifiers = 0) => { await send("Input.dispatchKeyEvent", { type: "keyDown", key: keyValue, code, windowsVirtualKeyCode: virtual, modifiers }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: keyValue, code, windowsVirtualKeyCode: virtual, modifiers }); };
  await key("Tab", "Tab", 9); keyboard.push(await evaluate(`({href:document.activeElement.getAttribute('href'),outline:getComputedStyle(document.activeElement).outlineStyle})`));
  await key("Tab", "Tab", 9); keyboard.push(await evaluate(`({href:document.activeElement.getAttribute('href'),outline:getComputedStyle(document.activeElement).outlineStyle})`));
  await navigate("/strategies"); await evaluate(`document.querySelector('nav a[href="/portfolio"]').click()`); await evaluate(settle);
  await evaluate(`new Promise(resolve=>{addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))),{once:true});history.back()})`); await evaluate(settle);
  const historyBack = await evaluate(`({path:location.pathname,active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')})`);
  await evaluate(`new Promise(resolve=>{addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))),{once:true});history.forward()})`); await evaluate(settle);
  const historyForward = await evaluate(`({path:location.pathname,active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')})`);
  assert.deepEqual(historyBack, { path: "/strategies", active: "/strategies" }); assert.deepEqual(historyForward, { path: "/portfolio", active: "/portfolio" });
  const report = { captures: 3, direct, keyboard, historyBack, historyForward, exceptions, network: [...new Set(network)] };
  assert.equal(exceptions.length, 0);
  assert.ok(report.network.every(url => url.startsWith(origin) || url === "about:blank" || url.startsWith("data:")), "External request detected");
  await writeFile(join(output, "browser-checks.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ captures: 3, directRoutes: direct.length, keyboard: "PASS", history: "PASS", exceptions: exceptions.length }));
} finally {
  socket?.close(); chrome.kill();
}
