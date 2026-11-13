"use strict";

const $ = (id) => document.getElementById(id);

const audio = $("audio");
const bgLayerA = $("bgLayerA");
const bgLayerB = $("bgLayerB");
const stationName = $("stationName");
const stationSubtitle = $("stationSubtitle");
const noticeBar = $("noticeBar");
const syncStatus = $("syncStatus");
const listenerCount = $("listenerCount");
const nicknameBtn = $("nicknameBtn");
const livePill = $("livePill");
const title = $("title");
const artist = $("artist");
const album = $("album");
const positionEl = $("position");
const durationEl = $("duration");
const progressTrack = $("progressTrack");
const progressFill = $("progressFill");
const playBtn = $("playBtn");
const voteSkipBtn = $("voteSkipBtn");
const voteInfo = $("voteInfo");
const muteBtn = $("muteBtn");
const volumeEl = $("volume");
const audioMode = $("audioMode");
const audioLink = $("audioLink");
const sourceBadge = $("sourceBadge");
const cover = $("cover");
const lyricsScroll = $("lyricsScroll");
const lyricsEl = $("lyrics");
const translationToggle = $("translationToggle");
const playlistList = $("playlistList");
const playlistCount = $("playlistCount");
const searchForm = $("searchForm");
const searchInput = $("searchInput");
const searchBtn = $("searchBtn");
const searchStatus = $("searchStatus");
const searchResults = $("searchResults");
const selectedBar = $("selectedBar");
const selectedTrack = $("selectedTrack");
const requestBtn = $("requestBtn");
const clearSearchBtn = $("clearSearch");
const messageForm = $("messageForm");
const messageInput = $("messageInput");
const messageBtn = $("messageBtn");
const messageStatus = $("messageStatus");
const messageIdentity = $("messageIdentity");
const messageList = $("messageList");
const messagesCard = $("messagesCard");
const messagesToggle = $("messagesToggle");
const messagesBody = $("messagesBody");

const nicknameModal = $("nicknameModal");
const nicknameInput = $("nicknameInput");
const nicknameSave = $("nicknameSave");
const nicknameStatus = $("nicknameStatus");

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
  lastMessageCount: -1,
  messagesCollapsed: false,
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

function updateListenerUI(now) {
  const listeners = now && Number.isFinite(Number(now.listeners)) ? Number(now.listeners) : 0;
  listenerCount.textContent = `一起听 ${listeners} 人`;
}

function updateVoteUI(now) {
  const hasTrack = Boolean(now && now.track);
  const vote = (now && now.vote) || {};
  const votes = Number(vote.votes) || 0;
  const listeners = Number(vote.listeners) || 0;
  const needed = Number(vote.needed) || 1;
  const total = listeners || needed;
  voteInfo.textContent = hasTrack ? `${votes} / ${total}` : "0 / 1";
  voteInfo.title = `达到 ${needed} 票切换下一首`;
  voteSkipBtn.disabled = !hasTrack || Boolean(vote.voted);
  voteSkipBtn.textContent = vote.voted ? "已投票" : "投票切歌";
}

async function voteSkip() {
  if (!state.now || !state.now.track) return;
  voteSkipBtn.disabled = true;
  try {
    const data = await fetchJSON("/api/vote-skip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      timeout: 10_000,
    });
    if (data.now) applyNow(data.now);
    if (data.switched) {
      voteInfo.textContent = "已切歌";
      loadSidebar();
      pollSoon();
    }
  } catch (error) {
    voteInfo.textContent = errorText(error, "投票失败");
    pollNow();
  }
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

function audioKeyForCurrent() {
  return `${state.seq}:${state.audioRev}:${state.sourceIndex}`;
}

function ensureSourceLoaded(now = state.now) {
  if (!state.sources.length) return false;
  if (!audio.src || state.audioKey !== audioKeyForCurrent()) {
    loadSource(currentPosition(now));
    return true;
  }
  return false;
}

function unloadAudio() {
  if (audio.src) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  state.audioKey = null;
}

function tryNextSource(reason) {
  if (!state.userWantsPlay) return;
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
  updateListenerUI(now);

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
    updateVoteUI(now);
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
    if (state.userWantsPlay && sources.length) {
      loadSource(currentPosition(now));
    } else {
      unloadAudio();
      state.audioState = sources.length ? "idle" : "error";
      updateAudioMode();
    }
  } else if (isNewAudio) {
    state.sources = sources;
    state.sourceIndex = 0;
    clearStalledTimer();
    if (state.userWantsPlay && sources.length) {
      loadSource(currentPosition(now));
    } else {
      unloadAudio();
      state.audioState = sources.length ? "idle" : "error";
      updateAudioMode();
    }
  }

  if (state.serverPaused) {
    audio.pause();
    clearStalledTimer();
  } else if (state.userWantsPlay && !audio.src) {
    ensureSourceLoaded(now);
  } else if (state.userWantsPlay && audio.paused) {
    seekToLive();
    attemptPlay();
  }

  setLivePill();
  updatePlayButton();
  updateVoteUI(now);
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

/* ---------- playlist / messages ---------- */

function renderPlaylist(items) {
  playlistList.textContent = "";
  playlistCount.textContent = String(
    items.filter((item) => item.status !== "playing").length
  );
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "播放列表为空";
    playlistList.appendChild(empty);
    return;
  }

  for (const [index, item] of items.slice(0, 60).entries()) {
    const playing = item.status === "playing";
    const played = item.status === "played";
    const row = document.createElement("div");
    row.className = `playlist-item${playing ? " playing" : ""}${played ? " played" : ""}`;

    const idx = document.createElement("span");
    idx.className = "playlist-index";
    idx.textContent = playing ? "ON" : String(index + 1).padStart(2, "0");
    row.appendChild(idx);

    const image = document.createElement("img");
    image.className = "playlist-cover";
    image.src = item.cover_url || "/placeholder.svg";
    image.alt = "";
    image.onerror = () => {
      if (!image.src.endsWith("/placeholder.svg")) image.src = "/placeholder.svg";
    };
    row.appendChild(image);

    const copy = document.createElement("div");
    copy.className = "playlist-copy";
    const name = document.createElement("span");
    name.className = "playlist-title";
    name.textContent = item.title || "未知曲目";
    const sub = document.createElement("span");
    sub.className = "playlist-sub";
    const parts = [item.artist || "未知歌手"];
    if (item.nickname && item.status === "pending") parts.push(`by ${item.nickname}`);
    sub.textContent = parts.join(" · ");
    copy.appendChild(name);
    copy.appendChild(sub);
    row.appendChild(copy);

    const side = document.createElement("div");
    side.className = "playlist-side";

    const badge = document.createElement("span");
    badge.className = "playlist-badge";
    badge.textContent = playing
      ? "正在播放"
      : item.status === "pending"
        ? "点歌"
        : played
          ? "已播放"
          : "播放列表";
    side.appendChild(badge);

    const time = document.createElement("span");
    time.className = "playlist-duration";
    time.textContent = fmtTime(item.duration_ms);
    side.appendChild(time);

    if (item.status === "pending" && item.queue_id) {
      const pins = Number(item.priority) || 0;
      const pinBtn = document.createElement("button");
      pinBtn.type = "button";
      pinBtn.className = `pin-btn${pins > 0 ? " active" : ""}`;
      pinBtn.textContent = pins > 0 ? `顶 ${pins}` : "顶";
      pinBtn.title = "置顶这首歌";
      pinBtn.addEventListener("click", async () => {
        pinBtn.disabled = true;
        try {
          await fetchJSON("/api/queue/pin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ queue_id: item.queue_id }),
            timeout: 10_000,
          });
          await loadSidebar();
          pollSoon();
        } catch (_) {
          pinBtn.disabled = false;
        }
      });
      side.appendChild(pinBtn);
    }

    row.appendChild(side);
    playlistList.appendChild(row);
  }
}

function setMessagesCollapsed(collapsed, persist = true) {
  state.messagesCollapsed = Boolean(collapsed);
  messagesCard.classList.toggle("collapsed", state.messagesCollapsed);
  messagesToggle.textContent = state.messagesCollapsed ? "展开" : "收起";
  messagesToggle.setAttribute("aria-expanded", String(!state.messagesCollapsed));
  if (persist) storageSet("radio.messagesCollapsed", state.messagesCollapsed ? "1" : "0");
  if (!state.messagesCollapsed) {
    requestAnimationFrame(() => {
      messageList.scrollTop = messageList.scrollHeight;
    });
  }
}

function renderMessages(items) {
  const wasNearBottom =
    messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight < 48;
  const previousScrollTop = messageList.scrollTop;
  const previousHeight = messageList.scrollHeight;
  const previousCount = state.lastMessageCount;
  const ordered = items.slice(0, 10).reverse();
  state.lastMessageCount = ordered.length;

  messageList.textContent = "";
  if (!ordered.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有留言";
    messageList.appendChild(empty);
    return;
  }

  for (const item of ordered) {
    const row = document.createElement("div");
    row.className = "message-item";

    const name = document.createElement("span");
    name.className = "message-name";
    name.textContent = item.nickname || "匿名";

    const body = document.createElement("span");
    body.className = "message-body";
    body.textContent = item.body || "";

    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = fmtRelative(item.created_at);

    row.appendChild(name);
    row.appendChild(body);
    row.appendChild(time);
    messageList.appendChild(row);
  }

  const shouldStickToBottom = wasNearBottom || previousCount < 0 || ordered.length !== previousCount;
  requestAnimationFrame(() => {
    if (shouldStickToBottom) {
      messageList.scrollTop = messageList.scrollHeight;
    } else {
      messageList.scrollTop = previousScrollTop + (messageList.scrollHeight - previousHeight);
    }
  });
}

async function loadSidebar() {
  if (state.sidebarLoading) return;
  state.sidebarLoading = true;
  try {
    const data = await fetchJSON("/api/state", { timeout: 8_000 });
    renderPlaylist(data.upcoming || []);
    renderMessages(data.messages || []);
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
  selectedBar.classList.remove("hidden");
  requestBtn.disabled = false;
}

function clearSelectedCandidate() {
  state.selectedCandidate = null;
  for (const row of searchResults.querySelectorAll(".search-result")) {
    row.classList.remove("selected");
  }
  selectedTrack.textContent = "";
  requestBtn.disabled = true;
}

function enterSearchMode() {
  searchResults.classList.remove("hidden");
  playlistList.classList.add("hidden");
  selectedBar.classList.remove("hidden");
  selectedTrack.textContent = "选择一首歌后点确认";
}

function exitSearchMode() {
  searchResults.classList.add("hidden");
  searchResults.textContent = "";
  playlistList.classList.remove("hidden");
  selectedBar.classList.add("hidden");
  clearSelectedCandidate();
  searchInput.value = "";
  setSearchStatus("");
}

function renderPublicSearchResults(results) {
  state.searchResults = results;
  enterSearchMode();
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
      if (!image.src.endsWith("/placeholder.svg")) image.src = "/placeholder.svg";
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
  enterSearchMode();
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
    state.requestTs = Date.now();
    exitSearchMode();
    setSearchStatus(`已加入队列：${trackTitle}`, "ok");
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
  const open = !nicknameModal.classList.contains("hidden");
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
  if (!state.sources.length) {
    state.audioState = "error";
    refreshStatus();
    return;
  }
  state.userWantsPlay = true;
  if (ensureSourceLoaded()) {
    updatePlayButton();
    return;
  }
  seekToLive();
  attemptPlay();
  updatePlayButton();
});

voteSkipBtn.addEventListener("click", voteSkip);

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

messagesToggle.addEventListener("click", () =>
  setMessagesCollapsed(!state.messagesCollapsed)
);

nicknameBtn.addEventListener("click", openNicknameModal);
nicknameSave.addEventListener("click", saveNickname);
nicknameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveNickname();
});

searchForm.addEventListener("submit", performSearch);
requestBtn.addEventListener("click", requestSelected);
clearSearchBtn.addEventListener("click", exitSearchMode);
messageForm.addEventListener("submit", submitMessage);

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!nicknameModal.classList.contains("hidden") && state.nickname) {
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
  if (state.userWantsPlay && audio.src) tryNextSource("error");
});
audio.addEventListener("ended", () => {
  clearStalledTimer();
  report("ended");
  pollSoon();
});

cover.addEventListener("error", () => {
  if (!cover.src.endsWith("/placeholder.svg")) cover.src = "/placeholder.svg";
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
setMessagesCollapsed(storageGet("radio.messagesCollapsed") === "1", false);

const savedVolume = storageGet("radio.volume");
if (savedVolume !== null) volumeEl.value = savedVolume;
const initialVolume = Number(volumeEl.value);
audio.volume = clamp(Number.isFinite(initialVolume) ? initialVolume : 0.8, 0, 1);

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
