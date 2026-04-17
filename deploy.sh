#!/usr/bin/env bash
# تشغيل على السيرفر (يفضّل root أو مع sudo) لمزامنة الكود من GitHub ثم Django + إعادة التشغيل.
# عدّل المتغيرات في الأعلى حسب مسارك.
set -euo pipefail

PROJECT_DIR="/opt/recharge-desk"
# إن كان manage.py في جذر المشروع نفسه: ضع APP_DIR="$PROJECT_DIR"
APP_DIR="${PROJECT_DIR}/app"
VENV_DIR="${PROJECT_DIR}/venv"
ENV_FILE="/etc/recharge-desk.env"
APP_USER="rechargedesk"
SERVICE_NAME="recharge-desk"
BRANCH="main"

REQ_FILE="${APP_DIR}/requirements.txt"
[[ -f "$REQ_FILE" ]] || REQ_FILE="${PROJECT_DIR}/requirements.txt"

echo "==> دخول مجلد الريبو"
cd "$PROJECT_DIR"

echo "==> سحب آخر تحديث من Git (مطابقة لـ origin/${BRANCH})"
sudo git -c "safe.directory=${PROJECT_DIR}" fetch origin
sudo git -c "safe.directory=${PROJECT_DIR}" reset --hard "origin/${BRANCH}"

echo "==> pip + migrate + collectstatic + check (كمستخدم ${APP_USER})"
sudo -u "${APP_USER}" -H bash -lc "
set -a
. '${ENV_FILE}'
set +a
source '${VENV_DIR}/bin/activate'
pip install -q -r '${REQ_FILE}'
cd '${APP_DIR}'
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py check
"

echo "==> إعادة تشغيل الخدمة"
sudo systemctl restart "${SERVICE_NAME}.service"

echo "==> حالة الخدمة"
sudo systemctl status "${SERVICE_NAME}.service" --no-pager

echo "==> انتهى التحديث بنجاح"
