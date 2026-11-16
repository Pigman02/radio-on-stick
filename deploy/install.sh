#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 运行：sudo bash deploy/install.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

APP_DIR="/opt/radio-on-stick"
DATA_DIR="/var/lib/radio-on-stick"
ENV_FILE="/etc/radio-on-stick.env"
SERVICE="radio-on-stick"
SITE="radio-on-stick"

if [[ ! -d "${REPO_DIR}/app" ]]; then
  echo "找不到 ${REPO_DIR}/app，请在解压后的项目根目录运行 deploy/install.sh" >&2
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("需要 Python 3.11+")
PY

echo "==> 安装系统依赖（不会升级全系统）"
apt-get update
apt-get install -y --no-install-recommends nginx python3 openssl

echo "==> 创建运行用户和目录"
if ! id radio >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin radio
fi
install -d -m 0755 -o root -g root "${APP_DIR}"
install -d -m 0750 -o radio -g radio "${DATA_DIR}" "${DATA_DIR}/backgrounds"

echo "==> 部署应用文件"
rm -rf "${APP_DIR}/app" "${APP_DIR}/tools"
cp -a "${REPO_DIR}/app" "${APP_DIR}/app"
cp -a "${REPO_DIR}/tools" "${APP_DIR}/tools"
install -m 0644 "${REPO_DIR}/README.md" "${APP_DIR}/README.md"
chown -R root:root "${APP_DIR}"
chmod -R u=rwX,go=rX "${APP_DIR}"

echo "==> 配置环境变量"
NEW_TOKEN=""
if [[ -f "${ENV_FILE}" ]]; then
  echo "保留已有 ${ENV_FILE}"
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  SALT="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  cat >"${ENV_FILE}" <<EOF
RADIO_HOST=127.0.0.1
RADIO_PORT=8080
RADIO_DATA_DIR=${DATA_DIR}
RADIO_STATION_NAME=我的电台
RADIO_LOG_LEVEL=INFO
RADIO_ADMIN_TOKEN=${TOKEN}
RADIO_IP_SALT=${SALT}
EOF
  chmod 0600 "${ENV_FILE}"
  chown root:root "${ENV_FILE}"
  NEW_TOKEN="${TOKEN}"
fi
chown -R radio:radio "${DATA_DIR}"

echo "==> 准备 TLS 证书"
TLS_DIR="/etc/radio-on-stick/tls"
install -d -m 0700 -o root -g root "${TLS_DIR}"
if [[ ! -s "${TLS_DIR}/fullchain.pem" || ! -s "${TLS_DIR}/privkey.pem" ]]; then
  SAN_PARTS="DNS:localhost,DNS:$(hostname)"
  IPS="$(hostname -I 2>/dev/null || true) $(tailscale ip -4 2>/dev/null || true) $(tailscale ip -6 2>/dev/null || true)"
  for ip in ${IPS}; do
    SAN_PARTS="${SAN_PARTS},IP:${ip}"
  done
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
    -keyout "${TLS_DIR}/privkey.pem" \
    -out "${TLS_DIR}/fullchain.pem" \
    -subj "/CN=radio-on-stick" \
    -addext "subjectAltName=${SAN_PARTS}" >/dev/null 2>&1
  chmod 0600 "${TLS_DIR}/privkey.pem"
  chmod 0644 "${TLS_DIR}/fullchain.pem"
  echo "已生成自签名证书，覆盖：${SAN_PARTS}"
else
  echo "保留已有 TLS 证书：${TLS_DIR}"
fi

echo "==> 安装 systemd 服务"
install -m 0644 "${SCRIPT_DIR}/radio-on-stick.service" "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload

echo "==> 安装 Nginx 站点"
ALLOW_SNIPPET="/etc/nginx/snippets/radio-allow.conf"
install -d -m 0755 /etc/nginx/snippets
if [[ -f "${ALLOW_SNIPPET}" ]]; then
  echo "保留现有访问规则：${ALLOW_SNIPPET}"
else
  OLD_SITE="/etc/nginx/sites-available/${SITE}"
  if [[ -f "${OLD_SITE}" ]]; then
    grep -E '^[[:space:]]*(allow|deny|include)[[:space:]]' "${OLD_SITE}" \
      | grep -v 'snippets/radio-allow.conf' \
      | sed 's/^[[:space:]]*//' >"${ALLOW_SNIPPET}" || true
  fi
  if [[ ! -s "${ALLOW_SNIPPET}" ]]; then
    install -m 0644 "${SCRIPT_DIR}/nginx-radio-allow.conf" "${ALLOW_SNIPPET}"
    echo "已写入默认访问规则：${ALLOW_SNIPPET}"
  else
    chmod 0644 "${ALLOW_SNIPPET}"
    echo "已保留旧站点的访问规则：${ALLOW_SNIPPET}"
  fi
fi

install -m 0644 "${SCRIPT_DIR}/nginx-radio.conf" "/etc/nginx/sites-available/${SITE}"
ln -sfn "/etc/nginx/sites-available/${SITE}" "/etc/nginx/sites-enabled/${SITE}"
rm -f /etc/nginx/sites-enabled/default
systemctl enable nginx >/dev/null 2>&1 || true
nginx -t
systemctl restart nginx

if [[ -f "${SCRIPT_DIR}/journald-radio.conf" ]]; then
  install -d -m 0755 /etc/systemd/journald.conf.d
  install -m 0644 "${SCRIPT_DIR}/journald-radio.conf" /etc/systemd/journald.conf.d/radio.conf
  systemctl restart systemd-journald || true
fi

echo "==> 启动服务"
systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
systemctl restart "${SERVICE}"

OK=0
for _ in $(seq 1 30); do
  if python3 - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=2).read()
PY
  then
    OK=1
    break
  fi
  sleep 0.5
done

if [[ "${OK}" -ne 1 ]]; then
  echo "服务未通过健康检查，请查看：" >&2
  systemctl --no-pager --full status "${SERVICE}" >&2 || true
  journalctl -u "${SERVICE}" -n 50 --no-pager >&2 || true
  exit 1
fi

echo
echo "部署完成。"
echo "  应用目录: ${APP_DIR}"
echo "  数据目录: ${DATA_DIR}"
echo "  环境文件: ${ENV_FILE}"
echo "  管理页面: http://<设备IP>/admin"
if [[ -n "${NEW_TOKEN}" ]]; then
  echo "  管理 token: ${NEW_TOKEN}"
  echo "  （请立即保存，之后也可在 ${ENV_FILE} 查看）"
else
  echo "  管理 token 沿用 ${ENV_FILE} 中的已有值"
fi
echo
echo "常用命令："
echo "  systemctl status ${SERVICE}"
echo "  journalctl -u ${SERVICE} -f"
echo "  tail -f /var/log/nginx/radio.access.log"
echo "  python3 ${APP_DIR}/tools/radioctl.py --token <token> add '歌名 歌手'"
