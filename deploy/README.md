# 部署说明

目标环境：Debian 12（bookworm）aarch64，约 400MB 内存，根分区剩余约 1.9GB。
部署只需要 `nginx` 和系统自带 `python3`（3.11+），不需要 pip、Docker、ffmpeg、Icecast。

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
- 设置 journald 日志上限 32MB

安装完成会打印管理 token。管理页：`http://<设备IP>/admin`。

## 首次检查

```bash
systemctl status radio-on-stick
journalctl -u radio-on-stick -n 50 --no-pager
curl -s http://127.0.0.1:8080/healthz
curl -sI http://127.0.0.1/
```

从 Tailscale 或局域网设备打开主页，设置昵称，搜索并点歌。

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
  --base http://127.0.0.1:8080 \
  --token '<管理 token>' \
  add 'Iron Lotus Mili'
```

## 备份

SQLite 数据库在 `/var/lib/radio-on-stick/radio.db`，背景图在 `/var/lib/radio-on-stick/backgrounds/`。
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

- 管理 token 走明文 HTTP（Tailscale 本身加密）；如非 Tailscale 环境，请加 TLS。
- Nginx 已覆盖 `X-Real-IP`，不要让 Python 端口暴露到局域网。
- 建议修改 SSH 密码、增加 SSH key，并只允许 Tailscale/内网访问。
- 401MB 设备上不要同时运行其他重型服务。

## 卸载

```bash
sudo bash deploy/uninstall.sh          # 移除应用和服务，保留数据
sudo bash deploy/uninstall.sh --purge  # 连同数据、环境文件一起删除
```
