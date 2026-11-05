import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { platform, release } from "node:os";
import assert from "node:assert/strict";

// Local evidence capture only; this script is never imported by the application.
const origin = process.argv[2] ?? "http://127.0.0.1:4175";
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const output = resolve(process.argv[3]);
await mkdir(output, { recursive: true });
const profile = await mkdtemp(join(output, "chrome-profile-"));
const child = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--force-color-profile=srgb", "--force-device-scale-factor=1", "--lang=en-GB",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
], { stdio: "ignore", windowsHide: true });
let socket;
try {
  let port;
  for (let i=0;i<100;i++) {
    try { port=(await readFile(join(profile,"DevToolsActivePort"),"utf8")).split(/\r?\n/)[0]; break; }
    catch { await new Promise(r=>setTimeout(r,100)); }
  }
  assert.ok(port, "Chrome debugging endpoint unavailable");
  const targets=await fetch(`http://127.0.0.1:${port}/json/list`).then(r=>r.json());
  socket=new WebSocket(targets.find(t=>t.type==="page").webSocketDebuggerUrl);
  await new Promise((yes,no)=>{socket.addEventListener("open",yes,{once:true});socket.addEventListener("error",no,{once:true});});
  let next=0;
  const pending=new Map();
  const network=[]; const exceptions=[]; let loads=0;
  socket.addEventListener("message",({data})=>{
    const msg=JSON.parse(data);
    if(msg.method==="Page.loadEventFired") loads++;
    if(msg.method==="Network.requestWillBeSent") network.push(msg.params.request.url);
    if(msg.method==="Runtime.exceptionThrown") exceptions.push(msg.params.exceptionDetails);
    if(pending.has(msg.id)){const item=pending.get(msg.id);pending.delete(msg.id);clearTimeout(item.timer);if(msg.error)item.no(Error(msg.error.message));else item.yes(msg.result);}
  });
  const send=(method,params={})=>new Promise((yes,no)=>{const id=++next;const timer=setTimeout(()=>{pending.delete(id);no(Error(`Timeout ${method}`));},20000);pending.set(id,{yes,no,timer});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await send("Emulation.setTimezoneOverride",{timezoneId:"Europe/London"});
  await send("Emulation.setLocaleOverride",{locale:"en-GB"});
  const version=await send("Browser.getVersion");
  const settle=`(async()=>{await document.fonts.ready;await Promise.all([400,500,600,700].map(w=>document.fonts.load(w+' 16px Inter')));await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return true})()`;
  const loaded=async(operation)=>{
    const before=loads;await operation();
    for(let i=0;i<400&&loads===before;i++)await new Promise(r=>setTimeout(r,50));
    assert.ok(loads>before,'Document load event missing');
    await evaluate(settle);
  };
  const navigate=async path=>loaded(()=>send("Page.navigate",{url:origin+path}));
  const capture=async(name,path,width,height)=>{
    await send("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:false,screenWidth:width,screenHeight:height});
    await navigate(path);
    const state=await evaluate(`(()=>{
      const box=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
      const style=e=>{const c=getComputedStyle(e);return Object.fromEntries(['backgroundColor','backgroundImage','color','fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','fontFeatureSettings','fontVariationSettings','textShadow','boxShadow','filter','opacity','overflow','clipPath','borderWidth','borderColor','borderRadius','transform','fill','stroke','strokeWidth','strokeLinecap','strokeLinejoin'].map(k=>[k,c[k]]))};
      const attrs=e=>Object.fromEntries([...e.attributes].map(a=>[a.name,a.value]));
      return {rootStyle:style(document.querySelector('.console-frame')),headings:[...document.querySelectorAll('h1,h2,h3')].map(e=>({text:e.textContent,rect:box(e),style:style(e)})),chartElements:[...document.querySelectorAll('.markets-chart,.markets-chart *,.markets-vector,.markets-vector *')].map(e=>({tag:e.tagName,attributes:attrs(e),rect:box(e),style:style(e)})),url:location.href,viewport:[innerWidth,innerHeight],documentExtent:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],consoleRect:box(document.querySelector('.console-frame')),devicePixelRatio,zoom:visualViewport.scale,locale:navigator.language,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,fontStatus:document.fonts.status,weights:[400,500,600,700].map(w=>({weight:w,loaded:document.fonts.check(w+' 16px Inter')})),images:[...document.images].map(i=>({src:i.getAttribute('src'),complete:i.complete&&i.naturalWidth>0})),text:document.querySelector('.console-frame').innerText,nodes:[...document.querySelectorAll('[data-figma-node]')].map(e=>({id:e.dataset.figmaNode,rect:box(e),tag:e.tagName,text:e.textContent,style:style(e),attributes:attrs(e),color:getComputedStyle(e).color,font:getComputedStyle(e).font,overflow:getComputedStyle(e).overflow})),paths:[...document.querySelectorAll('.markets-vector path')].map(p=>p.getAttribute('d')),primitives:[...document.querySelectorAll('[data-primitive]')].map(e=>({name:e.dataset.primitive,x:e.getAttribute('x'),y:e.getAttribute('y'),width:e.getAttribute('width'),height:e.getAttribute('height')})),links:[...document.querySelectorAll('nav a')].map(a=>({href:a.getAttribute('href'),current:a.getAttribute('aria-current'),text:a.textContent}))};
    })()`);
    state.browser=version;state.os={platform:platform(),release:release()};state.colorProfile="srgb";
    if(path.startsWith('/markets')) { assert.equal(state.rootStyle.backgroundColor,'rgb(6, 16, 29)'); assert.equal(state.rootStyle.backgroundImage,'none'); assert.equal(state.rootStyle.borderWidth,'0px'); assert.ok(state.headings.some(h=>h.text==='Markets')); }
    assert.equal(state.devicePixelRatio,1);assert.equal(state.zoom,1);assert.ok(state.weights.every(w=>w.loaded));assert.ok(state.images.every(i=>i.complete));
    assert.equal(state.links.length,12);assert.equal(state.links.filter(l=>l.current==="page").length,1);
    assert.deepEqual(state.documentExtent,[width,height]);
    const shot=await send("Page.captureScreenshot",{format:"png",fromSurface:true});
    await writeFile(join(output,name+".png"),Buffer.from(shot.data,"base64"));
    await writeFile(join(output,name+".json"),JSON.stringify(state,null,2));
    return state;
  };
  const native=await capture("markets-native","/markets",1536,1024);
  assert.deepEqual(native.consoleRect,[0,0,1536,1024]);
  const fit=await capture("markets-fit","/markets?fit=window",1365,768);
  assert.deepEqual(fit.consoleRect,[106.5,0,1152,768]);
  await capture("markets-width-fit","/markets?fit=window",1280,900);
  await capture("dashboard-after-native","/dashboard",1536,1024);
  await capture("dashboard-after-fit","/dashboard?fit=window",1365,768);
  const navigation=await evaluate(`(async()=>{const result=[];for(const href of [...document.querySelectorAll('nav a')].map(a=>a.getAttribute('href'))){document.querySelector('nav a[href="'+href+'"]').click();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));result.push({expected:href,actual:location.pathname,active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href'),query:location.search,fit:document.querySelector('.console-viewport').classList.contains('fit-to-window')});} const move=direction=>new Promise(r=>{addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(location.pathname))),{once:true});history[direction]()});return {routes:result,back:await move('back'),forward:await move('forward')};})()`);
  assert.ok(navigation.routes.every(r=>r.expected===r.actual&&r.actual===r.active&&r.fit&&r.query==="?fit=window"));
  assert.equal(navigation.back,"/audit-log");assert.equal(navigation.forward,"/settings");
  const direct=[];
  for(const route of native.links){await navigate(route.href);direct.push(await evaluate(`({path:location.pathname,active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')})`));}
  assert.ok(direct.every(r=>r.path===r.active));
  const modeChecks=[];
  const modeState=()=>evaluate(`({path:location.pathname,query:location.search,fit:document.querySelector('.console-viewport').classList.contains('fit-to-window'),active:document.querySelector('nav a[aria-current="page"]')?.getAttribute('href')})`);
  const checkMode=async(label,path,fitted)=>{const state=await modeState();modeChecks.push({label,...state});await writeFile(join(output,"mode-checks-progress.json"),JSON.stringify(modeChecks,null,2));assert.equal(state.path,path);assert.equal(state.active,path);assert.equal(state.fit,fitted);assert.equal(state.query,fitted?'?fit=window':'');};
  const move=async direction=>{await evaluate(`new Promise(resolve=>{addEventListener('popstate',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))),{once:true});history.${direction}()})`);await evaluate(settle);};
  for(const fitted of [false,true]) {
    const query=fitted?'?fit=window':'';
    for(const route of native.links) {
      await navigate(route.href+query);await checkMode('direct',route.href,fitted);
      await evaluate(`document.querySelector('nav a[href="/markets"]').click()`);await evaluate(settle);await checkMode('navigation','/markets',fitted);
    }
    await loaded(()=>send('Page.reload'));await checkMode('reload','/markets',fitted);
    await evaluate(`document.querySelector('nav a[href="/dashboard"]').click()`);await evaluate(settle);await checkMode('navigation','/dashboard',fitted);
    await move('back');await checkMode('back','/markets',fitted);
    await move('forward');await checkMode('forward','/dashboard',fitted);
  }
  // Cross-mode history must restore the URL mode, not retain the previous React state.
  await navigate('/markets');await navigate('/dashboard?fit=window');
  const historyEntries=await send('Page.getNavigationHistory');
  const restore=async entry=>{
    await send('Page.navigateToHistoryEntry',{entryId:entry.id});
    let ready=false;
    for(let i=0;i<400;i++) {
      try { ready=await evaluate(`location.href===${JSON.stringify(entry.url)} && document.readyState==='complete' && !!document.querySelector('.console-frame')`); } catch { ready=false; }
      if(ready)break;await new Promise(r=>setTimeout(r,50));
    }
    assert.ok(ready,'History document not restored');await evaluate(settle);
  };
  await restore(historyEntries.entries[historyEntries.currentIndex-1]);await checkMode('cross-mode back','/markets',false);
  await restore(historyEntries.entries[historyEntries.currentIndex]);await checkMode('cross-mode forward','/dashboard',true);
  await navigate("/markets");
  const key=async(key,code,virtual,modifiers=0)=>{await send("Input.dispatchKeyEvent",{type:"keyDown",key,code,windowsVirtualKeyCode:virtual,modifiers});await send("Input.dispatchKeyEvent",{type:"keyUp",key,code,windowsVirtualKeyCode:virtual,modifiers});};
  await key("Tab","Tab",9);const first=await evaluate("document.activeElement.getAttribute('href')");
  await key("Tab","Tab",9);const second=await evaluate("document.activeElement.getAttribute('href')");
  await key("Tab","Tab",9,8);const reverse=await evaluate("document.activeElement.getAttribute('href')");
  await key("Enter","Enter",13);await evaluate(settle);const enter=await evaluate("location.pathname");
  assert.equal(first,"/dashboard");assert.equal(second,"/markets");assert.equal(reverse,"/dashboard");assert.equal(enter,"/dashboard");
  await navigate("/markets?fit=window");
  await send("Emulation.setDeviceMetricsOverride",{width:1280,height:900,deviceScaleFactor:1,mobile:false});await evaluate(settle);
  const resized=await evaluate("(()=>{const r=document.querySelector('.console-frame').getBoundingClientRect();return [r.x,r.y,r.width,r.height]})()");
  assert.ok(Math.abs(resized[2]-1280)<.01&&Math.abs(resized[3]-1024*1280/1536)<.01);
  const report={modeChecks,navigation,direct,keyboard:{first,second,reverse,enter},resized,exceptions,network:[...new Set(network)],fitQueryMismatch:navigation.routes.some(r=>r.fit&&r.query!=="?fit=window")};
  await writeFile(join(output,"browser-checks.json"),JSON.stringify(report,null,2));
  assert.equal(report.fitQueryMismatch,false);
  assert.equal(exceptions.length,0);
  assert.ok(report.network.every(url=>url.startsWith(origin)||url==="about:blank"||url.startsWith("data:")),"External request detected");
  console.log(JSON.stringify({captures:5,routes:12,directLoads:12,keyboard:"PASS",resize:"PASS",fitQueryMismatch:report.fitQueryMismatch}));
} finally {
  socket?.close();child.kill();
}
