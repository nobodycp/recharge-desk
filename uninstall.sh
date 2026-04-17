#!/usr/bin/env bash
#
# إزالة معقولة لتثبيت Recharge Desk على Ubuntu (عكس install.py الافتراضي).
# يشغّل كـ root: sudo bash uninstall.sh
#
# قبل التشغيل: عدّل المتغيرات أدناه لتطابق install.config.json عندك.
#
# حماية: لن يحذف شيئاً مدمراً (PostgreSQL / مستخدم النظام / جذر المشروع بالكامل)
#         إلا إذا فعّلت المتغيرات الصريحة + UNINSTALL_CONFIRM=YES
#
# جاف تشغيل: UNINSTALL_DRY_RUN=1 sudo bash uninstall.sh
#
set -euo pipefail

# --- اضبط هنا (نفس القيم الافتراضية في install/config.example.json) ---
PROJECT_DIR="/opt/recharge-desk"
APP_DIR="${PROJECT_DIR}/app"
VENV_DIR="${PROJECT_DIR}/venv"
STATIC_DIR="${PROJECT_DIR}/staticfiles"
MEDIA_DIR="${PROJECT_DIR}/media"
ENV_FILE="/etc/recharge-desk.env"
SERVICE_NAME="recharge-desk"
CADDY_SITES_DIR="/etc/caddy/sites"
CADDY_FRAGMENT="recharge-desk.caddy"
SYSTEM_USER="rechargedesk"
# بيانات PostgreSQL (للحذف الاختياري فقط)
POSTGRES_DB="recharge_desk"
POSTGRES_USER="recharge_desk"

# --- تأكيد إلزامي ---
: "${UNINSTALL_CONFIRM:=}"
if [[ "${UNINSTALL_CONFIRM}" != "YES" ]]; then
	echo "ERROR: To run uninstall, set:  UNINSTALL_CONFIRM=YES" >&2
	echo "Example:  sudo UNINSTALL_CONFIRM=YES bash uninstall.sh" >&2
	exit 1
fi

DRY="${UNINSTALL_DRY_RUN:-0}"
run() {
	if [[ "$DRY" == "1" ]]; then
		echo "[dry-run] $*"
	else
		"$@"
	fi
}

echo "==> إيقاف وتعطيل خدمة التطبيق (${SERVICE_NAME})"
run systemctl stop "${SERVICE_NAME}.service" || true
run systemctl disable "${SERVICE_NAME}.service" || true

echo "==> حذف وحدة systemd"
run rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
run systemctl daemon-reload

echo "==> حذف موقع Caddy"
if [[ -f "${CADDY_SITES_DIR}/${CADDY_FRAGMENT}" ]]; then
	run rm -f "${CADDY_SITES_DIR}/${CADDY_FRAGMENT}"
	run systemctl reload caddy || true
else
	echo "    (ملف Caddy غير موجود — تخطي)"
fi

echo "==> حذف ملف البيئة"
run rm -f "${ENV_FILE}"

echo "==> حذف أدلة التطبيق (venv / app / staticfiles / media)"
for d in "${VENV_DIR}" "${APP_DIR}" "${STATIC_DIR}" "${MEDIA_DIR}"; do
	if [[ -e "$d" ]]; then
		run rm -rf "$d"
	else
		echo "    (تخطي — غير موجود: $d)"
	fi
done

echo "==> حذف ملف كاش static_version إن وُجد"
run rm -rf /var/lib/recharge-desk

# --- اختياري: قاعدة PostgreSQL (مدمر للبيانات) ---
if [[ "${UNINSTALL_DROP_POSTGRES:-}" == "YES" ]]; then
	echo "==> PostgreSQL: dropdb + dropuser (أدوات عميل PostgreSQL)"
	run sudo -u postgres dropdb --if-exists "${POSTGRES_DB}" || true
	run sudo -u postgres dropuser --if-exists "${POSTGRES_USER}" || true
	echo "    إن فشل dropuser لأن الدور مرتبط بموارد أخرى، احذفه يدوياً من psql."
else
	echo "==> PostgreSQL: لم يُحذف (ضع UNINSTALL_DROP_POSTGRES=YES لحذف القاعدة '${POSTGRES_DB}' والدور '${POSTGRES_USER}')"
fi

# --- اختياري: مستخدم النظام Linux ---
if [[ "${UNINSTALL_REMOVE_SYSTEM_USER:-}" == "YES" ]]; then
	echo "==> حذف مستخدم النظام ${SYSTEM_USER}"
	run userdel -r "${SYSTEM_USER}" || true
else
	echo "==> مستخدم النظام: لم يُحذف (ضع UNINSTALL_REMOVE_SYSTEM_USER=YES لحذف ${SYSTEM_USER})"
fi

# --- اختياري: جذر المشروع بالكامل (يشمل .git إن وُجد) ---
if [[ "${UNINSTALL_REMOVE_PROJECT_ROOT:-}" == "YES" ]]; then
	echo "==> حذف كامل لـ PROJECT_DIR: ${PROJECT_DIR}"
	run rm -rf "${PROJECT_DIR}"
else
	echo "==> جذر المشروع: لم يُحذف بالكامل (بقي ${PROJECT_DIR} إن وُجد). ضع UNINSTALL_REMOVE_PROJECT_ROOT=YES لحذف كل المجلد."
fi

echo ""
echo "==> انتهى."
echo "    تذكير: إذا عدّل المثبت /etc/caddy/Caddyfile (سطر import)، راجعه يدوياً واستعد نسخة .bak.installer إن لزم."
echo "    حزم apt (postgresql, caddy, …) لم تُزال — أزلها بـ apt remove إن أردت."
