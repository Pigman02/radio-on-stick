# 公开运行检查清单

## 可以直接运行的范围

当前默认配置可以直接用于：

- 本机
- 局域网
- Tailscale / WireGuard 等私有网络

Nginx 默认只放行 `127.0.0.1`、Tailscale 和 RFC1918 私网，其他公网地址会被 `deny all` 拒绝。

## 公网开放前必须完成

1. **域名与 DNS**：A/AAAA 记录指向服务器。
2. **正式 TLS 证书**：替换自签名证书：
   - `/etc/radio-on-stick/tls/fullchain.pem`
   - `/etc/radio-on-stick/tls/privkey.pem`
   然后 `sudo systemctl reload nginx`。推荐 Let's Encrypt。
3. **修改准入白名单**：编辑 `/etc/nginx/snippets/radio-allow.conf`。默认只有私网；如果真的要公开，需要添加允许的公网地址或调整 `deny all`。该文件升级时保留。
4. **服务器防火墙**：只开放 80/443，Python 服务继续只监听 `127.0.0.1:8080`。
5. **强随机密钥**：
   - `RADIO_ADMIN_TOKEN`
   - `RADIO_IP_SALT`
6. **管理端策略**：
   - 设置最大听众人数
   - 视情况关闭上传、点歌、留言
   - 开启 MP3 限制
7. **资源评估**：ffmpeg 转码会占用 CPU；上行带宽和 HTTP 叶子数量需要压测。
8. **第三方音源合规性**：公开运行前确认音源授权、版权和当地法规。
9. **备份**：`/var/lib/radio-on-stick/radio.db`、背景和上传临时目录策略。

## 推荐做法

优先使用 Tailscale / 私有网络 + 白名单，不直接把管理端和上传接口暴露给全网。

## 检查命令

```bash
curl -I https://你的域名/
curl https://你的域名/healthz
curl -H 'Authorization: Bearer <token>' https://你的域名/api/admin/state
nginx -t
systemctl status radio-on-stick
```
