# 前端手动调整指南

所有调整都在 `app/static/` 下：

- `style.css`：布局、间距、主题、背景、歌词、按钮
- `index.html`：结构、标签文字
- `app.js`：下一首数据、列表过滤、交互
- `sw.js`：PWA 缓存版本

改完后普通刷新即可；如果 Service Worker 仍使用旧缓存，把 `index.html` 中的 `style.css?v=` / `app.js?v=` 数字加一，同时改 `sw.js` 的 `CACHE` 版本。

## 顶栏

```css
.shell { padding-top: 8px; }          /* 顶栏整体下移 */
.topbar { padding-left: 14px; padding-right: 14px; } /* 内容内收 */
:root { --topbar-h: 66px; }            /* 顶栏高度 */
```

## 竖屏播放页

```css
@media (orientation: portrait) and (min-height: 700px) {
  .playback-page:not(.lyrics-open) {
    justify-content: center;          /* 内容整体垂直居中 */
  }
}
.track-meta { margin-top: 22px; }      /* 封面与歌名间距 */
.current-lyric { margin-top: 26px; }   /* 歌名与当前句间距 */
.cover-button { width: min(72vw, 40vh, 440px); } /* 封面大小 */
```

## 横屏播放页

```css
@media (orientation: landscape) {
  .cover-button { width: min(34vw, 48vh, 470px, calc(100dvh - 270px)); }
  .cover-column { transform: translateY(-2.5vh); } /* 左半视觉中心上移 */
  .lyrics-panel {
    padding-left: clamp(18px, 3.8vw, 58px);  /* 歌词右移 */
    padding-right: clamp(18px, 4.2vw, 66px);
  }
}
```

## 进度条与播放控件

进度条在 DOM 中位于时间上方：

```html
<div class="dock-progress">
  <div class="progress">...</div>
  <div class="time-row">...</div>
</div>
```

控件间距：

```css
.dock-controls { min-height: 70px; align-items: center; }
.center-controls { gap: clamp(18px, 4vw, 34px); }
```

## 下一首信息

位置：

- 竖屏：`#nextTrackInfo` 位于当前句歌词下方
- 横屏：同一元素位于歌名/歌手/专辑下方

```css
.next-track-info {
  width: min(72vw, 40vh, 440px);
  margin-top: clamp(18px, 2.4vh, 26px);
}
.next-track-info img { width: 46px; height: 46px; }
```

## 完整歌词

控制可见行数和当前句放大：

```css
.lyrics-panel .lyric-line {
  font-size: clamp(20px, 5.6vw, 29px);
  line-height: 1.4;
  padding: 10px 8px;
  opacity: 0.34;
}
.lyrics-panel .lyric-line.active {
  opacity: 1;
  font-size: 1.16em;
  font-weight: 700;
  transform: translateX(3px);
}
```

行数太多时把 `font-size` 调大或把 `padding-top/bottom` 调大；行数太少则反向调整。

## 二级菜单搜索框

```css
.search-input-wrap input {
  padding-left: 48px;   /* 给左侧取消键留空间 */
  padding-right: 44px;  /* 给右侧搜索键留空间 */
}
.search-input-wrap .search-cancel { left: 4px; }
.search-input-wrap #searchBtn { right: 4px; }
```

搜索栏与选项卡之间的空行已删除：

```css
.drawer-search { padding-bottom: 0; border-bottom: 0; }
.drawer-switch { border-top: 1px solid var(--line); padding-top: 0; }
```

## 待播放 / 历史点歌标签

```css
.drawer-switch-btn { flex: 1 1 50%; height: 39px; }
.drawer-switch-btn.active::after {
  left: 22%; right: 22%; height: 3px; background: var(--accent);
}
```

待播放只显示 `status === "pending"` 的请求；播放列表轮播曲不再混入待播放，历史记录只在历史点歌标签中。

## 背景压暗

```css
.bg-layer.local { filter: brightness(0.56) saturate(1.05); }
.bg-layer.cover { filter: blur(28px) brightness(0.42) saturate(1.14); }
.backdrop {
  background: linear-gradient(180deg, rgba(3,6,12,.56), rgba(3,6,12,.78));
}
```

想让自定义背景更亮：调高 `.bg-layer.local` 的 brightness；想更暗则调低。

## 主题作用范围

浅色主题只改变：

- `.player-dock`
- `.drawer`

主页面始终使用深色文本变量；不要直接修改 `html[data-theme="light"]` 的全局 `--text`，否则会影响主播放页。

## 按钮边框

全局约定：

```css
button, .icon-btn, .play-btn, .row-action, .drawer-switch-btn {
  border: 0 !important;
}
```

当前搜索键也没有边框，所有按钮只留图标。

## 测试图

运行时截图中的彩色封面是一张真实音乐会照片，通过本地测试 HTTP 服务加载，不是生图；正式部署时使用真实歌曲封面。

## 本次新增/修正的定位

### 歌词当前句

当前句字号必须用绝对值，不能用 `1.16em`；`em` 会相对父级计算，曾导致当前句比普通行还小。

```css
.lyrics-panel .lyric-line { font-size: clamp(20px, 5.6vw, 29px); }
.lyrics-panel .lyric-line.active { font-size: clamp(23px, 6.2vw, 32px); }
```

当前句始终居中，列表上下留白：

```css
.lyrics-panel .lyrics { padding-top: 45vh; padding-bottom: 45vh; }
```

打开完整歌词时用即时居中，之后换句才用平滑滚动；见 `setLyricsOpen()` 和 `refreshCurrentLyric(true)`。

### 二级菜单动画

```css
.drawer { transform: translateY(102%); transition: transform .38s ...; }
.drawer.is-open { transform: translateY(0); }
@media (orientation: landscape) {
  .drawer { transform: translateX(-108%); }
  .drawer.is-open { transform: translateX(0); }
}
```

关闭后 380ms 才加 `hidden`，并在此时重置搜索/回到待播放。

### 成功提示

```css
.toast { ... border-radius: 999px; ... }
.toast.show { opacity: 1; }
```

`showToast(text)` 默认 2 秒后消失。

### 搜索/留言胶囊

```css
#searchInput, #messageInput { border-radius: 999px !important; }
```

### 输入框内部留白

```css
.search-input-wrap input {
  padding-left: 48px;
  padding-right: 44px;
}
```

### 无缝切歌（直连兜底）

- 下一首会提前预载到另一个 `<audio>`（`#audioB`）。
- 手动切歌时，如果预载完成，会在旧音频上交叉淡入新音频，然后再停止旧 P2P 流。
- 预载是否完成由 `state.preloadAudio.readyState >= 2` 判断。
- 相关函数：`preloadNext()`、`adoptPreloaded()`、`fadeToNewAudio()`。

### 上传音频元数据

后端 `app/media_meta.py`：

- `ffprobe` 读取 ID3/Vorbis/MP4 标签
- `ffmpeg` 提取内嵌封面
- ID3 解析器作为无 ffprobe 时的兜底
- 无内嵌歌词时通过 `TrackAggregator.lookup_lyrics()` 在线查找
- 封面写在 `data/uploads/<uuid>.jpg`，随上传曲目清理

## 16 项调整定位

- 副标题：`.topbar .brand` 改为纵向，`.station-subtitle` 显示；页面标题统一取 `station_name`。
- 搜索框左内边距：`#searchInput { padding-left: 50px !important; }`；取消键透明、圆形。
- 播放页共用列宽：`.cover-button / .track-meta / .current-lyric / .next-track-info` 使用同一个 width。
- 当前句自动定位：`lyricsScroll.clientHeight * 0.42`；手动滚动后 5 秒再自动居中（`markLyricsManualScroll()`）。
- 搜索右侧固定列：`.search-result` 的第三列 112px，来源标签不再推动“+”。
- 二级菜单取消键只调用 `cancelSearch()`，不关闭抽屉。
- 所有图标按钮和行内操作按钮：`border-radius: 50%`。
- 留言发送键：`.message-form` 相对定位，`.send-btn` 绝对定位在输入框内部。
- 留言成功只保留绿色小字；点歌成功只保留 2 秒胶囊提示。
- 服务端跳过曲目提前量：`RADIO_TRACK_PREROLL_MS=3000`；客户端轮询间隔 1.5s。
