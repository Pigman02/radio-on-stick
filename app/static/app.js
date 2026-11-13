"use strict";

const $ = (id) => document.getElementById(id);

const audio = $("audio");
const bgLayerA = $("bgLayerA");
const bgLayerB = $("bgLayerB");
const stationName = $("stationName");
const stationSubtitle = $("stationSubtitle");
const noticeBar = $("noticeBar");
const syncStatus = $("syncStatus");
const nicknameBtn = $("nicknameBtn");
const adminBtn = $("adminBtn");
const livePill = $("livePill");
const title = $("title");
const artist = $("artist");
const album = $("album");
const positionEl = $("position");
const durationEl = $("duration");
const progressTrack = $("progressTrack");
const progressFill = $("progressFill");
const playBtn = $("playBtn");
const muteBtn = $("muteBtn");
const volumeEl = $("volume");
const audioMode = $("audioMode");
const audioLink = $("audioLink");
const sourceBadge = $("sourceBadge");
const cover = $("cover");
const lyricsScroll = $("lyricsScroll");
const lyricsEl = $("lyrics");
const translationToggle = $("translationToggle");
const queueList = $("queueList");
const queueCount = $("queueCount");
const searchForm = $("searchForm");
const searchInput = $("searchInput");
const searchBtn = $("searchBtn");
const searchStatus = $("searchStatus");
const searchResults = $("searchResults");
const selectBar = $("selectBar");
const selectedTrack = $("selectedTrack");
const requestBtn = $("requestBtn");
const messageForm = $("messageForm");
const messageInput = $("messageInput");
const messageBtn = $("messageBtn");
const messageStatus = $("messageStatus");
const messageIdentity = $("messageIdentity");
const messageList = $("messageList");

const nicknameModal = $("nicknameModal");
const nicknameInput = $("nicknameInput");
const nicknameSave = $("nicknameSave");
const nicknameStatus = $("nicknameStatus");

const adminModal = $("adminModal");
const adminClose = $("adminClose");
const adminLogin = $("adminLogin");
const adminToken = $("adminToken");
const adminLoginBtn = $("adminLoginBtn");
const adminLoginStatus = $("adminLoginStatus");
const adminPanel = $("adminPanel");
const adminNow = $("adminNow");
const adminQueue = $("adminQueue");
const adminMessages = $("adminMessages");
const adminSkip = $("adminSkip");
const adminPause = $("adminPause");
const adminResume = $("adminResume");
const adminClearQueue = $("adminClearQueue");
const adminSearchForm = $("adminSearchForm");
const adminSearchInput = $("adminSearchInput");
const adminSearchStatus = $("adminSearchStatus");
const adminSearchResults = $("adminSearchResults");
const setStationName = $("setStationName");
const setSubtitle = $("setSubtitle");
const setPageTitle = $("setPageTitle");
const setNotice = $("setNotice");
const setBgInterval = $("setBgInterval");
const saveSettings = $("saveSettings");
const settingsStatus = $("settingsStatus");
const bgFile = $("bgFile");
const bgUpload = $("bgUpload");
const bgStatus = $("bgStatus");
const bgGallery = $("bgGallery");
const adminLogout = $("adminLogout");

const state = {
  now: null,
  seq: null,
  audioRev: null,
  audioKey: null,
  sources: [],
  sourceIndex: 0,
  clockOffset: 0,
  userWantsPlay: false,
  pendingSeekMs: 0,
  lastLoadedSeq: null,
  lyricsTrackId: null,
  lyricsLines: [],
  lyricEls: [],
  lastLyricIndex: -1,
  lastPositionSec: -1,
  lastReportKey: null,
  stalledTimer: null,
  pollTimer: null,
  connection: "connecting",
  audioState: "idle",
  serverPaused: false,
  sidebarLoading: false,
  showTranslation: true,
  nickname: "",
  uiConfig: null,
  backgrounds: [],
  bgIntervalSec: 300,
  bgKey: "",
  bgUrl: "",
  bgActive: 0,
  bgTimer: null,
  searchResults: [],
  selectedCandidate: null,
  searchBusy: false,
  requestBusy: false,
  requestTs: Date.now() - 5000,
  messageBusy: false,
  messageTs: Date.now() - 5000,
  adminToken: "",
  adminBusy: false,
  adminTab: "control",
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function storageSet(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (_) {
    /* ignore */
  }
}

function sessionGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

function sessionSet(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch (_) {
    /* ignore */
  }
}

async function fetchJSON(url, options = {}) {
  const { timeout = 15000, ...init } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { cache: "no-store", ...init, signal: controller.signal });
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = null;
    }
    if (!response.ok) {
      const error = new Error((data && data.message) || `HTTP ${response.status}`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function errorText(error, fallback = "操作失败") {
  return (error && error.data && error.data.message) || (error && error.message) || fallback;
}

function setPill(kind, text) {
  syncStatus.dataset.state = kind;
  syncStatus.textContent = text;
}

function refreshStatus() {
  if (state.connection === "offline") {
    setPill("offline", navigator.onLine ? "连接中断" : "离线");
    return;
  }
  if (state.serverPaused) {
    setPill("buffering", "电台已暂停");
    return;
  }
  if (state.audioState === "error") {
    setPill("offline", "音频不可用");
    return;
  }
  if (!state.userWantsPlay && audio.paused) {
    setPill(state.now && state.now.track ? "live" : "connecting", state.now && state.now.track ? "已同步" : "待开播");
    return;
  }
  if (state.audioState === "buffering") {
    setPill("buffering", "缓冲中");
    return;
  }
  if (!state.now || !state.now.track) {
    setPill("connecting", "待开播");
    return;
  }
  setPill("live", "已同步");
}

function sourceLabel(source) {
  if (source === "netease") return "网易云音乐";
  if (source === "qq") return "QQ音乐";
  return source || "未知来源";
}

function fmtTime(ms, estimated = false) {
  if (!Number.isFinite(ms) || ms < 0) return "--:--";
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const prefix = estimated ? "~" : "";
  if (hours > 0) {
    return `${prefix}${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${prefix}${minutes}:${String(seconds).padStart(2, "0")}`;
}

function fmtRelative(timestamp) {
  if (!timestamp) return "";
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  const date = new Date(timestamp);
  return `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function currentPosition(now = state.now) {
  if (!now || !now.track) return 0;
  if (now.paused) return Math.max(0, Number(now.position_ms) || 0);
  const live = Date.now() + state.clockOffset - Number(now.started_at_ms || 0);
  return clamp(live, 0, Number(now.duration_ms) || Number.MAX_SAFE_INTEGER);
}

/* ---------- config, theme, background ---------- */

function applyConfig(config) {
  state.uiConfig = config;
  stationName.textContent = config.station_name || "我的电台";
  stationSubtitle.textContent = config.subtitle || "";
  document.title = config.page_title || config.station_name || "我的电台";
  const notice = String(config.notice || "").trim();
  if (notice) {
    noticeBar.textContent = notice;
    noticeBar.classList.remove("hidden");
  } else {
    noticeBar.textContent = "";
    noticeBar.classList.add("hidden");
  }

  state.backgrounds = Array.isArray(config.backgrounds) ? config.backgrounds : [];
  state.bgIntervalSec = clamp(Number(config.background_interval_sec) || 300, 30, 86400);

  if (!state.backgrounds.length) {
    clearBackgroundLayers();
  } else if (!state.bgUrl || !state.backgrounds.some((item) => item.url === state.bgUrl)) {
    applyRandomBackground();
  }
  setupBackgroundTimer();
}

async function loadConfig() {
  try {
    applyConfig(await fetchJSON("/api/config"));
  } catch (_) {
    /* config is best-effort */
  }
}

function applyRandomBackground(force = false) {
  if (!state.backgrounds.length) return;
  let index = Math.floor(Math.random() * state.backgrounds.length);
  if (state.backgrounds.length > 1 && state.backgrounds[index].url === state.bgUrl) {
    index = (index + 1) % state.backgrounds.length;
  }
  if (!force && state.backgrounds[index].url === state.bgUrl) return;
  applyBackground(state.backgrounds[index]);
}

function applyBackground(item) {
  if (!item || !item.url) return;
  const layer = state.bgActive === 0 ? bgLayerB : bgLayerA;
  const other = state.bgActive === 0 ? bgLayerA : bgLayerB;
  layer.style.backgroundImage = `url("${item.url.replace(/"/g, "%22")}")`;
  layer.classList.add("active");
  other.classList.remove("active");
  state.bgActive = state.bgActive === 0 ? 1 : 0;
  state.bgUrl = item.url;
}

function clearBackgroundLayers() {
  bgLayerA.classList.remove("active");
  bgLayerB.classList.remove("active");
  bgLayerA.style.backgroundImage = "";
  bgLayerB.style.backgroundImage = "";
  state.bgUrl = "";
}

function setupBackgroundTimer() {
  const key = `${state.bgIntervalSec}:${state.backgrounds.map((item) => item.url).join("|")}`;
  if (key === state.bgKey && state.bgTimer) return;
  state.bgKey = key;
  clearInterval(state.bgTimer);
  state.bgTimer = null;
  if (state.backgrounds.length > 1) {
    state.bgTimer = setInterval(() => applyRandomBackground(), state.bgIntervalSec * 1000);
  }
}

/* ---------- nickname ---------- */

function loadNickname() {
  state.nickname = (storageGet("radio.nickname") || "").trim();
  updateNicknameUI();
}

function updateNicknameUI() {
  if (state.nickname) {
    nicknameBtn.textContent = `昵称：${state.nickname}`;
    messageIdentity.textContent = `以 ${state.nickname} 留言`;
  } else {
    nicknameBtn.textContent = "设置昵称";
    messageIdentity.textContent = "未设置昵称";
  }
}

function openNicknameModal() {
  nicknameInput.value = state.nickname;
  nicknameStatus.textContent = "";
  showModal(nicknameModal);
  setTimeout(() => nicknameInput.focus(), 50);
}

function saveNickname() {
  const value = nicknameInput.value.trim().slice(0, 16);
  if (!value) {
    nicknameStatus.textContent = "昵称不能为空";
    nicknameStatus.dataset.tone = "error";
    return;
  }
  state.nickname = value;
  storageSet("radio.nickname", value);
  updateNicknameUI();
  hideModal(nicknameModal);
  nicknameStatus.textContent = "";
}

/* ---------- player ---------- */

function updatePlayButton() {
  const hasTrack = Boolean(state.now && state.now.track);
  const playing = hasTrack && state.userWantsPlay && !audio.paused && !state.serverPaused;
  playBtn.disabled = !hasTrack || state.serverPaused;
  playBtn.setAttribute("aria-label", playing ? "暂停" : "播放");
  playBtn.querySelector(".icon-play").classList.toggle("hidden", playing);
  playBtn.querySelector(".icon-pause").classList.toggle("hidden", !playing);
}

function setLivePill() {
  if (!state.now || !state.now.track) {
    livePill.dataset.state = "idle";
    livePill.textContent = "待机";
  } else if (state.serverPaused) {
    livePill.dataset.state = "paused";
    livePill.textContent = "已暂停";
  } else {
    livePill.dataset.state = "live";
    livePill.textContent = "播放中";
  }
}

function renderTrack(now) {
  const track = now.track;
  title.textContent = track.title || "未知曲目";
  artist.textContent = track.artist || "--";
  album.textContent = track.album || "--";
  durationEl.textContent = fmtTime(now.duration_ms, track.duration_estimated);
  sourceBadge.textContent = sourceLabel(track.source);
  const newCover = track.cover_url || "/placeholder.svg";
  if (cover.getAttribute("src") !== newCover) {
    cover.src = newCover;
  }
  if (now.audio && now.audio.direct) {
    audioLink.href = now.audio.direct;
    audioLink.classList.remove("hidden");
  } else {
    audioLink.classList.add("hidden");
  }
  setLivePill();
}

function clearTrack() {
  title.textContent = "等待开播";
  artist.textContent = "--";
  album.textContent = "--";
  positionEl.textContent = "00:00";
  durationEl.textContent = "--:--";
  progressFill.style.width = "0%";
  progressTrack.setAttribute("aria-valuenow", "0");
  sourceBadge.textContent = "待机";
  livePill.dataset.state = "idle";
  livePill.textContent = "待机";
  audioMode.textContent = "等待音频";
  audioLink.classList.add("hidden");
  state.audioState = "idle";
  state.lastPositionSec = -1;
  setLyricsEmpty("等待开播");
  updatePlayButton();
  refreshStatus();
}

function updateAudioMode() {
  if (!state.sources.length) {
    audioMode.textContent = "无可用音频";
    return;
  }
  if (state.sourceIndex === 0) {
    audioMode.textContent = state.now && state.now.audio && state.now.audio.direct ? "直连" : "备用源";
  } else {
    audioMode.textContent = `备用源 ${state.sourceIndex}`;
  }
}

function loadSource(positionMs) {
  if (!state.sources.length) {
    updateAudioMode();
    return;
  }
  const url = state.sources[state.sourceIndex];
  state.pendingSeekMs = Number.isFinite(positionMs) ? Math.max(0, positionMs) : 0;
  state.audioKey = `${state.seq}:${state.audioRev}:${state.sourceIndex}`;
  state.audioState = state.userWantsPlay ? "buffering" : "idle";
  refreshStatus();
  updateAudioMode();
  audio.src = url;
  audio.load();
}

function attemptPlay() {
  if (!state.userWantsPlay || state.serverPaused) return;
  audio.play().catch((error) => {
    if (error && error.name === "AbortError") return;
    if (error && error.name === "NotAllowedError") {
      state.userWantsPlay = false;
      state.audioState = "idle";
      updatePlayButton();
      refreshStatus();
      return;
    }
    tryNextSource("error");
  });
}

function seekToLive() {
  const live = currentPosition();
  if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
  const target = Math.min(live / 1000, Math.max(0, audio.duration - 0.35));
  if (Math.abs(audio.currentTime - target) > 0.8) {
    try {
      audio.currentTime = target;
    } catch (_) {
      /* ignore */
    }
  }
}

function tryNextSource(reason) {
  if (state.sourceIndex + 1 < state.sources.length) {
    state.sourceIndex += 1;
    state.audioState = "buffering";
    refreshStatus();
    loadSource(currentPosition());
    if (state.userWantsPlay) attemptPlay();
    return;
  }
  const key = `${state.seq}:${state.audioRev}`;
  if (state.lastReportKey !== key) {
    state.lastReportKey = key;
    report(reason === "stalled" ? "stalled" : "error");
  }
  state.audioState = "error";
  refreshStatus();
}

function startStalledTimer() {
  clearTimeout(state.stalledTimer);
  state.stalledTimer = setTimeout(() => {
    if (state.userWantsPlay && !audio.paused && !state.serverPaused) {
      tryNextSource("stalled");
    }
  }, 12_000);
}

function clearStalledTimer() {
  clearTimeout(state.stalledTimer);
  state.stalledTimer = null;
}

function applyNow(now) {
  const previousSeq = state.seq;
  const previousAudioKey = state.audioKey !== null ? `${state.seq}:${state.audioRev}` : null;
  state.now = now;
  state.seq = now.seq;
  state.audioRev = now.audio_rev;
  state.serverPaused = Boolean(now.paused);

  if (!now.track) {
    state.sources = [];
    state.sourceIndex = 0;
    state.audioKey = null;
    clearStalledTimer();
    if (audio.src) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    clearTrack();
    return;
  }

  const isNewTrack = now.seq !== previousSeq;
  const audioKey = `${now.seq}:${now.audio_rev}`;
  const isNewAudio = audioKey !== previousAudioKey;
  const direct = (now.audio && now.audio.direct) || "";
  const alternates = (now.audio && now.audio.alternates) || [];
  const sources = [direct, ...alternates].filter(Boolean);

  renderTrack(now);

  if (isNewTrack) {
    state.sources = sources;
    state.sourceIndex = 0;
    state.lyricsTrackId = now.track.id;
    state.lastReportKey = null;
    state.lastPositionSec = -1;
    clearStalledTimer();
    loadLyrics(now.lyrics_url, now.track.id);
    if (sources.length) {
      loadSource(currentPosition(now));
    } else {
      state.audioState = "error";
    }
  } else if (isNewAudio) {
    state.sources = sources;
    state.sourceIndex = 0;
    clearStalledTimer();
    if (sources.length) {
      loadSource(currentPosition(now));
    } else {
      state.audioState = "error";
    }
  } else if (!audio.src && sources.length) {
    state.sources = sources;
    loadSource(now.position_ms);
  }

  if (state.serverPaused) {
    audio.pause();
    clearStalledTimer();
  } else if (state.userWantsPlay && audio.paused) {
    seekToLive();
    attemptPlay();
  }

  setLivePill();
  updatePlayButton();
  refreshStatus();
}

function applyReportSnapshot(data) {
  if (!data || typeof data !== "object" || !data.track) return;
  if (data.seq !== state.seq || data.audio_rev !== state.audioRev) {
    applyNow(data);
  }
}

async function report(event) {
  if (state.seq === null) return;
  try {
    const data = await fetchJSON("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seq: state.seq, event }),
      timeout: 10_000,
    });
    applyReportSnapshot(data);
  } catch (_) {
    /* reports are best-effort */
  }
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const delay = document.hidden ? 15_000 : state.connection === "offline" ? 8_000 : 4_000;
  state.pollTimer = setTimeout(pollNow, delay);
}

async function pollNow() {
  clearTimeout(state.pollTimer);
  const started = performance.now();
  try {
    const data = await fetchJSON("/now.json", { timeout: 8_000 });
    const finished = performance.now();
    const sample = data.server_time_ms + (finished - started) / 2 - Date.now();
    state.clockOffset = state.clockOffset ? state.clockOffset * 0.82 + sample * 0.18 : sample;
    state.connection = "online";
    applyNow(data);
  } catch (_) {
    state.connection = "offline";
    refreshStatus();
  } finally {
    schedulePoll();
  }
}

function pollSoon() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(pollNow, 600);
}

/* ---------- lyrics ---------- */

function setLyricsEmpty(text) {
  lyricsEl.textContent = "";
  const empty = document.createElement("p");
  empty.className = "lyrics-empty";
  empty.textContent = text;
  lyricsEl.appendChild(empty);
  state.lyricsLines = [];
  state.lyricEls = [];
  state.lastLyricIndex = -1;
}

function parseLrc(text) {
  const lines = [];
  const pattern = /\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
  for (const raw of String(text || "").split(/\r?\n/)) {
    const times = [];
    let match;
    pattern.lastIndex = 0;
    while ((match = pattern.exec(raw))) {
      const minutes = Number(match[1]);
      const seconds = Number(match[2]);
      const fraction = match[3] ? Number(`0.${match[3]}`) : 0;
      times.push((minutes * 60 + seconds + fraction) * 1000);
    }
    const content = raw.replace(pattern, "").trim();
    if (!content || !times.length) continue;
    for (const time of times) lines.push({ time, text: content });
  }
  lines.sort((a, b) => a.time - b.time);
  return lines;
}

function nearestTranslation(translations, time) {
  let best = null;
  let bestDiff = 450;
  for (const line of translations) {
    const diff = Math.abs(line.time - time);
    if (diff < bestDiff) {
      best = line;
      bestDiff = diff;
    }
  }
  return best ? best.text : "";
}

function renderLyrics(lrc, translation) {
  const lines = parseLrc(lrc);
  const translations = parseLrc(translation);
  if (!lines.length) {
    setLyricsEmpty("暂无歌词");
    return;
  }
  state.lyricsLines = lines;
  state.lyricEls = [];
  state.lastLyricIndex = -1;
  const fragment = document.createDocumentFragment();
  for (const line of lines) {
    const row = document.createElement("div");
    row.className = "lyric-line";
    const text = document.createElement("span");
    text.className = "lyric-text";
    text.textContent = line.text;
    row.appendChild(text);
    const trans = nearestTranslation(translations, line.time);
    if (trans) {
      const sub = document.createElement("span");
      sub.className = "lyric-translation";
      sub.textContent = trans;
      row.appendChild(sub);
    }
    fragment.appendChild(row);
    state.lyricEls.push(row);
  }
  lyricsEl.textContent = "";
  lyricsEl.appendChild(fragment);
  lyricsEl.classList.toggle("hide-translation", !state.showTranslation);
}

async function loadLyrics(url, trackId) {
  if (!url) {
    setLyricsEmpty("暂无歌词");
    return;
  }
  setLyricsEmpty("歌词加载中…");
  try {
    const data = await fetchJSON(url, { timeout: 10_000 });
    if (state.lyricsTrackId !== trackId) return;
    renderLyrics(data.lrc, data.translation);
  } catch (_) {
    if (state.lyricsTrackId === trackId) setLyricsEmpty("歌词暂不可用");
  }
}

function updateLyrics(positionMs) {
  if (!state.lyricEls.length) return;
  let index = -1;
  for (let i = 0; i < state.lyricsLines.length; i += 1) {
    if (state.lyricsLines[i].time <= positionMs + 60) index = i;
    else break;
  }
  if (index === state.lastLyricIndex) return;
  if (state.lastLyricIndex >= 0 && state.lyricEls[state.lastLyricIndex]) {
    state.lyricEls[state.lastLyricIndex].classList.remove("active");
  }
  state.lastLyricIndex = index;
  const active = state.lyricEls[index];
  if (active) {
    active.classList.add("active");
    const top = active.offsetTop - lyricsScroll.clientHeight / 2 + active.clientHeight / 2;
    lyricsScroll.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }
}

/* ---------- queue / messages ---------- */

function renderQueue(items) {
  queueList.textContent = "";
  const pending = items.filter((item) => item.status === "pending");
  queueCount.textContent = String(pending.length);
  if (!pending.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "队列还是空的";
    queueList.appendChild(empty);
    return;
  }
  for (const [index, item] of pending.slice(0, 12).entries()) {
    const row = document.createElement("div");
    row.className = "queue-item";

    const idx = document.createElement("span");
    idx.className = "queue-index";
    idx.textContent = String(index + 1).padStart(2, "0");
    row.appendChild(idx);

    const copy = document.createElement("div");
    copy.className = "queue-copy";
    const name = document.createElement("span");
    name.className = "queue-title";
    name.textContent = item.title || "未知曲目";
    const sub = document.createElement("span");
    sub.className = "queue-sub";
    const parts = [item.artist || "未知歌手"];
    if (item.nickname) parts.push(`by ${item.nickname}`);
    sub.textContent = parts.join(" · ");
    copy.appendChild(name);
    copy.appendChild(sub);
    row.appendChild(copy);

    const time = document.createElement("span");
    time.className = "queue-time";
    time.textContent = fmtTime(item.duration_ms);
    row.appendChild(time);
    queueList.appendChild(row);
  }
}

function renderMessages(items) {
  messageList.textContent = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有留言";
    messageList.appendChild(empty);
    return;
  }
  for (const item of items.slice(0, 10)) {
    const row = document.createElement("div");
    row.className = "message-item";

    const top = document.createElement("div");
    top.className = "message-top";
    const name = document.createElement("span");
    name.className = "message-name";
    name.textContent = item.nickname || "匿名";
    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = fmtRelative(item.created_at);
    top.appendChild(name);
    top.appendChild(time);
    row.appendChild(top);

    if (item.body) {
      const body = document.createElement("p");
      body.className = "message-body";
      body.textContent = item.body;
      row.appendChild(body);
    }
    messageList.appendChild(row);
  }
}

async function loadSidebar() {
  if (state.sidebarLoading) return;
  state.sidebarLoading = true;
  try {
    const data = await fetchJSON("/api/state", { timeout: 8_000 });
    renderQueue(data.queue || []);
    renderMessages(data.messages || []);
    if (data.now && data.now.queue) {
      queueCount.textContent = String(data.now.queue.pending || 0);
    }
  } catch (_) {
    /* sidebar is best-effort */
  } finally {
    state.sidebarLoading = false;
  }
}

/* ---------- search / song request ---------- */

function setSearchStatus(text, tone = "") {
  searchStatus.textContent = text;
  if (tone) searchStatus.dataset.tone = tone;
  else delete searchStatus.dataset.tone;
}

function setSelectedCandidate(item) {
  state.selectedCandidate = item;
  for (const row of searchResults.querySelectorAll(".search-result")) {
    row.classList.toggle("selected", row.dataset.candidateId === item.candidate_id);
  }
  selectedTrack.textContent = `${item.title} / ${item.artist || "未知歌手"}`;
  selectBar.classList.remove("hidden");
  requestBtn.disabled = false;
}

function clearSelectedCandidate() {
  state.selectedCandidate = null;
  for (const row of searchResults.querySelectorAll(".search-result")) {
    row.classList.remove("selected");
  }
  selectBar.classList.add("hidden");
  selectedTrack.textContent = "";
}

function renderPublicSearchResults(results) {
  state.searchResults = results;
  searchResults.textContent = "";
  if (!results.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "没有找到匹配的歌曲";
    searchResults.appendChild(empty);
    return;
  }
  for (const item of results) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "search-result";
    row.dataset.candidateId = item.candidate_id;

    const image = document.createElement("img");
    image.className = "search-cover";
    image.src = item.cover_url || "/placeholder.svg";
    image.alt = "";
    image.onerror = () => {
      image.onerror = null;
      image.src = "/placeholder.svg";
    };
    row.appendChild(image);

    const copy = document.createElement("span");
    copy.className = "search-copy";
    const name = document.createElement("span");
    name.className = "search-title";
    name.textContent = item.title || "未知曲目";
    const sub = document.createElement("span");
    sub.className = "search-sub";
    sub.textContent = [item.artist || "未知歌手", item.album || ""].filter(Boolean).join(" · ");
    copy.appendChild(name);
    copy.appendChild(sub);
    row.appendChild(copy);

    const side = document.createElement("span");
    side.className = "search-side";
    const source = document.createElement("span");
    source.className = "search-source";
    source.textContent = item.source_label || sourceLabel(item.source);
    const duration = document.createElement("span");
    duration.className = "search-duration";
    duration.textContent = fmtTime(item.duration_ms, item.duration_estimated);
    side.appendChild(source);
    side.appendChild(duration);
    row.appendChild(side);

    row.addEventListener("click", () => setSelectedCandidate(item));
    searchResults.appendChild(row);
  }
}

async function performSearch(event) {
  event.preventDefault();
  if (state.searchBusy) return;
  const query = searchInput.value.trim();
  if (!query) {
    setSearchStatus("请输入歌名或歌手", "error");
    searchInput.focus();
    return;
  }
  state.searchBusy = true;
  searchBtn.disabled = true;
  setSearchStatus("正在搜索…", "busy");
  clearSelectedCandidate();
  try {
    const data = await fetchJSON(`/api/search?q=${encodeURIComponent(query)}`, {
      timeout: 25_000,
    });
    renderPublicSearchResults(data.results || []);
    setSearchStatus(`找到 ${(data.results || []).length} 首`, "ok");
  } catch (error) {
    renderPublicSearchResults([]);
    const retry = error.data && error.data.retry_after ? `，${error.data.retry_after} 秒后可再试` : "";
    setSearchStatus(`${errorText(error, "搜索失败")}${retry}`, "error");
  } finally {
    state.searchBusy = false;
    searchBtn.disabled = false;
  }
}

async function requestSelected() {
  if (state.requestBusy) return;
  if (!state.selectedCandidate) {
    setSearchStatus("请先选择一首歌", "error");
    return;
  }
  if (!state.nickname) {
    openNicknameModal();
    return;
  }
  state.requestBusy = true;
  requestBtn.disabled = true;
  setSearchStatus("正在加入队列…", "busy");
  try {
    const data = await fetchJSON("/api/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_id: state.selectedCandidate.candidate_id,
        nickname: state.nickname,
        ts: state.requestTs,
      }),
      timeout: 25_000,
    });
    const trackTitle = (data.track && data.track.title) || state.selectedCandidate.title;
    setSearchStatus(`已加入队列：${trackTitle}`, "ok");
    state.requestTs = Date.now();
    clearSelectedCandidate();
    loadSidebar();
    pollSoon();
  } catch (error) {
    const retry = error.data && error.data.retry_after ? `，${error.data.retry_after} 秒后可再试` : "";
    setSearchStatus(`${errorText(error, "点歌失败")}${retry}`, "error");
    if (error.status === 410) {
      renderPublicSearchResults([]);
      clearSelectedCandidate();
    }
  } finally {
    state.requestBusy = false;
    requestBtn.disabled = !state.selectedCandidate;
  }
}

/* ---------- messages ---------- */

function setMessageStatus(text, tone = "") {
  messageStatus.textContent = text;
  if (tone) messageStatus.dataset.tone = tone;
  else delete messageStatus.dataset.tone;
}

async function submitMessage(event) {
  event.preventDefault();
  if (state.messageBusy) return;
  if (!state.nickname) {
    openNicknameModal();
    return;
  }
  const body = messageInput.value.trim();
  if (!body) {
    setMessageStatus("请输入留言内容", "error");
    messageInput.focus();
    return;
  }
  state.messageBusy = true;
  messageBtn.disabled = true;
  setMessageStatus("正在发送…", "busy");
  try {
    await fetchJSON("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nickname: state.nickname,
        body,
        ts: state.messageTs,
      }),
      timeout: 15_000,
    });
    messageInput.value = "";
    state.messageTs = Date.now();
    setMessageStatus("留言已发送", "ok");
    loadSidebar();
  } catch (error) {
    const retry = error.data && error.data.retry_after ? `，${error.data.retry_after} 秒后可再试` : "";
    setMessageStatus(`${errorText(error, "发送失败")}${retry}`, "error");
  } finally {
    state.messageBusy = false;
    messageBtn.disabled = false;
  }
}

/* ---------- modals ---------- */

function updateModalState() {
  const open =
    !nicknameModal.classList.contains("hidden") || !adminModal.classList.contains("hidden");
  document.body.classList.toggle("modal-open", open);
}

function showModal(element) {
  element.classList.remove("hidden");
  updateModalState();
}

function hideModal(element) {
  element.classList.add("hidden");
  updateModalState();
}

/* ---------- admin ---------- */

function adminHeaders(extra = {}) {
  return { ...extra, Authorization: `Bearer ${state.adminToken}` };
}

async function adminFetch(path, options = {}) {
  const headers = adminHeaders(options.headers || {});
  return fetchJSON(path, { ...options, headers });
}

async function adminJson(path, payload) {
  return adminFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
}

function showAdminLogin() {
  adminLogin.classList.remove("hidden");
  adminPanel.classList.add("hidden");
}

function showAdminPanel() {
  adminLogin.classList.add("hidden");
  adminPanel.classList.remove("hidden");
}

function openAdmin() {
  showModal(adminModal);
  if (state.adminToken) {
    showAdminPanel();
    loadAdminState();
  } else {
    showAdminLogin();
    setTimeout(() => adminToken.focus(), 50);
  }
}

function adminLogoutNow() {
  state.adminToken = "";
  sessionSet("radio.adminToken", null);
  adminToken.value = "";
  showAdminLogin();
}

async function adminLoginNow() {
  const token = adminToken.value.trim();
  if (!token) {
    adminLoginStatus.textContent = "请输入管理 token";
    adminLoginStatus.dataset.tone = "error";
    return;
  }
  state.adminToken = token;
  adminLoginStatus.textContent = "正在验证…";
  adminLoginStatus.dataset.tone = "busy";
  adminLoginBtn.disabled = true;
  try {
    const data = await adminFetch("/api/admin/state");
    sessionSet("radio.adminToken", token);
    adminLoginStatus.textContent = "";
    showAdminPanel();
    applyAdminState(data);
  } catch (error) {
    state.adminToken = "";
    sessionSet("radio.adminToken", null);
    adminLoginStatus.textContent = errorText(error, "登录失败");
    adminLoginStatus.dataset.tone = "error";
  } finally {
    adminLoginBtn.disabled = false;
  }
}

async function loadAdminState() {
  if (!state.adminToken) return;
  try {
    applyAdminState(await adminFetch("/api/admin/state"));
  } catch (error) {
    if (error.status === 401) {
      adminLogoutNow();
      adminLoginStatus.textContent = "管理 token 已失效";
      adminLoginStatus.dataset.tone = "error";
    } else {
      adminSearchStatus.textContent = errorText(error, "加载管理状态失败");
      adminSearchStatus.dataset.tone = "error";
    }
  }
}

function adminRow(titleText, subText, actionText, action) {
  const row = document.createElement("div");
  row.className = "admin-row";
  const copy = document.createElement("div");
  copy.className = "admin-row-copy";
  const titleEl = document.createElement("span");
  titleEl.className = "admin-row-title";
  titleEl.textContent = titleText;
  const subEl = document.createElement("span");
  subEl.className = "admin-row-sub";
  subEl.textContent = subText;
  copy.appendChild(titleEl);
  copy.appendChild(subEl);
  row.appendChild(copy);
  if (actionText && action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = actionText;
    button.addEventListener("click", action);
    row.appendChild(button);
  }
  return row;
}

function applyAdminState(data) {
  const now = data.now || {};
  adminNow.textContent = "";
  if (now.track) {
    const strong = document.createElement("strong");
    strong.textContent = `${now.track.title} / ${now.track.artist || "未知歌手"}`;
    adminNow.appendChild(strong);
    const status = document.createElement("span");
    status.textContent = now.paused ? " · 已暂停" : " · 播放中";
    adminNow.appendChild(status);
    if (now.next) {
      const next = document.createElement("div");
      next.textContent = `下一首：${now.next.title} / ${now.next.artist || "未知歌手"}`;
      adminNow.appendChild(next);
    }
  } else {
    adminNow.textContent = "当前没有播放。添加曲目后会自动开始。";
  }

  adminQueue.textContent = "";
  const queue = data.queue || [];
  if (!queue.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "队列为空";
    adminQueue.appendChild(empty);
  } else {
    for (const item of queue) {
      const sub = [item.artist || "未知歌手", item.status === "playing" ? "播放中" : "等待中"];
      if (item.nickname) sub.push(`by ${item.nickname}`);
      adminQueue.appendChild(adminRow(item.title || "未知曲目", sub.join(" · "), "", null));
    }
  }

  adminMessages.textContent = "";
  const messages = data.messages || [];
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有留言";
    adminMessages.appendChild(empty);
  } else {
    for (const item of messages) {
      adminMessages.appendChild(
        adminRow(
          item.nickname || "匿名",
          `${item.body || ""}${item.created_at ? ` · ${fmtRelative(item.created_at)}` : ""}`,
          "隐藏",
          async () => {
            await adminJson("/api/admin/hide-message", { id: item.id });
            await loadAdminState();
            loadSidebar();
          }
        )
      );
    }
  }

  const settings = data.settings || {};
  setStationName.value = settings.station_name || "";
  setSubtitle.value = settings.subtitle || "";
  setPageTitle.value = settings.page_title || "";
  setNotice.value = settings.notice || "";
  setBgInterval.value = String(settings.background_interval_sec || 300);

  renderAdminGallery(data.backgrounds || []);
}

function renderAdminGallery(items) {
  bgGallery.textContent = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有上传背景";
    bgGallery.appendChild(empty);
    return;
  }
  for (const item of items) {
    const thumb = document.createElement("div");
    thumb.className = "bg-thumb";
    const image = document.createElement("img");
    image.src = item.url;
    image.alt = "";
    thumb.appendChild(image);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "×";
    button.title = "删除背景";
    button.addEventListener("click", async () => {
      try {
        await adminJson("/api/admin/background/delete", { name: item.name });
        await loadConfig();
        await loadAdminState();
        bgStatus.textContent = "已删除";
        bgStatus.dataset.tone = "ok";
      } catch (error) {
        bgStatus.textContent = errorText(error, "删除失败");
        bgStatus.dataset.tone = "error";
      }
    });
    thumb.appendChild(button);
    bgGallery.appendChild(thumb);
  }
}

async function adminAction(path, successText) {
  if (state.adminBusy) return;
  state.adminBusy = true;
  try {
    await adminFetch(path, { method: "POST" });
    await loadAdminState();
    pollSoon();
    if (successText) {
      adminSearchStatus.textContent = successText;
      adminSearchStatus.dataset.tone = "ok";
    }
  } catch (error) {
    adminSearchStatus.textContent = errorText(error);
    adminSearchStatus.dataset.tone = "error";
  } finally {
    state.adminBusy = false;
  }
}

async function savePageSettings() {
  settingsStatus.textContent = "正在保存…";
  settingsStatus.dataset.tone = "busy";
  try {
    const data = await adminJson("/api/admin/settings", {
      station_name: setStationName.value,
      subtitle: setSubtitle.value,
      page_title: setPageTitle.value,
      notice: setNotice.value,
      background_interval_sec: Number(setBgInterval.value),
    });
    settingsStatus.textContent = "已保存";
    settingsStatus.dataset.tone = "ok";
    applyConfig({ ...(state.uiConfig || {}), ...(data.settings || {}), backgrounds: state.backgrounds });
    await loadConfig();
  } catch (error) {
    settingsStatus.textContent = errorText(error, "保存失败");
    settingsStatus.dataset.tone = "error";
  }
}

async function uploadBackground() {
  const file = bgFile.files && bgFile.files[0];
  if (!file) {
    bgStatus.textContent = "请选择图片";
    bgStatus.dataset.tone = "error";
    return;
  }
  if (file.size > 8 * 1024 * 1024) {
    bgStatus.textContent = "图片不能超过 8MB";
    bgStatus.dataset.tone = "error";
    return;
  }
  bgStatus.textContent = "正在上传…";
  bgStatus.dataset.tone = "busy";
  bgUpload.disabled = true;
  try {
    const response = await fetch("/api/admin/background", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${state.adminToken}`,
        "Content-Type": file.type || "application/octet-stream",
      },
      body: file,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.message || `HTTP ${response.status}`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    bgFile.value = "";
    bgStatus.textContent = "上传成功";
    bgStatus.dataset.tone = "ok";
    await loadConfig();
    applyRandomBackground(true);
    await loadAdminState();
  } catch (error) {
    bgStatus.textContent = errorText(error, "上传失败");
    bgStatus.dataset.tone = "error";
  } finally {
    bgUpload.disabled = false;
  }
}

async function adminSearch(event) {
  event.preventDefault();
  const query = adminSearchInput.value.trim();
  if (!query) return;
  adminSearchStatus.textContent = "正在搜索…";
  adminSearchStatus.dataset.tone = "busy";
  adminSearchResults.textContent = "";
  try {
    const data = await fetchJSON(`/api/search?q=${encodeURIComponent(query)}`, {
      timeout: 25_000,
    });
    const results = data.results || [];
    adminSearchStatus.textContent = `找到 ${results.length} 首`;
    adminSearchStatus.dataset.tone = "ok";
    for (const item of results) {
      const row = document.createElement("div");
      row.className = "search-result";
      const image = document.createElement("img");
      image.className = "search-cover";
      image.src = item.cover_url || "/placeholder.svg";
      image.alt = "";
      row.appendChild(image);
      const copy = document.createElement("span");
      copy.className = "search-copy";
      const name = document.createElement("span");
      name.className = "search-title";
      name.textContent = item.title;
      const sub = document.createElement("span");
      sub.className = "search-sub";
      sub.textContent = [item.artist || "未知歌手", item.source_label || item.source].join(" · ");
      copy.appendChild(name);
      copy.appendChild(sub);
      row.appendChild(copy);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "assign-btn";
      button.textContent = "加入列表";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await adminJson("/api/admin/track", { candidate_id: item.candidate_id });
          adminSearchStatus.textContent = `已加入播放列表：${item.title}`;
          adminSearchStatus.dataset.tone = "ok";
          await loadAdminState();
          pollSoon();
        } catch (error) {
          adminSearchStatus.textContent = errorText(error, "加入失败");
          adminSearchStatus.dataset.tone = "error";
          button.disabled = false;
        }
      });
      row.appendChild(button);
      adminSearchResults.appendChild(row);
    }
  } catch (error) {
    adminSearchStatus.textContent = errorText(error, "搜索失败");
    adminSearchStatus.dataset.tone = "error";
  }
}

function switchAdminTab(tab) {
  state.adminTab = tab;
  for (const button of adminModal.querySelectorAll(".admin-tabs button")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  for (const section of adminModal.querySelectorAll(".admin-tab")) {
    section.classList.toggle("hidden", section.id !== `tab-${tab}`);
  }
}

/* ---------- render loop ---------- */

function frame() {
  const now = state.now;
  if (now && now.track) {
    const position = currentPosition(now);
    const duration = Number(now.duration_ms) || 0;
    const percent = duration > 0 ? clamp((position / duration) * 100, 0, 100) : 0;
    progressFill.style.width = `${percent.toFixed(2)}%`;
    progressTrack.setAttribute("aria-valuenow", String(Math.round(percent)));
    const seconds = Math.floor(position / 1000);
    if (seconds !== state.lastPositionSec) {
      state.lastPositionSec = seconds;
      positionEl.textContent = fmtTime(position);
    }
    updateLyrics(position);
  }
  requestAnimationFrame(frame);
}

/* ---------- events ---------- */

playBtn.addEventListener("click", () => {
  if (!state.now || !state.now.track || state.serverPaused) return;
  if (!audio.paused) {
    state.userWantsPlay = false;
    audio.pause();
    updatePlayButton();
    return;
  }
  state.userWantsPlay = true;
  seekToLive();
  attemptPlay();
  updatePlayButton();
});

muteBtn.addEventListener("click", () => {
  audio.muted = !audio.muted;
  muteBtn.setAttribute("aria-label", audio.muted ? "取消静音" : "静音");
  muteBtn.style.color = audio.muted ? "var(--faint)" : "";
});

volumeEl.addEventListener("input", () => {
  audio.volume = clamp(Number(volumeEl.value), 0, 1);
  storageSet("radio.volume", volumeEl.value);
});

translationToggle.addEventListener("click", () => {
  state.showTranslation = !state.showTranslation;
  translationToggle.setAttribute("aria-pressed", String(state.showTranslation));
  lyricsEl.classList.toggle("hide-translation", !state.showTranslation);
});

nicknameBtn.addEventListener("click", openNicknameModal);
nicknameSave.addEventListener("click", saveNickname);
nicknameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveNickname();
});

searchForm.addEventListener("submit", performSearch);
requestBtn.addEventListener("click", requestSelected);
messageForm.addEventListener("submit", submitMessage);

adminBtn.addEventListener("click", openAdmin);
adminClose.addEventListener("click", () => hideModal(adminModal));
adminLoginBtn.addEventListener("click", adminLoginNow);
adminToken.addEventListener("keydown", (event) => {
  if (event.key === "Enter") adminLoginNow();
});
adminLogout.addEventListener("click", adminLogoutNow);
adminSkip.addEventListener("click", () => adminAction("/api/admin/skip", "已切歌"));
adminPause.addEventListener("click", () => adminAction("/api/admin/pause", "已暂停"));
adminResume.addEventListener("click", () => adminAction("/api/admin/resume", "已恢复"));
adminClearQueue.addEventListener("click", () =>
  adminAction("/api/admin/clear-queue", "已清空队列")
);
saveSettings.addEventListener("click", savePageSettings);
bgUpload.addEventListener("click", uploadBackground);
adminSearchForm.addEventListener("submit", adminSearch);
for (const button of adminModal.querySelectorAll(".admin-tabs button")) {
  button.addEventListener("click", () => switchAdminTab(button.dataset.tab));
}
adminModal.addEventListener("click", (event) => {
  if (event.target === adminModal) hideModal(adminModal);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!adminModal.classList.contains("hidden")) {
    hideModal(adminModal);
  } else if (!nicknameModal.classList.contains("hidden") && state.nickname) {
    hideModal(nicknameModal);
  }
});

audio.addEventListener("loadedmetadata", () => {
  if (state.pendingSeekMs > 0 && Number.isFinite(audio.duration) && audio.duration > 0) {
    const target = Math.min(state.pendingSeekMs / 1000, Math.max(0, audio.duration - 0.35));
    try {
      audio.currentTime = target;
    } catch (_) {
      /* ignore */
    }
  }
  state.pendingSeekMs = 0;
  if (state.userWantsPlay && !state.serverPaused) attemptPlay();
});

audio.addEventListener("playing", () => {
  clearStalledTimer();
  state.audioState = "live";
  refreshStatus();
  updatePlayButton();
});

audio.addEventListener("waiting", () => {
  if (!state.userWantsPlay) return;
  state.audioState = "buffering";
  refreshStatus();
  startStalledTimer();
});

audio.addEventListener("stalled", () => {
  if (!state.userWantsPlay) return;
  state.audioState = "buffering";
  refreshStatus();
  startStalledTimer();
});

audio.addEventListener("pause", updatePlayButton);
audio.addEventListener("play", updatePlayButton);
audio.addEventListener("error", () => {
  if (audio.src) tryNextSource("error");
});
audio.addEventListener("ended", () => {
  clearStalledTimer();
  report("ended");
  pollSoon();
});

cover.addEventListener("error", () => {
  cover.onerror = null;
  cover.src = "/placeholder.svg";
});

window.addEventListener("online", () => {
  state.connection = "connecting";
  pollNow();
});

window.addEventListener("offline", () => {
  state.connection = "offline";
  refreshStatus();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollNow();
    loadSidebar();
  } else {
    schedulePoll();
  }
});

/* ---------- init ---------- */

loadNickname();
state.adminToken = sessionGet("radio.adminToken") || "";

const savedVolume = storageGet("radio.volume");
if (savedVolume !== null) volumeEl.value = savedVolume;
audio.volume = clamp(Number(volumeEl.value) || 0.8, 0, 1);

updatePlayButton();
refreshStatus();
setLyricsEmpty("等待开播");
requestAnimationFrame(frame);
loadConfig();
pollNow();
loadSidebar();

if (!state.nickname) openNicknameModal();

setInterval(() => {
  if (!document.hidden) loadSidebar();
}, 12_000);
setInterval(() => {
  if (!document.hidden) loadConfig();
}, 60_000);
