# دليل التصميم الموحّد — Recharge Desk

هذا الملف المرجعي لأي شاشة أو مكوّن جديد في المشروع. **لا ت invent أسلوباً جديداً** — استخدم الأصناف والأنماط الموجودة في `static/css/design-system.css` واتبع القوالب المرجعية أدناه.

---

## 1. المصدر الرسمي للستايل

| الملف | الدور |
|--------|--------|
| `static/css/design-system.css` | التوكنات، التخطيط، البطاقات، الجداول، التبويبات |
| `static/css/app.css` | تخصيصات إضافية صغيرة فقط — لا تضع منطق تصميم جديد هنا |
| Bootstrap 5 | شبكة، نماذج، أزرار — مع override عبر متغيرات `--rd-*` |
| `static/js/data-ui.js` | قوائم الإجراءات داخل الجداول (`rd-actions-details`) |

**قاعدة ذهبية:** إذا احتجت لوناً أو ظلاً أو radius جديداً، أضفه أولاً كـ token في `design-system.css` ثم استخدمه — لا تكتب inline styles إلا للضرورة (مثل عرض عمود ثابت).

---

## 2. هيكل صفحة الإدارة (Management)

كل صفحة إدارية تتبع هذا الترتيب:

```html
{% extends "base_management.html" %}
{% block content %}

<!-- 1) رأس الصفحة -->
<div class="rd-page-header d-flex flex-wrap justify-content-between align-items-start gap-3">
  <div>
    <h1 class="rd-heading-xl mb-1">{{ title }}</h1>
    <p class="rd-text-muted mb-0">وصف قصير للصفحة.</p>
  </div>
  <!-- اختياري: زر رئيسي -->
  <a class="btn btn-primary" href="...">إجراء</a>
</div>

<!-- 2) تبويبات الأقسام (إن وُجدت) -->
<ul class="nav nav-tabs mb-3 rd-section-tabs" role="tablist">...</ul>

<!-- 3) فلاتر / نماذج إدخال -->
<div class="rd-card mb-3">
  <div class="rd-card-body rd-filter-bar">...</div>
</div>

<!-- 4) محتوى رئيسي (جدول أو بطاقات) -->
<div class="rd-card overflow-hidden rd-datagrid">...</div>

{% endblock %}
```

**مرجع:** `phone_refresh/templates/phone_refresh/settings_index.html`، `templates/inventory/overview.html`

---

## 3. التبويبات (Tabs)

### متى تُستخدم
- تقسيم **قسم واحد** إلى شاشات فرعية (مثل: إعدادات التحديث، مخزون الشرائح، API).

### الشكل المطلوب
```html
<ul class="nav nav-tabs mb-3 rd-section-tabs" role="tablist">
  <li class="nav-item" role="presentation">
    <a class="nav-link active" href="?tab=general" role="tab">عام</a>
  </li>
  ...
</ul>
```

### ممنوع
- `nav-pills` للتنقل بين أقسام التطبيق (استخدمها فقط إذا طُلب صراحة).
- تبويبات مخصصة بـ CSS inline أو ألوان مختلفة عن `--bs-primary`.

**مرجع:** `templates/inventory/_nav.html`، `phone_refresh/settings_index.html`

---

## 4. البطاقات (Cards)

| الصنف | الاستخدام |
|--------|-----------|
| `rd-card` | حاوية أي محتوى (نموذج، جدول، KPI) |
| `rd-card-body` | padding داخلي للنماذج والنصوص |
| `rd-card-header` | عنوان شريط علوي (نادر — يُفضّل `fw-semibold` داخل body) |

### نمط قسم داخل تبويب (مثل رابط التحديث)
```html
<div class="rd-card h-100">
  <div class="rd-card-body">
    <div class="mb-3">
      <div class="fw-semibold">عنوان القسم</div>
      <div class="rd-text-muted small">شرح مختصر.</div>
    </div>
    <!-- نموذج أو محتوى -->
  </div>
</div>
```

### ممنوع
- `app-card card` للصفحات الجديدة (legacy فقط — حوّل تدريجياً إلى `rd-card`).
- `rd-card p-3` بدون `rd-card-body`.

---

## 5. الجداول (Tables)

### النمط القياسي للبيانات
```html
<div class="rd-card overflow-hidden rd-datagrid">
  <div class="rd-table-dual rd-datagrid-body">
    <div class="rd-data-shell">
      <div class="table-responsive border-0">
        <table class="table mb-0 align-middle rd-table-modern">
          <thead>...</thead>
          <tbody>...</tbody>
        </table>
      </div>
    </div>
  </div>
</div>
```

### تفاصيل
- أرقام: `tabular-nums` على الخلايا الرقمية.
- اسم رئيسي في الصف: `fw-semibold`.
- نص ثانوي: `rd-text-muted` أو `small rd-text-muted`.
- لا بيانات: `<div class="empty-state my-2">...</div>` داخل `<td colspan="...">`.

### ممنوع
- `table table-sm` بدون `rd-table-modern` في صفحات الإدارة الجديدة.
- جداول bare بدون `rd-card` wrapper.

**مرجع:** `phone_refresh/_api_tokens_tab.html`، `templates/inventory/main_stock.html`

---

## 6. شريط الفلاتر (Filters)

### نمط بسيط (دائماً ظاهر)
```html
<div class="rd-card mb-3">
  <div class="rd-card-body rd-filter-bar">
    <form method="get" class="row g-3 align-items-end">
      <div class="col-md-4">
        <label class="form-label" for="...">...</label>
        <input class="form-control form-control-sm" ...>
      </div>
      <div class="col-12 d-flex flex-wrap gap-2">
        <button type="submit" class="btn btn-primary btn-sm">تطبيق</button>
        <a class="btn btn-outline-secondary btn-sm" href="...">إعادة ضبط</a>
      </div>
    </form>
  </div>
</div>
```

### نمط قابل للطي (HTMX / فلاتر كثيرة)
استخدم `templates/partials/filter_card_open.html` + `filter_card_close.html`.

**مرجع:** `templates/companies/company_list.html`، `templates/inventory/movements.html`

---

## 7. النماذج والأزرار

### حقول النماذج (Django)
في `forms.py` استخدم دائماً:
```python
widget=forms.TextInput(attrs={"class": "form-control"})
widget=forms.Select(attrs={"class": "form-select"})
```

### مفاتيح ON/OFF
```html
<div class="form-check form-switch">
  {{ form.field }}
  <label class="form-check-label fw-semibold" for="...">...</label>
</div>
<p class="small text-secondary mb-0">شرح التأثير.</p>
```

### الأزرار
| النوع | الصنف |
|--------|--------|
| إجراء رئيسي | `btn btn-primary` |
| إجراء ثانوي | `btn btn-outline-secondary` |
| خطر | `btn btn-outline-danger` أو `btn btn-danger` |
| صغير في جدول | `btn btn-sm btn-outline-secondary` |

---

## 8. الشارات (Badges)

```html
<span class="rd-badge rd-badge--pending">...</span>
<span class="rd-badge rd-badge--paid">...</span>
<span class="rd-badge rd-badge--neutral">...</span>
```

لا تستخدم `badge bg-*` من Bootstrap مباشرة في واجهات الإدارة الجديدة.

---

## 9. Typography

| الصنف | الاستخدام |
|--------|-----------|
| `rd-heading-xl` | عنوان الصفحة (H1) |
| `rd-heading-lg` | عنوان قسم داخل بطاقة |
| `rd-text-muted` | وصف، تلميح، نص ثانوي |
| `rd-label` | تسميات KPI / فواتير |

---

## 10. شاشة الموظف (Employee)

- القالب: `base_employee.html`
- المحتوى داخل `rd-employee-main`
- نماذج البيع: `rd-sale-form`، `rd-field-group`، `rd-field-label`
- **لا** تستخدم تخطيط الإدارة الكامل (sidebar) على شاشة الموظف.

---

## 11. الألوان والثيم

- الثيم: `data-bs-theme="light|dark"` على `<html>`.
- اللون الأساسي: `--rd-accent` / `--bs-primary` (بنفسجي indigo).
- **لا** ت hard-code ألوان hex في القوالب — استخدم متغيرات CSS أو أصناف KPI:
  - `kpi-success`, `kpi-danger`, `kpi-warning`, `kpi-info`

---

## 12. RTL / العربية

- `dir="rtl"` و `lang="ar"` يُضبطان من Django i18n.
- استخدم `margin-inline-*`، `padding-inline-*`، `text-start`/`text-end` — لا `ml-*`/`mr-*` في CSS جديد.
- خط العربية: `--rd-font-ar` (Cairo) يُفعّل تلقائياً على `html[lang="ar"]`.

---

## 13. صفحات مرجعية (Golden references)

انسخ من هذه الصفحات عند بناء أي شيء جديد:

| الصفحة | المسار | ماذا تقلّد |
|--------|--------|-----------|
| إعدادات التحديث | `phone_refresh/settings_index.html` | تبويبات + بطاقات أقسام |
| توكنات API | `phone_refresh/_api_tokens_tab.html` | جدول datagrid |
| قائمة الشركات | `templates/companies/company_list.html` | فلاتر + جدول |
| مخزون الشرائح | `templates/inventory/*.html` | تبويبات + فلاتر + جداول |
| إعدادات النظام | `templates/core/system_settings.html` | نموذج switches في بطاقة |
| لوحة التحكم | `templates/reports/dashboard.html` | KPI + charts |

---

## 14. قائمة «ممنوع» (Anti-patterns)

1. **تبويبات pills** لأقسام التطبيق → استخدم `nav-tabs rd-section-tabs`.
2. **جداول Bootstrap خام** بدون `rd-table-modern` + `rd-card`.
3. **ألوان/ظلال inline** → tokens في design-system.
4. **Bootstrap cards** (`card card-body`) في صفحات جديدة → `rd-card`.
5. **تصميم mobile-first منفصل** بدون مراجعة desktop — استخدم `rd-table-dual` + mobile cards حيث موجود.
6. **مكتبات UI خارجية** (Tailwind, Material, …) — ممنوعة.
7. **CSS per-page** في `<style>` — اجمع في design-system أو app.css بحجة واضحة.

---

## 15. عند إضافة مكوّن جديد

1. ابحث في `design-system.css` — هل يوجد صنف جاهز؟
2. انسخ هيكل HTML من صفحة مرجعية (قسم 13).
3. إذا احتجت صنفاً عاماً → أضفه في `design-system.css` مع تعليق قصير.
4. حدّث هذا الملف (`docs/DESIGN_GUIDE.md`) بسطر واحد يشير للمكوّن الجديد.
5. لا تُنشئ «variant» ثالثاً لنفس الفكرة (مثلاً tab style جديد).

---

## 16. Checklist قبل merge

- [ ] `rd-page-header` + عنوان + وصف
- [ ] تبويبات `rd-section-tabs` إن وُجدت
- [ ] بطاقات `rd-card` / `rd-card-body`
- [ ] جداول `rd-table-modern` داخل `rd-datagrid`
- [ ] فلاتر `rd-filter-bar`
- [ ] أزرار من جدول الأزرار (قسم 7)
- [ ] empty state عند عدم وجود بيانات
- [ ] يعمل في light + dark
- [ ] يعمل في RTL (Arabic)

---

*آخر تحديث: توحيد مخزون الشرائح مع أسلوب رابط التحديث — تبويبات `rd-section-tabs`، جداول datagrid، بطاقات rd-card.*
