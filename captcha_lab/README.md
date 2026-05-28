# captcha_lab

تجارب حل reCAPTCHA لـ Sky — **منفصلة عن المشروع الرئيسي**.

لا تعدّل `phone_refresh/` هنا. كل تجربة مجلد مستقل. إذا نجح التوken في Burp، ندمجه لاحقاً في النظام.

## الإعداد (مرة واحدة)

```bash
cd captcha_lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## التجربة 01 — Playwright + stealth

```bash
cd captcha_lab/01_playwright_stealth
../../.venv/bin/python get_token.py
# أو بعد تفعيل venv الخاص بالـ lab:
python get_token.py
```

ينسخ التوken في الطرفية + `last_token.txt`. جرّبه في Burp:

```http
POST /api/public/refresh HTTP/2
Host: rn.sky-5g.net
Content-Type: application/json

{"phone_number":"0555544071","captcha":"TOKEN_HERE"}
```

## ترتيب التجارب

| # | المجلد | الحالة |
|---|--------|--------|
| 1 | `01_playwright_stealth` | ❌ Sky رفض |
| 2 | `02_playwright_firefox` | ✅ Burp نجح |
| 3 | `03_undetected_chromedriver` | لاحقاً |
| 4 | `04_camoufox` | لاحقاً |
