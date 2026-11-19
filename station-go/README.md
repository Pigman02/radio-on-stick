# Radio Station Go

Go Station 重构的第一个可运行模块：本地资料库。

当前独立运行在 `127.0.0.1:8091`，不接管现有 Python 电台。

## 能力

- 扫描管理员配置的本地音乐目录
- ffprobe 读取标题/歌手/专辑/时长
- 文件名兜底
- 内存索引
- 搜索
- Range 音频流

## 环境

```text
RADIO_GO_ADDR=127.0.0.1:8091
RADIO_LIBRARY_DIRS=/path/to/music:/another/path
RADIO_GO_ADMIN_TOKEN=<scan token>
```

`RADIO_LIBRARY_DIRS` 使用系统路径分隔符；macOS/Linux 是 `:`。

## API

```text
GET  /healthz
GET  /api/library?limit=20
GET  /api/library/search?q=test&limit=20
GET  /api/library/track/{id}
GET  /api/library/stream/{id}
POST /api/admin/library/scan
```

扫描需要：

```http
Authorization: Bearer <RADIO_GO_ADMIN_TOKEN>
```

## 构建

```bash
cd station-go
go test ./...
go build -trimpath -ldflags="-s -w" -o radio-station-go ./cmd/station
```

## 尚未实现

- 播放队列
- 播放头/同步
- P2P mesh
- `/source`、`/ws`、`/native`
- Station 注册/Directory 接入
- 管理端页面
- 曲目封面/歌词
