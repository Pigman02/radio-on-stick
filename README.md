# Radio on Stick

运行在小内存 Debian 设备上的轻量 web 电台。浏览器直连第三方音频链接；不存储音频、不解码、不转码，不使用 Icecast/Liquidsoap/Docker。

## 功能

- 纯标准库 Python + SQLite（WAL），无 pip 依赖
- 网易云 / TXQQ / meting 字段聚合，搜索源可扩展
- 播放列表顺序播放，队列优先，播完即停，不循环
- `/now.json` 伪同步：封面、时长、进度、歌词
- 点歌与留言分离；点歌为搜索 → 选择 → 确认
- 点歌历史按 IP 保存；重复点歌只保留最新一条；播放列表/历史记录可切换并重新点歌
- 昵称保存在浏览器 localStorage
- 一起听人数、50% 切歌投票、队列置顶
- 留言区为直播评论样式，底部通栏、从下往上滚动、可折叠
- `/admin` 独立管理页：切歌、暂停/恢复、进度、队列、页面文字、背景上传与轮换
- 背景随机轮换，支持本地图片和网络图床 URL
- 客户端提前预载下一首；歌词跟随本地播放进度，不再强行对齐服务器
- 移动端后台降低轮询与渲染频率；Media Session 支持系统状态栏/锁屏显示歌曲
- Nginx 提供 HTTP/HTTPS（自签名证书，可替换）

## 快速开始

Python 3.11+，不需要安装 pip 包。

```powershell
cd radio-on-stick
$env:RADIO_ADMIN_TOKEN = "dev-token"
$env:RADIO_IP_SALT = "dev-salt"
python -m app
```

```bash
# Linux / macOS
RADIO_ADMIN_TOKEN=dev-token RADIO_IP_SALT=dev-salt python3 -m app
```

打开 <http://127.0.0.1:8080>，管理页 <http://127.0.0.1:8080/admin>。

首次进入设置昵称；点歌先搜索再确认。也可以直接用 CLI：

```bash
python tools/radioctl.py --token dev-token add "Iron Lotus Mili"
python tools/radioctl.py now
```

## 配置

配置通过环境变量。最少设置：

```bash
RADIO_ADMIN_TOKEN=你的管理token
RADIO_IP_SALT=随机字符串
```

页面文字、背景和轮换间隔可在 `/admin` 覆盖。完整示例见 `deploy/radio.env.example`，默认值见 `app/config.py`。

## 行为

- 点歌提交服务端生成的 `candidate_id`，不信任客户端音频 URL。
- 播放列表播完后进入待机；再次添加新曲目会继续播放。
- 音频直连优先，失败后尝试备用链接；仍失败时上报并重新解析。
- 播放器会在当前曲目剩余约 45 秒时预载下一首，并预取封面/歌词。
- 歌词高亮和进度条使用本地 `audio.currentTime`，音画以实际播放为准。
- 一起听人数按唯一 IP 统计；投票达到在线人数 50% 自动切歌。
- 队列置顶每人每首一次，顶数越多越靠前。
- 新增音源：实现 `name`、`display_name`、`search(keyword, limit)`，再调用 `aggregator.register_source(...)`。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/now.json` | 当前曲目、进度、音频、下一首、队列、投票 |
| GET | `/api/config` | 页面文字、背景、轮换间隔 |
| GET | `/api/state` | 状态 + 队列 + 留言 + 点歌历史 |
| GET | `/api/search?q=` | 搜索候选 |
| GET | `/api/lyrics/<track_id>` | 歌词与翻译 |
| GET | `/api/messages` | 最近留言 |
| POST | `/api/messages` | 发送留言 |
| POST | `/api/request` | 点歌（`candidate_id`、`query` 或历史 `track_id`） |
| POST | `/api/vote-skip` | 投票切歌 |
| POST | `/api/queue/pin` | 队列置顶 |
| POST | `/api/report` | `ended` / `error` / `stalled` 上报 |
| GET | `/healthz` | 健康检查 |
| GET | `/api/admin/state` | 管理状态 |
| GET | `/api/admin/queue` | 管理队列 |
| POST | `/api/admin/skip` | 切歌 |
| POST | `/api/admin/pause` | 暂停 |
| POST | `/api/admin/resume` | 恢复 |
| POST | `/api/admin/track` | 加入播放列表 |
| POST | `/api/admin/clear-queue` | 清空队列 |
| POST | `/api/admin/settings` | 修改页面文字 |
| POST | `/api/admin/hide-message` | 隐藏留言 |
| POST | `/api/admin/background` | 上传背景 |
| POST | `/api/admin/background/delete` | 删除本地上传背景 |
| POST | `/api/admin/background/url` | 添加网络图床背景 |
| POST | `/api/admin/background/url/delete` | 删除网络背景 |

管理接口需要 `Authorization: Bearer <RADIO_ADMIN_TOKEN>`。

## 结构

```text
app/            # 服务端与前端资源
deploy/         # Nginx、systemd、安装、TLS、打包
docs/           # 路线图
tests/          # unittest 与响应夹具
tools/          # radioctl CLI
.github/        # GitHub Actions
AGENTS.md       # 后续开发约定
data/           # 运行时数据（gitignore）
```

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

## 部署

```bash
bash deploy/package.sh
# 目标机
tar -xzf radio-on-stick-*.tar.gz
cd radio-on-stick-*
sudo bash deploy/install.sh
```

安装脚本会安装 Nginx、创建 `radio` 用户、部署到 `/opt/radio-on-stick`、数据放 `/var/lib/radio-on-stick`，生成自签名 TLS 证书并启动 systemd 服务。详细说明见 `deploy/README.md`。

- HTTP：`http://<设备IP>/`
- HTTPS：`https://<设备IP>/`（自签名，首次需手动信任）
- 替换 `/etc/radio-on-stick/tls/fullchain.pem` 和 `privkey.pem` 后执行 `sudo systemctl reload nginx` 即可换成正式证书。
- 访问白名单在 `/etc/nginx/snippets/radio-allow.conf`，升级时会保留该文件，不会被安装脚本覆盖。

## 说明

- 音频链接来自第三方，时效和可用性不受本项目控制。
- 不存音频；只保存链接、元数据、歌词、留言和背景图。
- 当前不做 Nginx 音频代理，`audio.alternates` 负责直连兜底。
- 第三方音源请自行确认合规性。
