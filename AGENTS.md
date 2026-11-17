# AGENTS.md

## 项目

Radio on Stick：运行在 Debian 12 / aarch64 / 约 400MB 内存设备上的轻量 web 电台。服务端维护状态、队列、留言、歌词和 SQLite；音频由服务器 `/source` 共享会话取得，经两个种子浏览器的 WebRTC DataChannel 网状转发；音频默认由 ffmpeg 转码为低码率 MP3；无 WebRTC 或转码失败时退回 HTTP 叶子或服务器 `/native` 原生流，浏览器不直连第三方音频。

## 硬性约束

- Python 3.11+，**只用标准库**，不要引入 pip 依赖。
- 第三方音频不落盘；音频默认由 ffmpeg 转码为低码率 MP3（`RADIO_TRANSCODE_MP3=0` 时 MP3 直通）；用户主动上传的本地音频临时存 `data/uploads/`，需保持可清理且不入 git。
- 不使用 Icecast/Liquidsoap/Docker。
- 前端不引入外部 CDN、字体或框架。
- 不把 token、环境文件、数据库、日志、音频、背景图提交到 git。
- 用户内容一律用 `textContent` 渲染，禁止 `innerHTML`/`eval`。
- 上游 URL 必须保持 http/https 校验。

## 常用命令

```bash
# 本地运行
RADIO_ADMIN_TOKEN=dev-token RADIO_IP_SALT=dev-salt python3 -m app

# 检测/安装 ffmpeg（支持 apt/dnf/yum/pacman/apk/zypper/brew/port/winget/choco/scoop）
python3 deploy/ensure_ffmpeg.py --check

# 测试（不访问网络；转码测试需要 ffmpeg）
python3 -m unittest discover -s tests -t . -v

# 打包部署
bash deploy/package.sh
```

## 目录

```text
app/            Python 服务与 static 前端
app/sources/    网易/TXQQ/meting 与聚合
deploy/         Nginx、systemd、安装、TLS、打包
tests/          unittest、响应夹具与可选浏览器 E2E
tools/          radioctl CLI
docs/TODO.md    路线图
data/           运行时数据（gitignored）
```

## 关键实现

- `app/player.py`：播放头、队列、轮转、投票后的切歌、后台音频解析。
- `app/httpd.py`：HTTP API、静态文件、管理接口、背景/歌词限频。
- `app/sources/aggregate.py`：跨源搜索、候选缓存、元数据/音频/封面/歌词聚合。
- `app/static/app.js`：轮询 `/now.json`、直连/网状播放切换、封面虚化背景、明暗主题、封面模式/完整歌词切换、聊天页、图标抽屉（点歌/待播放/历史）、本地歌词对齐、留言/投票/置顶。
- `app/stream.py`：MP3 帧头解析、ffmpeg 转 MP3、共享 `StreamSession`、内存环形缓冲；第三方流只读、不落盘；上传文件按本地文件只读。
- `app/uploads.py`：上传音频的生命周期清理（默认播放结束即删除；遗留模式可配置上传者离线宽限；硬保留期清理；只删除应用命名的临时文件）。
- `app/mesh.py`：标准库 RFC6455 WebSocket `/ws`、种子/父子拓扑、双上行分配、显式种子替换、不可中继叶子、断线重挂。
- `app/static/mesh.js`：种子 `/source` 取块、DataChannel 原样转发、`(stream_id, seq)` 去重、MSE 多格式播放、时间戳同步、HTTP 叶子/服务器 `/native` 兜底。
- 点歌历史按 `(IP, track_id)` 唯一，重复点歌只更新时间，不新增行；待播放请求可由本人取消，不能取消别人的请求。
- `app/static/admin.js`：`/admin` 复用客户端壳，叠加管理控制台（队列/曲目库/策略/页面背景/留言）。
- `app/policy.py`：上传/第三方 MP3 限制、网络白名单、最大听众、点歌/上传/留言开关。
- `deploy/nginx-radio.conf`：TLS、反向代理、限流、私网访问控制。

## 修改时

1. 先读相关模块和测试。
2. 保持小内存、无阻塞网络 I/O；不要在状态锁内做上游请求。
3. 新增 API 时同步更新 README、管理端和测试。
4. 变更部署配置时同步更新 `deploy/` 和 `deploy/README.md`。
5. 提交前运行 unittest；不要提交 `data/`、`dist/`、`.env` 或证书私钥。

## 当前已知限制

- 播放列表不循环，播完待机；靠管理员加入新曲目恢复。
- 所有音频默认由 ffmpeg 转为低码率 MP3 后走 MSE/网状；`RADIO_TRANSCODE_MP3=0` 时 MP3 直通；转码失败才由服务器 `/native` 兜底；不直连第三方。
- 稳定状态下子节点必须有 2 个中继父节点；只有父节点切换窗口允许 1 个，9 秒内补不齐会转 HTTP 叶子。服务器直连种子最多 2 个，显式 `?role=seed` 的新节点可替换已有种子。
- 浏览器或网络禁止 WebRTC 时自动转为 HTTP 直连叶子（不是 P2P 子节点）；跨公网可配置 `RADIO_MESH_STUN`。
- `?role=leaf` / `?relay=0` 的节点不能被选为种子或父节点；它们只作为叶子接收。
- 本地上传临时保存在 `data/uploads/`，默认播放结束即删除；播放中的文件不清理，且只删除应用生成的 UUID 临时文件，不碰其他文件。上传音频不会提交到 git。
- `RADIO_HTTP_LEAF_MAX` 限制无 WebRTC 的 HTTP 叶子数量；所有音频都由服务器转发，浏览器不直连第三方。默认 ffmpeg 统一转 MP3 后走 P2P/HTTP 叶子。
- 自签名 TLS 证书会触发浏览器警告；替换 `/etc/radio-on-stick/tls/` 后 reload Nginx。
- 估算时长曲目仍接受客户端 `ended`，但有最短播放时间限制。
- 转码会为当前曲目占用一个 ffmpeg 进程；弱 CPU 设备应降低 `RADIO_TRANSCODE_BITRATE` 或设置 `RADIO_TRANSCODE_MP3=0`。
- 部分平台/网络可能限制第三方音源。
