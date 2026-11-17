# APP / PWA 预留与客户端兼作服务器

## 当前可用的 APP 基础

- `/manifest.webmanifest`：可安装的 PWA 清单，支持独立窗口、任意横竖屏。
- `/sw.js`：仅缓存页面壳和静态资源，**不缓存** `/api/`、`/source/`、`/native/`、`/media/`、`/backgrounds/`、`/ws`。
- 前端没有 npm、CDN 或框架依赖，适合直接放进 WebView / Tauri / Capacitor / BeeWare 容器。
- `theme-color`、Apple mobile web app 元数据已加入页面。

## 打包建议

### 方案 A：远程 WebView（最简单）

APP 只作为 WebView 容器，启动时加载部署好的 HTTPS 地址：

```text
https://radio.example.com/
```

优点：APP 不需要内置 Python、ffmpeg 或数据库；升级服务器即可更新全部客户端。
注意：必须使用 HTTPS 才能稳定使用 PWA/Service Worker/麦克风之外的现代能力。

### 方案 B：本机内置服务器

把现有 Python 服务嵌入 APP（BeeWare、Chaquopy 或自带 Python runtime），APP 启动时：

```bash
RADIO_HOST=0.0.0.0 RADIO_PORT=8080 RADIO_DATA_DIR=... python3 -m app
```

然后 WebView 加载 `http://127.0.0.1:8080/`。
这就是“APP 客户端同时作为服务器”：手机在局域网内提供页面、队列、音频与 P2P 信令入口。
需要额外内置 Python 3.11、ffmpeg（按目标平台）和可写数据目录；当前仓库不包含原生 APK/IPA 工程。

### 方案 C：PWA 安装

用 Chrome/Edge/Safari 的“安装应用”即可获得接近原生 APP 的独立窗口体验，不需要安装包。
首次打开后浏览器会注册 Service Worker；离线时只能打开页面壳，音频和 API 仍需要服务器或局域网服务器。

## 客户端同时作为服务器 / 中继

- 当前网状音频中，普通浏览器默认就是可中继节点；每个客户端在播放时同时为下游转发 DataChannel。
- 显式让某台设备作为种子节点：

```text
https://radio.example.com/?role=seed
```

- 只接收、不中继：

```text
https://radio.example.com/?role=leaf
https://radio.example.com/?relay=0
```

- 服务器直连种子最多 2 个；新的显式 seed 可替换旧 seed。普通客户端仍可作为中继父节点。
- 因此“任何 APP 客户端同时作为服务器”在音频层已经可由 WebRTC 种子/中继承担；若要提供完整 HTTP 页面和 API，则需要方案 B 的内置 Python 服务。

## 安全边界

- 内置服务器模式必须设置 `RADIO_ADMIN_TOKEN`、`RADIO_IP_SALT`，并通过系统防火墙限制端口。
- 不要把 `data/`、`radio.db`、上传音频或 token 打进安装包。
- 服务端准入策略可在 `/admin` 的管理控制台中设置白名单、最大听众人数、点歌/上传/留言开关和 MP3 限制。
