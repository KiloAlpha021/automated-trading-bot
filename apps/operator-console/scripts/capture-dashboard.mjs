import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const targetUrl = process.argv[2] ?? "http://127.0.0.1:4174/";
const output = resolve(process.argv[3] ?? "evidence/font-rendering/dashboard.png");
const profile = await mkdtemp(join(tmpdir(), "atis-chrome-"));

const child = spawn(chrome, [
  "--headless=new",
  "--no-sandbox",
  "--disable-gpu",
  "--hide-scrollbars",
  "--no-first-run",
  "--no-default-browser-check",
  "--force-color-profile=srgb",
  "--force-device-scale-factor=1",
  `--user-data-dir=${profile}`,
  "--remote-debugging-port=0",
  targetUrl,
], { stdio: "ignore" });

async function waitForPort() {
  const file = join(profile, "DevToolsActivePort");
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return (await readFile(file, "utf8")).split(/\r?\n/)[0];
    } catch {
      await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    }
  }
  throw new Error("Chrome DevTools endpoint was not available.");
}

const port = await waitForPort();
const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
const page = targets.find((target) => target.type === "page");
if (!page) throw new Error("Chrome page target was not available.");

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolveOpen, rejectOpen) => {
  socket.addEventListener("open", resolveOpen, { once: true });
  socket.addEventListener("error", rejectOpen, { once: true });
});

let nextId = 0;
const pending = new Map();
socket.addEventListener("message", async ({ data }) => {
  const payload = typeof data === "string" ? data : await data.text();
  const message = JSON.parse(payload);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve: resolveMessage, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolveMessage(message.result);
});

function send(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolveMessage, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Chrome DevTools command timed out: ${method}`));
    }, 10000);
    pending.set(id, {
      resolve: (value) => {
        clearTimeout(timer);
        resolveMessage(value);
      },
      reject,
    });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setTimezoneOverride", { timezoneId: "Europe/London" });
await send("Emulation.setDeviceMetricsOverride", {
  width: Number(process.env.ATIS_VIEWPORT_WIDTH ?? 1536),
  height: Number(process.env.ATIS_VIEWPORT_HEIGHT ?? 1024),
  deviceScaleFactor: 1,
  mobile: false,
  screenWidth: Number(process.env.ATIS_VIEWPORT_WIDTH ?? 1536),
  screenHeight: Number(process.env.ATIS_VIEWPORT_HEIGHT ?? 1024),
});
await send("Emulation.setEmulatedMedia", { media: "screen", features: [{ name: "prefers-color-scheme", value: "dark" }] });
await send("Page.navigate", { url: targetUrl });

const expression = `new Promise(async resolve => {
  await document.fonts.ready;
  await Promise.all([400,500,600,700].map(weight => document.fonts.load(weight + ' 16px Inter')));
  await Promise.all([...document.images].map(image => image.complete ? image.decode().catch(() => {}) : new Promise(done => image.addEventListener('load', done, {once:true}))));
  const style = document.createElement('style');
  style.textContent = '*{animation:none!important;transition:none!important;caret-color:transparent!important}';
  document.head.append(style);
  requestAnimationFrame(() => requestAnimationFrame(() => resolve({
    family: getComputedStyle(document.body).fontFamily,
    weights: [400,500,600,700].map(weight => ({weight, loaded: document.fonts.check(weight + ' 16px Inter')})),
    viewport: [innerWidth, innerHeight],
    deviceScaleFactor: devicePixelRatio,
    locale: navigator.language,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    imagesComplete: [...document.images].every(image => image.complete),
    documentExtent: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    consoleRect: (() => { const r = document.querySelector('.console-frame').getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })(),
  })));
})`;
const evaluated = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
const screenshot = await send("Page.captureScreenshot", { format: "png", fromSurface: true });
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
const qualification = await send("Runtime.evaluate", { expression: `(async()=>{
  const keyboardFocus = {tag: document.activeElement?.tagName, href: document.activeElement?.getAttribute('href'), label: document.activeElement?.getAttribute('aria-label')};
  const results=[];
  for (const link of [...document.querySelectorAll('nav a')]) {
    const expected=link.getAttribute('href'); link.click(); await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
    results.push([expected, document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')]);
  }
  history.back(); await new Promise(r=>addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(r)),{once:true}));
  const backRoute=document.querySelector('nav a[aria-current="page"]')?.getAttribute('href');
  history.forward(); await new Promise(r=>addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(r)),{once:true}));
  const forwardRoute=document.querySelector('nav a[aria-current="page"]')?.getAttribute('href');
  history.replaceState({},'',${JSON.stringify(targetUrl)}); dispatchEvent(new PopStateEvent('popstate'));
  return {keyboardFocus, routes:results, historyCheck:{backRoute,forwardRoute}};
})()`, awaitPromise: true, returnByValue: true });
await mkdir(resolve(output, ".."), { recursive: true });
await writeFile(output, Buffer.from(screenshot.data, "base64"));
console.log(JSON.stringify({ ...evaluated.result.value, ...qualification.result.value }));

socket.close();
child.kill();
await new Promise((resolveExit) => child.once("exit", resolveExit));
await rm(profile, { recursive: true, force: true }).catch(() => {});
