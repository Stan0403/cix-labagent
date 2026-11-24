// Optional developer test. Use an installed Playwright module; no browser credentials are needed.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const readline = require("node:readline");
const { chromium } = require(process.env.LABAGENT_PLAYWRIGHT_MODULE || "playwright");
async function main() {
  const target = process.env.LABAGENT_TEST_URL || "http://127.0.0.1:8000";
  const downloadsPath=path.resolve(".tmp/browser-downloads");fs.mkdirSync(downloadsPath,{recursive:true});
  const options = {headless:true,downloadsPath};
  if (process.env.LABAGENT_CHROME_PATH) options.executablePath = process.env.LABAGENT_CHROME_PATH;
  const browser = await chromium.launch(options);
  const page = await browser.newPage({viewport:{width:1600,height:1100}});
  let bridge=null;
  if(process.env.LABAGENT_TEST_BRIDGE==="1") {
    const python=process.env.LABAGENT_TEST_PYTHON||path.resolve(".venv",process.platform==="win32"?"Scripts/python.exe":"bin/python");
    bridge=spawn(python,["-u","scripts/browser_bridge.py"],{windowsHide:true,env:{...process.env,PYTHONUTF8:"1"},stdio:["pipe","pipe","inherit"]});
    const pending=new Map();let sequence=0;
    readline.createInterface({input:bridge.stdout}).on("line",line=>{const response=JSON.parse(line), item=pending.get(response.id);if(item){pending.delete(response.id);response.error?item.reject(new Error(response.error)):item.resolve(response);}});
    bridge.on("exit",code=>{for(const item of pending.values())item.reject(new Error("Test bridge exited: "+code));pending.clear();});
    await page.route("**/*",async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.origin!==target){await route.abort();return;}
      const headers={...request.headers()};delete headers.host;delete headers.connection;delete headers["content-length"];
      const id=++sequence;
      const responsePromise=new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));
      bridge.stdin.write(JSON.stringify({id,method:request.method(),path:url.pathname+url.search,headers,body:(request.postDataBuffer()||Buffer.alloc(0)).toString("base64")})+"\n");
      const response=await responsePromise;
      await route.fulfill({status:response.status,headers:response.headers,body:Buffer.from(response.body,"base64")});
    });
    console.log("Browser test transport: stdio -> real FastAPI TestClient; endpoints and SQLite are not mocked");
  }
  const errors=[];
  page.on("pageerror", error=>errors.push(error.message));
  const directory=path.resolve("test-results"); fs.mkdirSync(directory,{recursive:true});
  try {
    await page.goto(target);
    await page.getByText("服务在线",{exact:true}).waitFor();
    await page.getByRole("button",{name:"＋ 新建真实实验",exact:true}).click();
    await page.locator("#active").waitFor({state:"visible"});
    await page.getByRole("button",{name:"验证并进入下一步 →",exact:true}).click();
    await page.locator("#toast").filter({hasText:"请阅读并确认"}).waitFor();
    assert.match(await page.locator("#step-count").innerText(), /1 \/ 9/);
    await page.locator("#human-confirm").check();
    await page.getByRole("button",{name:"已读 SOP",exact:true}).click();
    await page.locator("#toast").filter({hasText:"已记录人工确认"}).waitFor();
    await page.getByRole("button",{name:"验证并进入下一步 →",exact:true}).click();
    await page.locator("#step-count").filter({hasText:"2 / 9"}).waitFor();
    await page.getByRole("button",{name:"当前进度",exact:true}).click();
    await page.locator(".message.assistant").filter({hasText:"断电"}).waitFor();
    assert.match(await page.locator("#step-count").innerText(), /2 \/ 9/);
    await page.reload();
    await page.locator("#step-count").filter({hasText:"2 / 9"}).waitFor();
    await page.screenshot({path:path.join(directory,"browser-live.png"),fullPage:true});
    await page.locator("#fixture").selectOption("happy_path");
    await page.getByRole("button",{name:"运行测试剧本",exact:true}).click();
    await page.locator("#run-status").filter({hasText:"已完成"}).waitFor();
    assert.match(await page.locator("#latest-source").innerText(), /测试回放/);
    assert.match(await page.locator("#latest-reading").innerText(), /1.25/);
    const downloadPromise=page.waitForEvent("download");
    await page.getByRole("button",{name:"报告 .md",exact:true}).click();
    const download=await downloadPromise;
    await download.saveAs(path.join(directory,"browser-report.md"));
    assert.match(fs.readFileSync(path.join(directory,"browser-report.md"),"utf8"),/非真实测量/);
    await page.screenshot({path:path.join(directory,"browser-replay.png"),fullPage:true});
    await page.getByRole("button",{name:"重新测量 · 保留历史记录",exact:true}).click();
    await page.locator("#step-count").filter({hasText:"7 / 9"}).waitFor();
    assert.match(await page.locator("#measurement-history").innerText(), /已被替代/);
    await page.locator("#fixture").selectOption("missing_equipment");
    await page.getByRole("button",{name:"运行测试剧本",exact:true}).click();
    await page.locator("#blockers").filter({hasText:"万用表"}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(directory,"browser-mobile.png"),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,"Mobile layout overflows");
    assert.deepEqual(errors,[],"Browser JavaScript errors");
    console.log("Browser smoke passed: real UI create/block/confirm/chat/persist/replay/export/remeasure/mobile; no JS errors");
  } finally { await browser.close(); if(bridge)bridge.stdin.end(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
