# 私人部署配置

源码只保留通用代码和示例。私人配置分三层，不要写进仓库。

## 1. 环境变量 / 环境文件

```text
RADIO_ADMIN_TOKEN
RADIO_IP_SALT
RADIO_HOST
RADIO_PORT
RADIO_DATA_DIR
RADIO_FFMPEG
```

Linux 安装包使用：

```text
/etc/radio-on-stick.env
```

本地运行使用 shell 环境变量。这些值包含管理凭证和密钥，不能提交。

## 2. 部署层配置

这些属于 Nginx / 系统 / TLS，不由网页管理：

- Nginx 监听端口、`server_name`
- 校园网/办公室 IP 白名单
- TLS 证书和私钥
- Cloudflare Tunnel / DNS
- systemd 服务
- 本机校园 IPv4/IPv6 地址

它们应放在：

```text
data/campus/
/etc/nginx/
/etc/radio-on-stick/
```

`data/` 已在 `.gitignore` 中；`docs/CAMPUS.md`、校园 CA、Mac 启动脚本也是本地文件，不进入 PR。

## 3. 管理页面可配置

登录 `/admin` 后可以配置：

- 电台名称、副标题、公告
- 页面标题、背景轮换
- 背景图库
- 点歌、上传、留言、MP3 限制
- 最大听众
- 公开访问地址、校园内网地址、备用隧道地址、访问说明

最后四项在「访问与部署」标签页，仅管理端可见，用于记录和复制部署信息，不会自动修改 Nginx、TLS、隧道或防火墙。

管理 token 是启动凭证，必须继续放在环境文件中，不能由管理页面读取或修改。

## 提交 PR 前

```bash
git status --short
git diff --cached --name-only
```

确认没有：

```text
.env
data/
dist/
*.key
*.pem
docs/CAMPUS.md
docs/radio-campus-ca.crt
deploy/macos-campus-*.sh
deploy/refresh-cernet-allow.py
```
