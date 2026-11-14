# AGENTS.md

## 项目

Radio on Stick：运行在 Debian 12 / aarch64 / 约 400MB 内存设备上的轻量 web 电台。浏览器直连第三方音频链接；服务端只维护状态、队列、留言、歌词和 SQLite。

## 硬性约束

- Python 3.11+，**只用标准库**，不要引入 pip 依赖。
- 不存储音频、不解码、不转码；不要引入 ffmpeg。
- 不使用 Icecast/Liquidsoap/Docker。
- 前端不引入外部 CDN、字体或框架。
- 不把 token、环境文件、数据库、日志、音频、背景图提交到 git。
- 用户内容一律用 `textContent` 渲染，禁止 `innerHTML`/`eval`。
- 上游 URL 必须保持 http/https 校验。

## 常用命令

```bash
# 本地运行
RADIO_ADMIN_TOKEN=dev-token RADIO_IP_SALT=dev-salt python3 -m app

# 测试（不访问网络）
python3 -m unittest discover -s tests -t . -v

# 打包部署
bash deploy/package.sh
```

## 目录

```text
app/            Python 服务与 static 前端
app/sources/    网易/TXQQ/meting 与聚合
deploy/         Nginx、systemd、安装、TLS、打包
tests/          unittest 与真实响应夹具
tools/          radioctl CLI
docs/TODO.md    路线图
data/           运行时数据（gitignored）
```

## 关键实现

- `app/player.py`：播放头、队列、轮转、投票后的切歌、后台音频解析。
- `app/httpd.py`：HTTP API、静态文件、管理接口、背景/歌词限频。
- `app/sources/aggregate.py`：跨源搜索、候选缓存、元数据/音频/封面/歌词聚合。
- `app/static/app.js`：轮询 `/now.json`、直连播放、下一首预载、本地歌词对齐、播放列表/点歌历史切换、留言/投票/置顶。
- 点歌历史按 `(IP, track_id)` 唯一，重复点歌只更新时间，不新增行。
- `app/static/admin.js`：`/admin` 独立管理页。
- `deploy/nginx-radio.conf`：TLS、反向代理、限流、私网访问控制。

## 修改时

1. 先读相关模块和测试。
2. 保持小内存、无阻塞网络 I/O；不要在状态锁内做上游请求。
3. 新增 API 时同步更新 README、管理端和测试。
4. 变更部署配置时同步更新 `deploy/` 和 `deploy/README.md`。
5. 提交前运行 unittest；不要提交 `data/`、`dist/`、`.env` 或证书私钥。

## 当前已知限制

- 播放列表不循环，播完待机；靠管理员加入新曲目恢复。
- 音频直连失败依赖备用链接和重新解析，没有 Nginx 音频代理。
- 自签名 TLS 证书会触发浏览器警告；替换 `/etc/radio-on-stick/tls/` 后 reload Nginx。
- 估算时长曲目仍接受客户端 `ended`，但有最短播放时间限制。
- 部分平台/网络可能限制第三方音源。
