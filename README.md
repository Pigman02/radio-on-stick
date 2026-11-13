# Radio on Stick

一个跑在小内存 Wi-Fi 棒子上的轻量 web 电台。服务端只维护播放状态、队列、留言和 SQLite；音频由浏览器直连第三方链接，**不存储音频、不解码、不转码、不使用 Icecast/Liquidsoap/Docker**。

## 当前功能

- 纯标准库 Python 状态/API 服务，SQLite WAL
- 网易云 / TXQQ / meting 字段聚合，搜索来源可扩展
- 播放列表顺序播放，**播完即停止，不循环**
- 伪同步 `/now.json`：封面、时长、播放进度、歌词
- 点歌与留言拆分
  - 点歌：搜索 → 结果列表 → 选择 → 确认入队
  - 留言：独立输入，不绑定歌曲
- 昵称首次登录设置，保存在浏览器 `localStorage`
- 一起听人数、切歌投票（达到 50% 自动切歌）
- 点歌与播放列表合并展示，正在播放高亮，支持搜索切换点歌
- 留言区为直播评论风格，从下往上滚动，底部通栏并可折叠
- 点歌队列置顶：每个听众每首可顶一次，顶数越多越靠前
- 独立管理页 `/admin`：列表式导航、切歌、暂停/恢复、进度条、清空队列、置顶、加入播放列表、修改页面文字、上传背景、隐藏留言
- 背景图片随机轮换，可在管理页上传/删除
- 暗色响应式 WebUI

## 快速开始

要求 Python 3.11+。不需要安装 pip 包。

PowerShell：

```powershell
cd radio-on-stick
$env:RADIO_ADMIN_TOKEN = "dev-token"
$env:RADIO_IP_SALT = "dev-salt"
python -m app
```

Linux / macOS：

```bash
cd radio-on-stick
RADIO_ADMIN_TOKEN=dev-token RADIO_IP_SALT=dev-salt python3 -m app
```

打开 <http://127.0.0.1:8080>。

首次进入会要求设置昵称。点歌时先搜索，选中结果后确认；留言在独立输入框发送。管理页是独立页面，访问 <http://127.0.0.1:8080/admin>，输入 `RADIO_ADMIN_TOKEN`。主页面不再显示管理入口。

添加第一首歌也可以直接用 CLI：

```bash
python tools/radioctl.py --token dev-token add "Iron Lotus Mili"
python tools/radioctl.py now
```

## 配置

所有配置都来自环境变量；页面文字和背景轮换间隔可以在 `/admin` 管理页里覆盖。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RADIO_HOST` | `127.0.0.1` | Python 监听地址，生产环境应只给 Nginx 访问 |
| `RADIO_PORT` | `8080` | Python 监听端口 |
| `RADIO_DATA_DIR` | `./data` | 数据目录，背景图在 `data/backgrounds` |
| `RADIO_DB_PATH` | `./data/radio.db` | SQLite 路径 |
| `RADIO_STATION_NAME` | `我的电台` | 默认电台名称 |
| `RADIO_STATION_SUBTITLE` | `分享一首歌，留一句话` | 默认副标题 |
| `RADIO_PAGE_TITLE` | 空 | 默认页面标题，空则使用电台名称 |
| `RADIO_NOTICE` | 空 | 默认公告 |
| `RADIO_ADMIN_TOKEN` | 空 | 管理 API token；为空时管理接口禁用 |
| `RADIO_IP_SALT` | 开发默认值 | IP 哈希盐，生产环境务必修改 |
| `RADIO_REQUEST_PER_MIN` | `5` | 同一 IP 每分钟最多点歌数，超过才限制 |
| `RADIO_QUEUE_MAX` | `20` | 全局待播队列上限 |
| `RADIO_DUPLICATE_WINDOW` | `1800` | 同曲去重窗口秒数 |
| `RADIO_MESSAGE_MAX` | `200` | 留言最大长度 |
| `RADIO_MESSAGE_PER_MIN` | `10` | 同一 IP 每分钟最多留言数，超过才限制 |
| `RADIO_NICK_MAX` | `16` | 昵称最大长度 |
| `RADIO_MIN_FORM_MS` | `1200` | 表单最短填写时间，挡简单脚本 |
| `RADIO_REPORT_PER_MIN` | `10` | 播放失败上报频率 |
| `RADIO_SEARCH_PER_MIN` | `20` | 同一 IP 每分钟搜索次数 |
| `RADIO_SEARCH_LIMIT` | `5` | 每个音源每次取的条数 |
| `RADIO_SEARCH_RESULTS` | `12` | 合并后的搜索结果上限 |
| `RADIO_SEARCH_CACHE_TTL` | `900` | 搜索候选缓存秒数 |
| `RADIO_BG_INTERVAL` | `300` | 背景随机轮换间隔秒数 |
| `RADIO_BG_MAX_BYTES` | `8388608` | 背景图片大小上限 |
| `RADIO_LISTENER_TTL` | `30` | 听众在线判定窗口秒数 |
| `RADIO_BANNED_WORDS` | 空 | 逗号分隔的屏蔽词 |
| `RADIO_DEFAULT_DURATION_MS` | `240000` | 无时长元数据时的默认时长 |
| `RADIO_LOG_LEVEL` | `INFO` | 日志级别 |

点歌和留言使用 60 秒滑动窗口：同一 IP 每分钟最多点 5 首、留言 10 条，第 6 首/第 11 条才会被限制，不会第一次操作就锁死。

## 数据聚合与搜索

```text
搜索关键词
  ├─ 网易搜索 → id / 歌名 / 歌手 / 专辑 / 时长 / 封面 picId
  ├─ TXQQ 搜索 → songid / 音频 URL / 封面 / LRC
  └─ 合并为候选结果
       ├─ 音频：网易 meting type=url 为主，QQ 音频为备用直连
       ├─ 封面：网易 picId → meting type=pic，失败用 QQ 封面
       ├─ 歌词：网易 lyric API（原文 + 翻译），失败用 meting / QQ LRC
       └─ 时长：网易 duration；QQ-only 用歌名+歌手回查网易，再失败用默认值
```

用户点歌时提交的是服务端生成的 `candidate_id`，不会直接信任客户端传音频 URL。搜索结果在内存缓存，过期后提示重新搜索。

### 增加搜索来源

音源适配器需要提供：

- `name`：内部标识
- `display_name`：前端显示名称
- `search(keyword, limit)`：返回 `source / source_id / title / artist / album / duration_ms / cover_url / audio_url / lyrics_lrc` 等字段

然后在 `app/app.py` 或启动时调用：

```python
aggregator.register_source(MySource(...))
```

搜索结果和候选解析会自动使用新来源。

## 播放行为

- 播放列表按加入顺序播放，队列里的点歌优先。
- **播放列表放完后不会回到第一首**；如果没有待播队列，电台进入待机。
- 待机时添加新曲目，会自动从新曲目开始。
- 音频播放失败会自动尝试备用直连；仍失败时上报 `/api/report` 触发重新解析。
- `/now.json` 会记录在线听众；人数按唯一 IP 计算，同一设备的多个标签页只算一个人。
- 切歌投票按当前在线人数计算，达到 50% 立即切到下一首。
- 队列项支持听众置顶，每个听众对同一项只能顶一次，按顶数降序播放。
- 启动时会清理已播放/已拒绝的队列历史，避免无效缓存累积。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/now.json` | 当前曲目、位置、音频直连/备用、下一首、队列数 |
| GET | `/api/config` | 页面文字、背景列表、轮换间隔 |
| GET | `/api/state` | 播放状态 + 队列 + 最近留言 |
| GET | `/api/search?q=` | 搜索候选，返回 `candidate_id` |
| GET | `/api/lyrics/<track_id>` | LRC 原文与翻译 |
| GET | `/api/messages` | 最近留言 |
| POST | `/api/messages` | 发送留言 |
| POST | `/api/request` | 点歌：`candidate_id` 或兼容的 `query` |
| POST | `/api/vote-skip` | 投票切歌，达到 50% 自动切换 |
| POST | `/api/queue/pin` | 队列项置顶，每个听众每项一次 |
| POST | `/api/report` | `ended` / `error` / `stalled` 上报 |
| GET | `/healthz` | 健康检查 |
| GET | `/api/admin/state` | 管理面板状态 |
| POST | `/api/admin/skip` | 切歌 |
| POST | `/api/admin/pause` | 暂停电台 |
| POST | `/api/admin/resume` | 恢复电台 |
| POST | `/api/admin/track` | 加入播放列表（`candidate_id` 或 `query`） |
| POST | `/api/admin/clear-queue` | 清空待播队列 |
| POST | `/api/admin/settings` | 修改页面文字与背景轮换间隔 |
| POST | `/api/admin/hide-message` | 隐藏留言 |
| POST | `/api/admin/background` | 上传背景（原始图片字节） |
| POST | `/api/admin/background/delete` | 删除背景 |

管理接口需要 `Authorization: Bearer <RADIO_ADMIN_TOKEN>`。

## 项目结构

```text
radio-on-stick/
├─ app/
│  ├─ __main__.py        # 入口
│  ├─ app.py             # 依赖装配
│  ├─ config.py          # 环境变量配置
│  ├─ db.py              # SQLite schema 与访问层
│  ├─ httpd.py           # HTTP 路由/API/静态文件/背景
│  ├─ player.py          # 播放头与队列调度（不循环）
│  ├─ ratelimit.py       # 点歌/留言/搜索/上报限频
│  ├─ settings.py        # 页面文字设置
│  ├─ upstream.py        # urllib 客户端
│  ├─ sources/           # 网易 / QQ / meting / 聚合
│  └─ static/            # 主页面 + /admin 独立管理页
├─ tests/                # unittest + 真实响应夹具
├─ tools/radioctl.py     # 管理 CLI
└─ data/                 # SQLite、背景图（gitignore）
```

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

测试不访问网络，使用 `tests/fixtures` 中的真实响应结构。

## 目标设备与部署

目标设备实测为 Debian 12 bookworm aarch64、4 核、401MB 内存、根分区可用约 1.9GB。Nginx 只负责静态文件、反向代理和限流；Python 服务建议只监听 `127.0.0.1`。

P6 部署文件（Nginx 配置、systemd、安装脚本）尚未加入。部署时应：

- 只安装 `nginx`，不装 pip 包
- Nginx 监听 80，允许 Tailscale `100.64.0.0/10` 和私网段
- `/now.json`、`/api/report` 关闭 access log
- systemd `MemoryMax` 限制在 150MB 左右
- journald 限制日志大小
- 修改 SSH 密码并加 SSH key

## 说明

- 音频链接来自第三方，接口和链接时效不受本项目控制。
- 本项目不存储音频文件，只保存链接、元数据、歌词文本、留言和背景图片。
- MVP 不做 Nginx 音频代理；`audio.alternates` 为直连兜底。后续可加 `secure_link` 流式反代。
- 第三方音源的使用请自行确认合规性。
