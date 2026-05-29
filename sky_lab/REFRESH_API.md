# Sky Sales Portal — API تحديث الشريحة

**Base URL:** `https://sales-ps.sky5g.ps:8888`  
**Generic API:** `POST /ipa/apis/json/internal/generic/v2`  
**Auth:** Cookies + `jwtToken` + `sessionId` (بعد login + OTP)

---

## رسالة النجاح (ردّنا — recharge-desk)

**قاعدة بسيطة:** لا حاجة للتحقق من رقم العملية أو مطابقته.  
إذا رجع `CreateNewTaskWithBalanceCheck` وفي `data[0].ID` رقم عملية (موجود وغير `-1`) → **نجاح فوري**.

```json
{
  "success": true,
  "message": "تم التحديث بنجاح"
}
```

| شرط | القيمة |
|-----|--------|
| `result` | `SUCCESS` |
| `data[0].ID` | موجود (مثال: `4520823`) |
| لا نعمل | تحقق لاحق من المهمة، ولا نعرض رقم العملية للمستخدم |

> الواجهة الأصلية تعرض «تم إصدار المهمة» + رقم المهمة — نحن نكتفي بـ **تم التحديث بنجاح**.

---

## مسار العملية

```
تحديث الشريحة (UI)
  → actionName = "change-status"
  → checkbox refresh (#sim-status-refresh)
  → GetSubscriberCosts
  → ValidateTaskDataBeforeCreate
  → GetSubscriberStatus
  → CreateNewTaskWithBalanceCheck
  → task_id في data[0].ID
```

---

## شكل الطلب العام

```http
POST /ipa/apis/json/internal/generic/v2 HTTP/1.1
Host: sales-ps.sky5g.ps:8888
Content-Type: application/json; charset=utf-8
Cookie: (session cookies من المتصفح)

{
  "apiName": "<API_NAME>",
  "wildcards": ["...", "..."],
  "jwtToken": "<JWT>",
  "sessionId": "<SESSION_ID>"
}
```

> `env.SEND_COOKIES = true` — الاعتماد على الكوكيز + JWT، بدون username/password في body.

---

## APIs المستخدمة في التحديث

### 1) GetSubscriberCosts — حساب تكلفة العملية

```json
{
  "apiName": "GetSubscriberCosts",
  "wildcards": [
    "0555544071",
    "1481",
    "321",
    "0",
    "change-status",
    "0"
  ]
}
```

| wildcard | المعنى |
|----------|--------|
| [0] | MSISDN |
| [1] | sub_customer_id |
| [2] | rate_plan_id |
| [3] | period_id (0 للتحديث) |
| [4] | task type = `change-status` |
| [5] | vas_id (0) |

**Response (نجاح):**
```json
{
  "result": "SUCCESS",
  "data": [{
    "ORDER_COST": "0",
    "ORDER_REFUND": "0",
    "ORDER_TOTAL_COST": "0"
  }]
}
```

---

### 2) ValidateTaskDataBeforeCreate

```json
{
  "apiName": "ValidateTaskDataBeforeCreate",
  "wildcards": ["0555544071", ""]
}
```

---

### 3) GetSubscriberStatus

```json
{
  "apiName": "GetSubscriberStatus",
  "wildcards": ["511677"]
}
```

يتحقق أن `STATUS_ID` الحالي = `301` (مفعل) قبل الإنشاء.

---

### 4) CreateNewTaskWithBalanceCheck — إصدار المهمة

```json
{
  "apiName": "CreateNewTaskWithBalanceCheck",
  "wildcards": [
    "change-status",
    1550,
    "511677",
    "<HTML task description>",
    "<JSON stringified taskDetails>"
  ]
}
```

| wildcard | المعنى |
|----------|--------|
| [0] | `change-status` |
| [1] | `eot_user_id` |
| [2] | `simId` / subscriber_id |
| [3] | `taskDescription` (HTML) |
| [4] | `JSON.stringify(taskDetails)` |

**Response (نجاح — من التنفيذ الفعلي):**
```json
{
  "message": "",
  "result": "SUCCESS",
  "columnList": ["ID"],
  "data": [{ "ID": "4520823" }],
  "rowsAffected": 0
}
```

**Response (فشل):**
```json
{
  "result": "FAILED",
  "message": "..."
}
```

---

## taskDetails لتحديث الشريحة (0555544071)

```json
{
  "actionName": "change-status",
  "actionType": "تعديل زبون",
  "existingData": {
    "subscriber_id": "511677",
    "msisdn": "0555544071",
    "iccid": "899720206491380371",
    "rate_plan_id": "321",
    "mno_id": "1",
    "status_id": "301",
    "subCustomer_id": "1481"
  },
  "newData": {
    "subscriber_id": "511677",
    "msisdn": "0555544071",
    "iccid": "899720206491380371",
    "status_id": "",
    "price": "55",
    "orderCost": "0.00",
    "orderTotalCost": "0.00"
  }
}
```

> `newData.status_id` فارغ = تحديث/refresh بدون تغيير حالة.

---

## مثال Python (requests)

```python
import json
import requests

BASE = "https://sales-ps.sky5g.ps:8888"
session = requests.Session()
# session.cookies + jwtToken + sessionId بعد login/OTP

def generic_api(api_name: str, wildcards: list, jwt: str, session_id: str):
    payload = {
        "apiName": api_name,
        "wildcards": wildcards,
        "jwtToken": jwt,
        "sessionId": session_id,
    }
    r = session.post(
        f"{BASE}/ipa/apis/json/internal/generic/v2",
        json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

# بعد بناء taskDetails من بيانات الزبون:
resp = generic_api(
    "CreateNewTaskWithBalanceCheck",
    ["change-status", 1550, "511677", task_html, json.dumps(task_details)],
    jwt_token,
    session_id,
)
def is_refresh_success(resp: dict) -> bool:
    if resp.get("result") != "SUCCESS":
        return False
    data = resp.get("data") or []
    if not data:
        return False
    task_id = str(data[0].get("ID", "")).strip()
    return bool(task_id) and task_id != "-1"

resp = generic_api(...)
if is_refresh_success(resp):
    print({"success": True, "message": "تم التحديث بنجاح"})
```

---

## OTP تلقائي من QR (Google Authenticator)

إذا الـ OTP مربوط بـ **QR** (مش SMS)، استخرج الـ **secret** وضعه في `sky_lab/.env`:

```env
SKY_SALES_TOTP_SECRET=XXXXXXXXXXXXXXXX
```

أو الرابط الكامل من QR:

```env
SKY_SALES_OTP_AUTH_URI=otpauth://totp/Sky:hamzahw?secret=XXXX&issuer=Sky
```

**استخراج السر:**
```bash
# من نص otpauth (انسخ من QR scanner)
python3 extract_totp.py 'otpauth://totp/...'

# أو من صورة QR
python3 extract_totp.py qr.png
```

**تشغيل كامل بدون تدخل:**
```bash
pip install pyotp
python3 refresh_sim.py 0555544071
```

`sky_login.py` و `refresh_sim.py` يولّدون كود TOTP تلقائياً بعد اللوجين.

---

## الجلسة + Sticky IP

الجلسة **مربوطة بـ IP الخروج** (من البروكسي أو السيرفر). محفوظة في `.sky_session.json`:

| حقل | المعنى |
|-----|--------|
| `bound_ip` | IP وقت اللوجين |
| `proxy_fingerprint` | بصمة `SKY_SALES_PROXY` |
| `logged_in_at` | وقت اللوجين |

**السلوك:**
```
قبل كل تحديث:
  1. اكتشف IP الحالي (api.ipify.org عبر البروكسي)
  2. IP == bound_ip؟ → أعد استخدام الجلسة + تجديد JWT
  3. IP تغيّر؟ → لوجين + TOTP جديد → جلسة جديدة
  4. فشل API (REJECTED)؟ → لوجين جديد + إعادة التحديث مرة واحدة
```

**Sticky proxy (Geonix):**
```env
SKY_SALES_PROXY=http://LOGIN_s_skysales:PASSWORD@res.geonix.com:10001
```
`_s_skysales` = نفس IP لمدة الجلسة (~1–2 ساعة). لما الـ IP يتغيّر، الكود يلاحظ ويفتح جلسة جديدة تلقائياً.

---

## تشغيل بدون متصفح (SMS يدوي)

```bash
cd sky_lab

# مرة أولى: login + OTP من SMS
export SKY_SALES_USER=hamzahw
export SKY_SALES_PASSWORD='...'
export SKY_SALES_OTP=123456   # كود SMS لمرة واحدة

python3 refresh_sim.py 0555544071
```

**رد النجاح:**
```json
{
  "success": true,
  "message": "تم التحديث بنجاح - رقم العملية: 4520823",
  "task_id": "4520823",
  "phone": "0555544071"
}
```

> بعد OTP يمكن حفظ `SKY_SALES_JWT` + `SKY_SALES_EOT_USER_ID` مؤقتاً (تنتهي خلال ~30 دقيقة).

**رد الرفض (تكرار تحديث):** `data[0].ID = "ERROR"` من Sky — الرقم محدّث مؤخراً.
