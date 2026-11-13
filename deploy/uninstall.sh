#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 运行：sudo bash deploy/uninstall.sh [--purge]" >&2
  exit 1
fi

PURGE=0
if [[ "${1:-}" == "--purge" ]]; then
  PURGE=1
fi

APP_DIR="/opt/radio-on-stick"
DATA_DIR="/var/lib/radio-on-stick"
ENV_FILE="/etc/radio-on-stick.env"
SERVICE="radio-on-stick"
SITE="radio-on-stick"

systemctl disable --now "${SERVICE}" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload

rm -f "/etc/nginx/sites-enabled/${SITE}" "/etc/nginx/sites-available/${SITE}"
nginx -t >/dev/null 2>&1 && systemctl reload nginx || true

if [[ "${PURGE}" -eq 1 ]]; then
  rm -rf "${APP_DIR}" "${DATA_DIR}" "${ENV_FILE}"
  if id radio >/dev/null 2>&1; then
    userdel radio 2>/dev/null || true
  fi
  echo "已卸载应用、数据和环境文件。"
else
  rm -rf "${APP_DIR}"
  echo "已移除应用和系统服务；保留数据：${DATA_DIR}"
  echo "如需彻底删除：sudo bash deploy/uninstall.sh --purge"
fi
