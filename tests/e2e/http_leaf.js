'use strict';
/*
 * Browser E2E for the no-WebRTC HTTP leaf.
 *
 * Run tests/e2e/server.py first, then:
 *   node tests/e2e/http_leaf.js
 *
 * The page is loaded with RTCPeerConnection removed before any application
 * script runs.  The client must not pretend to be a DataChannel child; it
 * should report role=http, fetch /source, and play via MediaSource.
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
const PORT = Number(process.env.E2E_CDP_PORT || 9560);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

if (!CHROME) throw new Error('Chrome not found; set CHROME_BIN');

function openSocket(url) {
  const socket = new WebSocket(url);
  return new Promise((resolve, reject) => {
    socket.addEventListener('open', () => resolve(socket), { once: true });
    socket.addEventListener(
      'error',
      (event) => reject(event.error || new Error('websocket error')),
      { once: true },
    );
  });
}

async function connect() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'radio-http-leaf-'));
  const child = spawn(
    CHROME,
    [
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      '--headless=new',
      '--no-sandbox',
      '--disable-crash-reporter',
      '--disable-breakpad',
      '--autoplay-policy=no-user-gesture-required',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      '--no-first-run',
      '--no-default-browser-check',
      'about:blank',
    ],
    { stdio: 'ignore' },
  );
  let target = null;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (response.ok) break;
    } catch (_) {
      /* still starting */
    }
    await sleep(200);
  }
  const created = await fetch(
    `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent('about:blank')}`,
    { method: 'PUT' },
  );
  target = await created.json();
  const socket = await openSocket(target.webSocketDebuggerUrl);
  let sequence = 0;
  const pending = new Map();
  const logs = [];
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
      return;
    }
    if (message.method === 'Runtime.consoleAPICalled') {
      logs.push(
        message.params.args
          .map((item) => item.value ?? item.description ?? '')
          .join(' '),
      );
    }
    if (message.method === 'Runtime.exceptionThrown') {
      logs.push(`EXC ${JSON.stringify(message.params.exceptionDetails)}`);
    }
  });
  const call = (method, params = {}) => {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  };
  const evaluate = async (expression) => {
    const result = await call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(JSON.stringify(result.exceptionDetails));
    }
    return result.result.value;
  };
  const wait = async (expression, label, timeout = 30000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await sleep(200);
    }
    throw new Error(`timeout ${label}\n${logs.slice(-20).join('\n')}`);
  };
  return {
    child,
    socket,
    call,
    evaluate,
    wait,
    logs,
    close: () => {
      try {
        child.kill('SIGKILL');
      } catch (_) {
        /* ignore */
      }
    },
  };
}

async function main() {
  const browser = await connect();
  try {
    // The target is about:blank.  Remove WebRTC before navigating to the app.
    await browser.call('Page.enable');
    await browser.call('Page.addScriptToEvaluateOnNewDocument', {
      source: 'delete window.RTCPeerConnection;',
    });
    await browser.call('Page.navigate', { url: `${APP}/` });
    await browser.wait(
      'document.readyState === "complete" && !!window.RadioMesh',
      'app ready',
    );
    await browser.evaluate('document.getElementById("playBtn").click()');
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      const stats = await browser.evaluate('window.RadioMesh.stats()');
      const audio = await browser.evaluate(
        '({paused:document.getElementById("audio").paused, ready:document.getElementById("audio").readyState, currentTime:document.getElementById("audio").currentTime, currentSrc:document.getElementById("audio").currentSrc, mode:document.getElementById("audioMode").textContent})',
      );
      const usingHttpLeaf = stats.role === 'http' && stats.chunks > 2;
      const directFallback = Boolean(stats.fallbackReason);
      if (
        (usingHttpLeaf || directFallback) &&
        !audio.paused &&
        (audio.ready >= 2 || audio.currentTime > 0.2)
      ) {
        if (directFallback && !String(audio.currentSrc || '').includes('/native/')) {
          continue;
        }
        console.log(
          JSON.stringify(
            {
              role: stats.role || 'direct',
              fallbackReason: stats.fallbackReason,
              chunks: stats.chunks,
              mode: audio.mode,
              readyState: audio.ready,
              currentTime: audio.currentTime,
              currentSrc: audio.currentSrc,
              source: stats.source,
            },
            null,
            2,
          ),
        );
        console.log('HTTP LEAF PASS');
        return;
      }
      await sleep(250);
    }
    throw new Error('http leaf timeout');
  } finally {
    browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
