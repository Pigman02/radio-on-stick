"use strict";

const $ = (id) => document.getElementById(id);

const adminStatus = $("adminStatus");
const loginCard = $("loginCard");
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
  token: "",
  busy: false,
  tab: "control",
};

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

function fmtTime(ms, estimated = false) {
  if (!Number.isFinite(ms) || ms < 0) return "--:--";
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${estimated ? "~" : ""}${minutes}:${String(seconds).padStart(2, "0")}`;
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

function setStatus(kind, text) {
  adminStatus.dataset.state = kind;
  adminStatus.textContent = text;
}

function adminHeaders(extra = {}) {
  return { ...extra, Authorization: `Bearer ${state.token}` };
}

async function adminFetch(path, options = {}) {
  return fetchJSON(path, { ...options, headers: adminHeaders(options.headers || {}) });
}

async function adminJson(path, payload) {
  return adminFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
}

function showLogin() {
  loginCard.classList.remove("hidden");
  adminPanel.classList.add("hidden");
}

function showPanel() {
  loginCard.classList.add("hidden");
  adminPanel.classList.remove("hidden");
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

function applyState(data) {
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
            await loadState();
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

  renderGallery(data.backgrounds || []);
}

function renderGallery(items) {
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
        bgStatus.textContent = "已删除";
        bgStatus.dataset.tone = "ok";
        await loadState();
      } catch (error) {
        bgStatus.textContent = errorText(error, "删除失败");
        bgStatus.dataset.tone = "error";
      }
    });
    thumb.appendChild(button);
    bgGallery.appendChild(thumb);
  }
}

async function loadState() {
  if (!state.token) {
    showLogin();
    setStatus("offline", "未登录");
    return;
  }
  try {
    applyState(await adminFetch("/api/admin/state"));
    showPanel();
    setStatus("live", "已连接");
    adminLoginStatus.textContent = "";
  } catch (error) {
    if (error.status === 401 || error.status === 503) {
      state.token = "";
      sessionSet("radio.adminToken", null);
      showLogin();
      setStatus("offline", "登录失效");
      adminLoginStatus.textContent = errorText(error, "登录失效");
      adminLoginStatus.dataset.tone = "error";
    } else {
      setStatus("offline", "连接中断");
      adminSearchStatus.textContent = errorText(error, "加载失败");
      adminSearchStatus.dataset.tone = "error";
    }
  }
}

async function login() {
  const token = adminToken.value.trim();
  if (!token) {
    adminLoginStatus.textContent = "请输入管理 token";
    adminLoginStatus.dataset.tone = "error";
    return;
  }
  state.token = token;
  adminLoginStatus.textContent = "正在验证…";
  adminLoginStatus.dataset.tone = "busy";
  adminLoginBtn.disabled = true;
  try {
    const data = await adminFetch("/api/admin/state");
    sessionSet("radio.adminToken", token);
    applyState(data);
    showPanel();
    setStatus("live", "已连接");
    adminLoginStatus.textContent = "";
  } catch (error) {
    state.token = "";
    sessionSet("radio.adminToken", null);
    adminLoginStatus.textContent = errorText(error, "登录失败");
    adminLoginStatus.dataset.tone = "error";
  } finally {
    adminLoginBtn.disabled = false;
  }
}

function logout() {
  state.token = "";
  sessionSet("radio.adminToken", null);
  adminToken.value = "";
  showLogin();
  setStatus("offline", "未登录");
}

async function adminAction(path, successText) {
  if (state.busy) return;
  state.busy = true;
  try {
    await adminFetch(path, { method: "POST" });
    await loadState();
    adminSearchStatus.textContent = successText;
    adminSearchStatus.dataset.tone = "ok";
  } catch (error) {
    adminSearchStatus.textContent = errorText(error);
    adminSearchStatus.dataset.tone = "error";
  } finally {
    state.busy = false;
  }
}

async function savePageSettings() {
  settingsStatus.textContent = "正在保存…";
  settingsStatus.dataset.tone = "busy";
  try {
    await adminJson("/api/admin/settings", {
      station_name: setStationName.value,
      subtitle: setSubtitle.value,
      page_title: setPageTitle.value,
      notice: setNotice.value,
      background_interval_sec: Number(setBgInterval.value),
    });
    settingsStatus.textContent = "已保存";
    settingsStatus.dataset.tone = "ok";
    await loadState();
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
        Authorization: `Bearer ${state.token}`,
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
    await loadState();
  } catch (error) {
    bgStatus.textContent = errorText(error, "上传失败");
    bgStatus.dataset.tone = "error";
  } finally {
    bgUpload.disabled = false;
  }
}

async function search(event) {
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
          await loadState();
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

function switchTab(tab) {
  state.tab = tab;
  for (const button of document.querySelectorAll(".admin-tabs button")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  for (const section of document.querySelectorAll(".admin-tab")) {
    section.classList.toggle("hidden", section.id !== `tab-${tab}`);
  }
}

adminLoginBtn.addEventListener("click", login);
adminToken.addEventListener("keydown", (event) => {
  if (event.key === "Enter") login();
});
adminLogout.addEventListener("click", logout);
adminSkip.addEventListener("click", () => adminAction("/api/admin/skip", "已切歌"));
adminPause.addEventListener("click", () => adminAction("/api/admin/pause", "已暂停"));
adminResume.addEventListener("click", () => adminAction("/api/admin/resume", "已恢复"));
adminClearQueue.addEventListener("click", () =>
  adminAction("/api/admin/clear-queue", "已清空队列")
);
saveSettings.addEventListener("click", savePageSettings);
bgUpload.addEventListener("click", uploadBackground);
adminSearchForm.addEventListener("submit", search);
for (const button of document.querySelectorAll(".admin-tabs button")) {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
}

state.token = sessionGet("radio.adminToken") || "";
if (state.token) {
  showPanel();
  loadState();
} else {
  showLogin();
  setStatus("offline", "未登录");
}
