# sky_lab — بيئة اختبار داخلية

مجلد للتجربة اليدوية على Sky Sales Portal. **الكود الإنتاجي** في:

`phone_refresh/providers/sky_sales_client.py`

## إعداد سريع

```bash
cp .env.example .env   # أو عدّل sky_lab/.env
pip install pyotp requests
python3 refresh_sim.py 0555544071
```

## الملفات

| ملف | الغرض |
|-----|--------|
| `.env` | credentials + proxy + TOTP (gitignored) |
| `.sky_session.json` | جلسة محفوظة (gitignored) |
| `refresh_sim.py` | CLI تحديث رقم |
| `sky_login.py` | CLI تسجيل دخول فقط |
| `extract_totp.py` | استخراج TOTP secret من QR |
| `REFRESH_API.md` | توثيق API |
| `responses/` | أمثلة ردود مرصودة |
