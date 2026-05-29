# Sky Lab — خطوات اكتشاف API التحديث

## الهدف
توثيق خطوات التحديث من لوحة Sky Sales → بناء API مستقل.

## الجلسة

| # | الخطوة | URL | ملاحظات |
|---|--------|-----|---------|
| 1 | فتح صفحة الدخول | https://sales-ps.sky5g.ps/ | واجهة عبرית (SKY) |
| 2 | إدخال username + password | نفس الرابط | نجح → طلب OTP |
| 3 | **OTP عبر SMS** | نفس الرابط | حقل: `קוד שנשלח לטלפון שלך` — بانتظار الكود |

## طلبات الشبكة (Network)

```
POST https://sales-ps.sky5g.ps:8888/ipa/apis/json/internal/generic/v2
  → GetSubscriberCosts        (change-status)
  → ValidateTaskDataBeforeCreate
  → GetSubscriberStatus
  → CreateNewTaskWithBalanceCheck  → task_id: 4520823
```

## نتيجة التحديث (0555544071)

- **ردّنا:** `{"success": true, "message": "تم التحديث بنجاح"}` — بمجرد ظهور `data[0].ID`
- **رقم العملية من البوابة:** 4520823 (للتوثيق فقط، لا نعرضه ولا نتحقق منه)
- **API doc:** `REFRESH_API.md`
- **JSON:** `responses/success_0555544071.json`
