'use strict';
/*
 * Browser E2E for the mesh path.  Requires Node 22+ (global WebSocket/fetch)
 * and Chrome.  It starts no server itself; run tests/e2e/server.py first.
 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const APP = process.env.E2E_APP || 'http://127.0.0.1:8795';
const CHROME = process.env.CHROME_BIN || [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].find((item) => fs.existsSync(item));
const BASE_CDP = Number(process.env.E2E_CDP_PORT || 9530);
const COUNT = Number(process.env.E2E_PAGES || 6);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

if (!CHROME) throw new Error('Chrome not found; set CHROME_BIN');

function openSocket(url) {
  const ws = new WebSocket(url);
  return new Promise((resolve, reject) => {
    ws.addEventListener('open', () => resolve(ws), { once: true });
    ws.addEventListener('error', (event) => reject(event.error || new Error('websocket error')), { once: true });
  });
}

async function newBrowser(index) {
  const port = BASE_CDP + index;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), `radio-e2e-${index}-`));
  const child = spawn(CHROME, [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--headless=new',
    '--no-sandbox',
    '--disable-crash-reporter',
    '--disable-breakpad',
    '--autoplay-policy=no-user-gesture-required',
    '--disable-features=WebRtcHideLocalIpsWithMdns,CalculateNativeWinOcclusion',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ], { stdio: 'ignore' });

  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) break;
    } catch (_) {
      // Chrome is still starting.
    }
    await sleep(200);
  }

  const created = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent(`${APP}/`)}`,
    { method: 'PUT' },
  );
  const target = await created.json();
  const ws = await openSocket(target.webSocketDebuggerUrl);
  let sequence = 0;
  const pending = new Map();
  const logs = [];
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
      return;
    }
    if (message.method === 'Runtime.consoleAPICalled') {
      logs.push(message.params.args.map((item) => item.value ?? item.description ?? '').join(' '));
    }
    if (message.method === 'Runtime.exceptionThrown') {
      logs.push(`EXC ${JSON.stringify(message.params.exceptionDetails)}`);
    }
  });
  const call = (method, params = {}) => {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  };
  const evaluate = async (expression) => {
    const result = await call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call('Runtime.enable');
  const wait = async (expression, label, timeout = 30000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await sleep(200);
    }
    throw new Error(`timeout ${label}\n${logs.slice(-20).join('\n')}`);
  };
  return {
    index,
    child,
    target,
    evaluate,
    wait,
    logs,
    close: () => {
      try { child.kill('SIGKILL'); } catch (_) { /* ignore */ }
      try { ws.close(); } catch (_) { /* ignore */ }
    },
  };
}

async function main() {
  const browsers = [];
  try {
    // Separate browsers make every listener an active tab; one Chrome with
    // six background tabs can throttle MSE and invalidate a sync test.
    // Launch sequentially: six parallel Chrome profiles can starve a small
    // CI/dev machine and make the first page miss its readiness window.
    for (let index = 0; index < COUNT; index += 1) {
      browsers.push(await newBrowser(index));
    }

    for (const browser of browsers) {
      await browser.wait(
        'document.readyState==="complete" && !!window.RadioMesh',
        `ready ${browser.index}`,
      );
      await browser.evaluate('document.getElementById("playBtn").click()');
    }
    for (const browser of browsers) {
      await browser.wait('window.RadioMesh.stats().chunks > 2', `chunks ${browser.index}`);
    }
    await sleep(3500);

    const topology = await (await fetch(`${APP}/mesh.json`)).json();
    if (topology.nodes.length !== COUNT) throw new Error(`topology ${JSON.stringify(topology)}`);
    if (topology.seeds.length !== 2) throw new Error('expected two seeds');
    const children = topology.nodes.filter((node) => node.role === 'child');
    if (children.some((node) => node.parents.length !== 2)) {
      throw new Error(`not dual-parent: ${JSON.stringify(children)}`);
    }

    const before = [];
    for (const browser of browsers) before.push(await browser.evaluate('window.RadioMesh.stats()'));
    if (before.some((item) => item.bad || item.gaps)) {
      throw new Error(`bad/gaps before: ${JSON.stringify(before)}`);
    }
    if (new Set(before.map((item) => item.streamId)).size !== 1) {
      throw new Error('different streams');
    }
    const beforeSpread = Math.max(...before.map((item) => item.positionMs))
      - Math.min(...before.map((item) => item.positionMs));
    if (beforeSpread > 1600) throw new Error(`sync before ${beforeSpread}`);

    const failedSeed = topology.seeds[0];
    let failedIndex = -1;
    for (const browser of browsers) {
      const node = await browser.evaluate('window.RadioMesh.stats().nodeId');
      if (node === failedSeed) {
        failedIndex = browser.index;
        break;
      }
    }
    if (failedIndex < 0) throw new Error('seed page not found');
    browsers[failedIndex].close();

    let afterTopology = null;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      await sleep(250);
      afterTopology = await (await fetch(`${APP}/mesh.json`)).json();
      if (afterTopology.nodes.length === COUNT - 1 && afterTopology.seeds.length === 2) break;
    }
    if (!afterTopology || afterTopology.nodes.length !== COUNT - 1) {
      throw new Error(`failover topology: ${JSON.stringify(afterTopology)}`);
    }
    if (afterTopology.seeds.includes(failedSeed)) throw new Error('failed seed still seed');
    await sleep(4000);

    const after = [];
    for (const browser of browsers) {
      if (browser.index === failedIndex) continue;
      const stats = await browser.evaluate('window.RadioMesh.stats()');
      const paused = await browser.evaluate('document.getElementById("audio").paused');
      after.push({ node: stats.nodeId, role: stats.role, pos: stats.positionMs, dup: stats.dup,
        bad: stats.bad, gaps: stats.gaps, paused });
    }
    if (after.some((item) => item.bad || item.gaps || item.paused)) {
      throw new Error(`after failover: ${JSON.stringify(after)}`);
    }
    const spread = Math.max(...after.map((item) => item.pos))
      - Math.min(...after.map((item) => item.pos));
    if (spread > 1800) throw new Error(`sync after ${spread}`);

    console.log(JSON.stringify({
      pages: COUNT,
      topology: { nodes: topology.nodes.length, seeds: topology.seeds },
      before: before.map((item) => ({ node: item.nodeId, role: item.role,
        pos: item.positionMs, dup: item.dup, parents: item.parents.length })),
      beforeSpread,
      afterTopology: { nodes: afterTopology.nodes.length, seeds: afterTopology.seeds },
      after,
      afterSpread: spread,
    }, null, 2));
    console.log('E2E PASS');
  } finally {
    for (const browser of browsers) browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
