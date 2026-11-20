# 部署说明

目标环境：Debian 12（bookworm）aarch64，约 400MB 内存，根分区剩余约 1.9GB。
部署需要 `nginx`、系统自带 `python3`（3.11+）和 `ffmpeg`；`deploy/ensure_ffmpeg.py` 会自动检测并尝试用当前系统的包管理器安装，`deploy/install.sh` 会调用它。ffmpeg 默认把所有输入转成低码率 MP3；设置 `RADIO_TRANSCODE_MP3=0` 可让 MP3 直通以节省 CPU。不需要 pip、Docker、Icecast。

## 打包

在项目根目录运行：

```bash
bash deploy/package.sh
```

会生成 `dist/radio-on-stick-<版本>-<时间>.tar.gz` 和对应 `.sha256`。

## 安装

把压缩包传到设备，解压后运行：

```bash
tar -xzf radio-on-stick-*.tar.gz
cd radio-on-stick-*
sudo bash deploy/install.sh
```

安装脚本会：

- 安装 `nginx`（不升级全系统）
- 创建系统用户 `radio`
- 部署代码到 `/opt/radio-on-stick`
- 数据放在 `/var/lib/radio-on-stick`
- 首次安装生成 `/etc/radio-on-stick.env` 和随机管理 token
- 安装并启动 `radio-on-stick.service`
- 安装 Nginx 站点，只允许 Tailscale 和私网访问
- 生成自签名 TLS 证书到 `/etc/radio-on-stick/tls/`
- 设置 journald 日志上限 32MB
- Nginx 为 `/ws` 打开 Upgrade 透传，为 `/source/` 关闭代理缓冲

安装完成会打印管理 token。管理页：

- `https://<设备IP>/admin`（自签名证书，首次需手动信任）
- `http://<设备IP>/admin`（仍保留 HTTP）

## 首次检查

```bash
systemctl status radio-on-stick
journalctl -u radio-on-stick -n 50 --no-pager
curl -s http://127.0.0.1:8090/healthz
curl -sI http://127.0.0.1/
curl -k -sI https://127.0.0.1/
curl -s http://127.0.0.1:8090/mesh.json
```

从 Tailscale 或局域网设备打开主页，设置昵称，搜索并点歌。

打开两个设备后，`/mesh.json` 应显示两个种子；第三个及以后设备会挂到两个父节点上（种子或已有中继），稳定状态不会只保留一个父节点。播放器底部会显示 `P2P 种子`、`P2P 子节点`、`HTTP 直连 · 无 WebRTC` 或“直连”，以及缓冲/上行数。`HTTP 直连` 表示浏览器不能/没有建立 WebRTC，仍使用服务器共享流，不是 P2P 子节点；这条路径会消耗服务器出口带宽。若显示“直连”，说明当前容器不被 MSE 支持、`/source` 失败，已回退到服务器 `/native` 原生流；浏览器不会直连第三方音频。

跨公网部署时在 `/etc/radio-on-stick.env` 增加：

```bash
RADIO_MESH_STUN=stun:stun.l.google.com:19302
```

然后重启服务。同一私网/Tailscale 内通常不需要 STUN。

## 更新

重新打包并上传后，解压执行同一个安装脚本即可：

```bash
sudo bash deploy/install.sh
```

脚本会保留 `/var/lib/radio-on-stick` 和 `/etc/radio-on-stick.env`，只替换应用代码并重启服务。

## 常用运维

```bash
systemctl restart radio-on-stick
journalctl -u radio-on-stick -f
tail -f /var/log/nginx/radio.access.log
```

用 CLI 添加曲目：

```bash
python3 /opt/radio-on-stick/tools/radioctl.py \
  --base http://127.0.0.1:8090 \
  --token '<管理 token>' \
  add 'Iron Lotus Mili'
```

## 备份

SQLite 数据库在 `/var/lib/radio-on-stick/radio.db`，背景图在 `/var/lib/radio-on-stick/backgrounds/`，本地上传的点歌音频在 `/var/lib/radio-on-stick/uploads/`（临时文件，可手动清理）。
建议定期用 SQLite 备份，而不是直接复制运行中的数据库：

```bash
sudo python3 - <<'PY'
import sqlite3, time
src = sqlite3.connect('/var/lib/radio-on-stick/radio.db')
dst = sqlite3.connect(f'/var/lib/radio-on-stick/radio-backup-{int(time.time())}.db')
src.backup(dst)
dst.close(); src.close()
PY
```

## 安全提醒

- HTTPS 默认使用自签名证书；浏览器会提示，信任后可加密管理 token。
- 访问白名单在 `/etc/nginx/snippets/radio-allow.conf`；升级安装脚本会保留该文件。
- 换成正式证书：替换 `/etc/radio-on-stick/tls/fullchain.pem` 和 `privkey.pem`，执行 `sudo nginx -t && sudo systemctl reload nginx`。
- 不要把 `privkey.pem` 提交到 git。
- Nginx 已覆盖 `X-Real-IP`，不要让 Python 端口暴露到局域网。
- 建议修改 SSH 密码、增加 SSH key，并只允许 Tailscale/内网访问。
- 401MB 设备上不要同时运行其他重型服务。
- `/mesh.json` 只暴露节点 id、角色、父子关系、是否可中继和等待状态，不包含音频地址或管理 token；如不需要可删除该 location 或加访问限制。
- 网状音频只在浏览器之间走 DataChannel；服务器仍会为两个种子读取同一上游流。若浏览器或网络禁止 WebRTC，客户端会在约 9 秒后转为 HTTP 直连叶子（服务器向该客户端发送 `/source` 块），仍能播放，但不是 P2P 子节点。
- Nginx 对 `/api/upload` 的全局请求体上限为 64MB；如需调整，同时修改 `client_max_body_size` 和 `RADIO_UPLOAD_MAX_BYTES`。上传文件在曲目结束且上传者离线后（默认再等 60 秒）或 24 小时后自动清理；当前播放文件不会清理。
- 带宽有限时：所有音频都由服务器转发；MP3 优先 P2P/HTTP 叶子，AAC/WAV/FLAC 在不支持 MSE 时使用服务器 `/native` 原生流。把 `RADIO_HTTP_LEAF_MAX` 调低（0 表示禁用叶子），降低 `RADIO_UPLOAD_MAX_BYTES`，并按总带宽在 Nginx 对 `/source/`、`/media/uploads/` 设置 `limit_rate`/`limit_conn`。

## 卸载

```bash
sudo bash deploy/uninstall.sh          # 移除应用和服务，保留数据
sudo bash deploy/uninstall.sh --purge  # 连同数据、环境文件一起删除
```
