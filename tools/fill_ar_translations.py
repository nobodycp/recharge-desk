"""
Fill Arabic msgstr entries in locale/ar/LC_MESSAGES/django.po (stdlib only).
Run from project root: python tools/fill_ar_translations.py
Then: msgfmt -o locale/ar/LC_MESSAGES/django.mo locale/ar/LC_MESSAGES/django.po
"""
from __future__ import annotations

import re
from pathlib import Path


def po_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# UI + model verbose strings used in the MVP
AR: dict[str, str] = {
    "Accounts": "الحسابات",
    "Full name": "الاسم الكامل",
    "Role": "الدور",
    "Profile active": "الملف الشخصي مفعّل",
    "Login enabled": "تسجيل الدخول مفعّل",
    "Management": "الإدارة",
    "Employee": "موظف",
    "full name": "الاسم الكامل",
    "role": "الدور",
    "profile active": "الملف الشخصي مفعّل",
    "user profile": "ملف المستخدم",
    "user profiles": "ملفات المستخدمين",
    "Employees & users": "الموظفون والمستخدمون",
    "User created.": "تم إنشاء المستخدم.",
    "Create user": "إنشاء مستخدم",
    "User updated.": "تم تحديث المستخدم.",
    "Edit user": "تعديل المستخدم",
    "name": "الاسم",
    "opening balance": "الرصيد الافتتاحي",
    "current balance": "الرصيد الحالي",
    "notes": "ملاحظات",
    "active": "نشط",
    "company": "الشركة",
    "companies": "الشركات",
    "cost price": "سعر التكلفة",
    "default sell price": "سعر البيع الافتراضي",
    "product": "المنتج",
    "products": "المنتجات",
    "Companies": "الشركات",
    "Company saved.": "تم حفظ الشركة.",
    "New company": "شركة جديدة",
    "Company updated.": "تم تحديث الشركة.",
    "Edit company": "تعديل الشركة",
    "Products": "المنتجات",
    "Product saved.": "تم حفظ المنتج.",
    "New product": "منتج جديد",
    "Product updated.": "تم تحديث المنتج.",
    "Edit product": "تعديل المنتج",
    "Access denied": "الوصول مرفوض",
    "title": "العنوان",
    "category": "التصنيف",
    "amount": "المبلغ",
    "Amount": "المبلغ",
    "date": "التاريخ",
    "created by": "أنشأه",
    "expense": "مصروف",
    "expenses": "المصروفات",
    "Expenses": "المصروفات",
    "Expense saved.": "تم حفظ المصروف.",
    "New expense": "مصروف جديد",
    "Expense updated.": "تم تحديث المصروف.",
    "Edit expense": "تعديل المصروف",
    "Date from": "من تاريخ",
    "Date to": "إلى تاريخ",
    "Expense report": "تقرير المصروفات",
    "Dashboard": "لوحة التحكم",
    "Profit report": "تقرير الأرباح",
    "Sales report": "تقرير المبيعات",
    "Deposit recorded.": "تم تسجيل الإيداع.",
    "Adjustment recorded.": "تم تسجيل التسوية.",
    "Company report": "تقرير الشركة",
    "Company": "الشركة",
    "Choose company": "اختر الشركة",
    "Product": "المنتج",
    "Choose product": "اختر المنتج",
    "Phone or shipment number": "رقم الهاتف أو الشحنة",
    "Selling price": "سعر البيع",
    "Payment method": "طريقة الدفع",
    "Choose payment method": "اختر طريقة الدفع",
    "Payer name": "اسم الدافع",
    "Customer name": "اسم العميل",
    "Notes": "ملاحظات",
    "Selected product does not belong to the company.": "المنتج المختار لا يتبع الشركة المحددة.",
    "Status": "الحالة",
    "All": "الكل",
    "Signed adjustment (+/-)": "تسوية موقعة (+/-)",
    "payment method": "طريقة الدفع",
    "payment methods": "طرق الدفع",
    "Pending": "معلّق",
    "Paid": "مدفوع",
    "Cancelled": "ملغى",
    "phone or shipment number": "رقم الهاتف أو الشحنة",
    "customer name": "اسم العميل",
    "payer name": "اسم الدافع",
    "actual selling price": "سعر البيع الفعلي",
    "cost price (snapshot)": "سعر التكلفة (لقطة)",
    "profit (snapshot)": "الربح (لقطة)",
    "status": "الحالة",
    "created at": "تاريخ الإنشاء",
    "updated at": "تاريخ التحديث",
    "marked paid at": "تاريخ التعليم كمدفوع",
    "marked paid by": "عُلِّم كمدفوع بواسطة",
    "cancelled at": "تاريخ الإلغاء",
    "cancelled by": "ألغاه",
    "sale": "عملية بيع",
    "sales": "المبيعات",
    "Deposit": "إيداع",
    "Deduction": "خصم",
    "Adjustment": "تسوية",
    "Reversal": "عكس حركة",
    "Sale": "بيع",
    "Manual": "يدوي",
    "Opening balance": "الرصيد الافتتاحي",
    "Cancellation": "إلغاء",
    "type": "النوع",
    "reference type": "نوع المرجع",
    "reference id": "معرّف المرجع",
    "balance transaction": "حركة رصيد",
    "balance transactions": "حركات الرصيد",
    "Sale recorded successfully.": "تم تسجيل البيع بنجاح.",
    "New sale": "بيع جديد",
    "New SIM": "شريحة جديدة",
    "Sales": "المبيعات",
    "Pending payments": "المدفوعات المعلّقة",
    "Marked as paid.": "تم التعليم كمدفوع.",
    "Sale cancelled and supplier balance restored.": "تم إلغاء البيع واسترجاع رصيد المورّد.",
    "Payment methods": "طرق الدفع",
    "Payment method saved.": "تم حفظ طريقة الدفع.",
    "New payment method": "طريقة دفع جديدة",
    "Payment method updated.": "تم تحديث طريقة الدفع.",
    "Edit payment method": "تعديل طريقة الدفع",
    "Sign in": "تسجيل الدخول",
    "Recharge Desk": "المحاسب الهامل",
    "Please correct the errors below.": "يرجى تصحيح الأخطاء أدناه.",
    "Save": "حفظ",
    "Cancel": "إلغاء",
    "Users": "المستخدمون",
    "Username": "اسم المستخدم",
    "Active": "نشط",
    "Yes": "نعم",
    "No": "لا",
    "Edit": "تعديل",
    "No users.": "لا يوجد مستخدمون.",
    "Sales entry": "إدخال مبيعات",
    "Log out": "تسجيل الخروج",
    "Name": "الاسم",
    "Opening": "افتتاحي",
    "Current balance": "الرصيد الحالي",
    "Report": "تقرير",
    "No companies.": "لا توجد شركات.",
    "Cost": "التكلفة",
    "Default sell": "البيع الافتراضي",
    "No products.": "لا توجد منتجات.",
    "You do not have permission to view this page.": "ليس لديك صلاحية لعرض هذه الصفحة.",
    "Go home": "الرئيسية",
    "Date": "التاريخ",
    "Title": "العنوان",
    "Category": "التصنيف",
    "By": "بواسطة",
    "No expenses.": "لا توجد مصروفات.",
    "Category breakdown and totals": "التصنيفات والإجماليات",
    "Apply": "تطبيق",
    "Reset": "إعادة ضبط",
    "Total profit (same date filter)": "إجمالي الربح (نفس نطاق التاريخ)",
    "Total expenses (filtered)": "إجمالي المصروفات (بعد التصفية)",
    "Net profit": "صافي الربح",
    "Profit minus expenses": "الربح ناقص المصروفات",
    "By category": "حسب التصنيف",
    "No data.": "لا توجد بيانات.",
    "Rows": "الصفوف",
    "Supplier statement & internal ledger": "كشف المورّد وسجل الرصيد الداخلي",
    "Back": "رجوع",
    "Total deposits": "إجمالي الإيداعات",
    "Consumed (sales)": "المستهلك (مبيعات)",
    "Reversals (cancellations)": "العكس (إلغاءات)",
    "Adjustments": "التسويات",
    "Sales count (non-cancelled)": "عدد المبيعات (غير الملغاة)",
    "Total sell": "إجمالي البيع",
    "Total profit": "إجمالي الربح",
    "Manual balance top-up": "تعبئة رصيد يدوية",
    "Save deposit": "حفظ الإيداع",
    "Balance adjustment": "تسوية الرصيد",
    "Save adjustment": "حفظ التسوية",
    "Sales detail": "تفاصيل المبيعات",
    "When": "متى",
    "Ref": "المرجع",
    "Sell": "البيع",
    "Profit": "الربح",
    "No sales.": "لا توجد مبيعات.",
    "Balance ledger": "سجل الرصيد",
    "Type": "النوع",
    "Reference": "المرجع",
    "No ledger rows.": "لا توجد حركات في السجل.",
    "Operational snapshot": "لمحة تشغيلية",
    "Today's sales": "مبيعات اليوم",
    "Volume": "الحجم",
    "Today's profit": "ربح اليوم",
    "All-time profit": "الربح الكلي",
    "All-time expenses": "المصروفات الكلية",
    "Net profit (all time)": "صافي الربح (كلّي)",
    "This month profit": "ربح هذا الشهر",
    "Net (month)": "الصافي (شهري)",
    "Supplier balances": "أرصدة المورّدين",
    "No companies yet.": "لا توجد شركات بعد.",
    "Recent activity": "النشاط الأخير",
    "No sales yet.": "لا توجد مبيعات بعد.",
    "Excludes cancelled sales": "باستثناء المبيعات الملغاة",
    "By company": "حسب الشركة",
    "Count": "العدد",
    "By payment method": "حسب طريقة الدفع",
    "Method": "الطريقة",
    "By employee": "حسب الموظف",
    "By product": "حسب المنتج",
    "Daily profit trend": "اتجاه الربح اليومي",
    "Day": "اليوم",
    "Monthly profit trend": "اتجاه الربح الشهري",
    "Month": "الشهر",
    "Filter, review, and export-friendly table layout": "تصفية ومراجعة وجدول مناسب للتصدير",
    "Filtered profit (excl. cancelled)": "الربح بعد التصفية (باستثناء الملغاة)",
    "Filtered volume": "الحجم بعد التصفية",
    "Filtered rows": "الصفوف بعد التصفية",
    "Results": "النتائج",
    "Copy/paste friendly": "مناسب للنسخ",
    "No rows.": "لا توجد صفوف.",
    "optional": "اختياري",
    "Save sale": "حفظ البيع",
    "Recent entries": "آخر الإدخالات",
    "No recent entries yet.": "لا توجد إدخالات حديثة بعد.",
    "Mark paid": "تعليم كمدفوع",
    "Cancel this sale?": "إلغاء هذا البيع؟",
    "New": "جديد",
    "Inactive": "غير نشط",
    "No payment methods.": "لا توجد طرق دفع.",
    "All sales": "كل المبيعات",
    "Payer": "الدافع",
    "No pending sales.": "لا توجد مبيعات معلّقة.",
    "Color theme": "مظهر الألوان",
    "Light theme": "الوضع الفاتح",
    "Dark theme": "الوضع الداكن",
    "Light": "فاتح",
    "Dark": "داكن",
    "Show more": "عرض المزيد",
    "Choose a company to see products.": "اختر شركة لعرض المنتجات.",
    "No active companies.": "لا توجد شركات نشطة.",
    "Lines for disconnect": "أرقام للفصل",
    "Idle threshold": "عتبة عدم النشاط",
    "Days without a new sale entry": "أيام من دون إدخال بيع جديد",
    "Save threshold": "حفظ العتبة",
    "Saved on your user profile until you change it again.": "يُحفَظ في ملف المستخدم حتى تغيّره مرة أخرى.",
    "Number / reference": "الرقم / المرجع",
    "SIM / chip": "الشريحة / رقم الشريحة",
    "Last sale entry": "آخر إدخال بيع",
    "Customer or payer": "الزبون أو الدافع",
    "Days since last sale": "أيام منذ آخر بيع",
    "Remove this line from the report only? Sales in the system are not deleted; the number can appear again if it becomes idle.": (
        "إزالة هذا السطر من التقرير فقط؟ لا تُحذف المبيعات من النظام؛ قد يظهر الرقم مجدداً إذا عاد راكداً."
    ),
    "No lines match this threshold.": "لا توجد خطوط تطابق هذه العتبة.",
    "Threshold saved.": "تم حفظ العتبة.",
    "Could not save threshold.": "تعذّر حفظ العتبة.",
    "Line updated.": "تم تحديث الخط.",
    "Please correct the errors below.": "يرجى تصحيح الأخطاء أدناه.",
    "Removed from this list.": "أُزيل من هذه القائمة.",
    "Edit line": "تعديل الخط",
    # SIM inventory (Phase 1)
    "SIM inventory": "مخزون الشرائح",
    "SIM inventory sections": "أقسام مخزون الشرائح",
    "Overview": "نظرة عامة",
    "Movements": "الحركات",
    "Main stock": "المخزون الرئيسي",
    "Main SIM stock": "المخزون الرئيسي للشرائح",
    "Customer stock": "مخزون الزبون",
    "Customer SIM stock": "مخزون شرائح الزبائن",
    "SIM stock balance": "رصيد مخزون الشرائح",
    "SIM stock balances": "أرصدة مخزون الشرائح",
    "SIM stock movement": "حركة مخزون الشرائح",
    "SIM stock movements": "حركات مخزون الشرائح",
    "Main receive": "استلام للمخزون الرئيسي",
    "Allocate to customer": "توزيع على زبون",
    "Return from customer": "مرتجع من زبون",
    "Damaged": "تالف",
    "Sale consume": "خصم بيع",
    "Sale reversal": "استرجاع بيع",
    "Quantity": "الكمية",
    "quantity": "الكمية",
    "Main stock updated.": "تم تحديث المخزون الرئيسي.",
    "Damaged stock recorded.": "تم تسجيل الشرائح التالفة.",
    "Stock allocated to customer.": "تم توزيع المخزون على الزبون.",
    "Stock returned to main.": "تم إرجاع المخزون إلى الرئيسي.",
    "Receive stock": "استلام مخزون",
    "Allocate from main": "توزيع من الرئيسي",
    "Allocate": "توزيع",
    "Return to main": "إرجاع للرئيسي",
    "Return": "إرجاع",
    "Back to list": "العودة للقائمة",
    "No main stock yet.": "لا يوجد مخزون رئيسي بعد.",
    "No customer stock.": "لا يوجد مخزون عند الزبائن.",
    "No stock for this customer.": "لا يوجد مخزون لهذا الزبون.",
    "No movements.": "لا توجد حركات.",
    "Plastic SIM counts by product line (main + with distributors).": "أعداد الشرائح البلاستيكية حسب خط المنتج (رئيسي + عند الموزّعين).",
    "Main": "رئيسي",
    "With customers": "عند الزبائن",
    "Line": "الخط",
    "Qty": "الكمية",
    "New SIM": "شريحة جديدة",
    "new SIM sale": "بيع شريحة جديدة",
    "Not deducted": "لم يُخصم",
    "Enter payer name and product to preview SIM stock.": "أدخل اسم الدافع والمنتج لمعاينة مخزون الشرائح.",
    "Quantity must be at least 1.": "يجب أن تكون الكمية 1 على الأقل.",
    "Insufficient main stock for this product line.": "المخزون الرئيسي غير كافٍ لهذا الخط.",
    "Insufficient customer stock for this product line.": "مخزون الزبون غير كافٍ لهذا الخط.",
    "Adjustment would make stock negative.": "التسوية ستجعل المخزون سالباً.",
    "Insufficient stock to mark as damaged.": "المخزون غير كافٍ لتسجيل تالف.",
    "Insufficient SIM stock.": "مخزون الشرائح غير كافٍ.",
    "Insufficient main SIM stock for this product line.": "المخزون الرئيسي للشرائح غير كافٍ لهذا الخط.",
    "Cannot reverse SIM: customer stock source unknown.": "تعذّر استرجاع الشريحة: مصدر مخزون الزبون غير معروف.",
    "Invalid form.": "نموذج غير صالح.",
    "Multiple customers share this payer name. Use a unique registered name.": "أكثر من زبون يشاركون اسم الدافع هذا. استخدم اسماً مسجلاً فريداً.",
    "Customer name…": "اسم الزبون…",
    "Payer name and product are required.": "اسم الدافع والمنتج مطلوبان.",
    # System settings (recent)
    "Approval workflows": "سير الموافقات",
    "Management panel defaults": "افتراضيات لوحة الإدارة",
    "Refresh link defaults": "افتراضيات رابط التحديث",
    "Sales entry screen": "شاشة إدخال المبيعات",
    "System (match device)": "تلقائي (حسب الجهاز)",
    "Allow creating customers from sales entry": "السماح بإنشاء زبائن من شاشة المبيعات",
    "When off, on-account sales require an existing customer created from the Customers screen.": (
        "عند الإيقاف، تتطلب مبيعات الآجل زبوناً موجوداً مسبقاً من شاشة الزبائن."
    ),
    "Require approval for debt requests": "طلبات الدين تتطلب موافقة",
    "Require approval for settlement requests": "طلبات التسديد تتطلب موافقة",
    "Require approval for payment requests": "طلبات الدفع تتطلب موافقة",
    "Show inventory (New SIM) on sales entry": "عرض المخزون (شريحة جديدة) في شاشة المبيعات",
    "Show phone refresh on sales entry": "عرض تحديث الهاتف في شاشة المبيعات",
    "Show record payment on sales entry": "عرض تسجيل الدفعة في شاشة المبيعات",
    "Refresh link default language": "اللغة الافتراضية لرابط التحديث",
    "Refresh link default theme": "الثيم الافتراضي لرابط التحديث",
    "Public refresh page default language": "اللغة الافتراضية لصفحة التحديث العامة",
    "Public refresh page default theme": "الثيم الافتراضي لصفحة التحديث العامة",
    "Initial language and theme for the public phone-refresh page before the visitor changes them.": (
        "اللغة والثيم الابتدائيان لصفحة التحديث العامة قبل أن يغيّرها الزائر."
    ),
    "Used on first visit before the user picks a language or theme. Personal choices in the header still override these defaults.": (
        "تُستخدم في الزيارة الأولى قبل اختيار المستخدم للغة أو الثيم. الاختيارات الشخصية في الشريط العلوي تبقى لها الأولوية."
    ),
    "Will deduct from customer “%(name)s” stock (%(qty)s available).": (
        "سيُخصم من مخزون الزبون «%(name)s» (%(qty)s متاح)."
    ),
    "Will deduct from main stock (%(qty)s available).": "سيُخصم من المخزون الرئيسي (%(qty)s متاح).",
    "Quantity cannot be negative.": "لا يمكن أن تكون الكمية سالبة.",
    "Multiple customers share the payer name “%(name)s”. Cannot deduct SIM stock.": (
        "أكثر من زبون يشاركون اسم الدافع «%(name)s». لا يمكن خصم مخزون الشرائح."
    ),
    "Sale-linked movements cannot be deleted. Cancel the sale or adjust stock instead.": (
        "لا يمكن حذف حركات مرتبطة ببيع. ألغِ البيع أو عدّل المخزون بدلاً من ذلك."
    ),
    "Error": "خطأ",
    "Not registered": "غير مسجّل",
    "This phone number is not registered in the system.": "رقم الهاتف هذا غير مسجّل في النظام.",
    "Could not read clipboard. Allow paste permission or use Ctrl+V.": (
        "تعذّر قراءة الحافظة. اسمح بإذن اللصق أو استخدم Ctrl+V."
    ),
    "Default language": "اللغة الافتراضية",
    "Default theme": "الثيم الافتراضي",
    "system settings": "إعدادات النظام",
    "System settings": "إعدادات النظام",
    "System settings updated.": "تم تحديث إعدادات النظام.",
    "Refresh phone": "تحديث الهاتف",
    "Phone number": "رقم الهاتف",
    "Close": "إغلاق",
    "Delete this movement? (Does not reverse stock.)": "حذف هذه الحركة؟ (لا يعكس المخزون.)",
    "No product lines yet.": "لا توجد خطوط منتج بعد.",
    "Choose a payment method tile.": "اختر بلاطة طريقة الدفع.",
    "A reason is required.": "السبب مطلوب.",
    "Phone number is required.": "رقم الهاتف مطلوب.",
    "Product not found.": "المنتج غير موجود.",
    "Turn a switch off to skip management approval for that queue. Sales and balances apply immediately.": (
        "عطّل المفتاح لتجاوز موافقة الإدارة على هذا الطابور. تُسجَّل المبيعات والأرصدة فوراً."
    ),
    "On-account sales posted to customers or distributors. When off, they are recorded on the customer account immediately.": (
        "مبيعات الآجل المسجّلة للزبائن أو الموزّعين. عند الإيقاف، تُسجَّل على حساب الزبون مباشرة."
    ),
    "Customer settlement submissions from the sales screen. When off, the payment applies to the balance immediately.": (
        "طلبات تسديد الزبون من شاشة المبيعات. عند الإيقاف، تُطبَّق الدفعة على الرصيد فوراً."
    ),
    "Cash sales awaiting “mark paid”. When off, new cash sales are marked paid as soon as they are saved.": (
        "مبيعات نقدية بانتظار «تعليم مدفوع». عند الإيقاف، تُعلَّم المبيعات النقدية الجديدة مدفوعة عند الحفظ."
    ),
    "Controls what employees see and how sales are recorded on the entry form.": (
        "يتحكّم فيما يراه الموظفون وكيف تُسجَّل المبيعات في نموذج الإدخال."
    ),
    "When off, on-account sales require an existing customer — create customers from the Customers screen.": (
        "عند الإيقاف، تتطلب مبيعات الآجل زبوناً موجوداً — أنشئ الزبائن من شاشة الزبائن."
    ),
    "When off, the New SIM toggle is hidden on sales entry. Inventory elsewhere is unchanged.": (
        "عند الإيقاف، يُخفى خيار شريحة جديدة في إدخال المبيعات. إدارة المخزون في باقي الشاشات دون تغيير."
    ),
    "When off, on-account sales post to the customer immediately without awaiting management approval.": (
        "عند الإيقاف، تُسجَّل مبيعات الآجل على حساب الزبون فوراً دون انتظار موافقة الإدارة."
    ),
    "When off, customer settlement submissions apply to the balance immediately without management approval.": (
        "عند الإيقاف، تُطبَّق طلبات تسديد الزبون على الرصيد فوراً دون موافقة الإدارة."
    ),
    "When off, cash sales are marked paid immediately without appearing in pending payments.": (
        "عند الإيقاف، تُعلَّم المبيعات النقدية مدفوعة فوراً دون الظهور في المدفوعات المعلّقة."
    ),
    "When off, the New SIM option is hidden on the employee sales screen. Inventory management elsewhere is unchanged.": (
        "عند الإيقاف، يُخفى خيار شريحة جديدة في شاشة مبيعات الموظف. إدارة المخزون في باقي الشاشات دون تغيير."
    ),
    "Debt requests require approval": "طلبات الدين تتطلب موافقة",
    "Settlement requests require approval": "طلبات التسديد تتطلب موافقة",
    "Payment requests require approval": "طلبات الدفع تتطلب موافقة",
    "Create customers from sales entry": "إنشاء زبائن من شاشة المبيعات",
    "Inventory (New SIM) on sales entry": "المخزون (شريحة جديدة) في شاشة المبيعات",
    "Phone refresh button on sales entry": "زر تحديث الهاتف في شاشة المبيعات",
    "Record payment button on sales entry": "زر تسجيل الدفعة في شاشة المبيعات",
    "Recorded on account and posted to the customer.": "تم التسجيل على الحساب وإضافته للزبون مباشرة.",
    "Sale recorded and marked paid.": "تم تسجيل البيع واعتباره مدفوعاً.",
    "Payment recorded on the customer account.": "تم تسجيل الدفعة على حساب الزبون.",
    "Recording payments from the sales screen is disabled.": "تسجيل الدفعات من شاشة المبيعات معطّل.",
    "Phone refresh is disabled on the sales screen.": "تحديث الهاتف معطّل في شاشة المبيعات.",
    "Inventory is disabled on the sales screen.": "المخزون معطّل في شاشة المبيعات.",
    # Inventory UI (recent)
    "New quantity": "الكمية الجديدة",
    "Movement type": "نوع الحركة",
    "movement type": "نوع الحركة",
    "Customer stock": "مخزون الزبون",
    "location": "الموقع",
    "SIM stock balance": "رصيد مخزون الشرائح",
    "SIM stock balances": "أرصدة مخزون الشرائح",
    "Main receive": "استلام للمخزون الرئيسي",
    "Allocate to customer": "توزيع على زبون",
    "Return from customer": "مرتجع من زبون",
    "Sale consume": "خصم بيع",
    "Sale reversal": "استرجاع بيع",
    "from balance": "من الرصيد",
    "to balance": "إلى الرصيد",
    "A reason is required for adjustments.": "سبب التسوية مطلوب.",
    "Adjustment amount cannot be zero.": "مبلغ التسوية لا يمكن أن يكون صفراً.",
    "Main stock set.": "تم تعيين المخزون الرئيسي.",
    "Quantity updated.": "تم تحديث الكمية.",
    "Balance adjusted.": "تم تسوية الرصيد.",
    "Invalid adjustment.": "تسوية غير صالحة.",
    "Balance cleared.": "تم تصفير الرصيد.",
    "Balance row deleted.": "تم حذف صف الرصيد.",
    "Movement deleted.": "تم حذف الحركة.",
    "Movements": "الحركات",
    "Back to customer stock list": "العودة إلى قائمة مخزون الزبائن",
    "Move SIMs from main stock to this customer.": "نقل شرائح من المخزون الرئيسي إلى هذا الزبون.",
    "Move SIMs back from this customer to main stock.": "إرجاع شرائح من هذا الزبون إلى المخزون الرئيسي.",
    "No stock for this customer.": "لا يوجد مخزون لهذا الزبون.",
    "SIM stock held with distributors and customers.": "مخزون الشرائح لدى الموزّعين والزبائن.",
    "Receive, adjust, and track main SIM stock by product line.": (
        "استلام وتسوية ومتابعة مخزون الشرائح الرئيسي حسب خط المنتج."
    ),
    "Add plastic SIMs to main inventory for a product line.": (
        "إضافة شرائح بلاستيكية للمخزون الرئيسي لخط منتج."
    ),
    "Full history of SIM stock changes.": "السجل الكامل لتغييرات مخزون الشرائح.",
    "Plastic SIM counts by product line (main stock + with distributors).": (
        "أعداد الشرائح البلاستيكية حسب خط المنتج (رئيسي + عند الموزّعين)."
    ),
    "Testing correction": "تصحيح للاختبار",
    "Clear this balance to zero?": "تصفير هذا الرصيد؟",
    "Clear": "تصفير",
    "Delete this balance row? (Must be zero first.)": "حذف صف الرصيد هذا؟ (يجب أن يكون صفراً أولاً.)",
    "Cleared for testing": "مُصفَّر للاختبار",
    "Clear the balance before deleting it.": "صفِّر الرصيد قبل حذفه.",
    "Adjust": "تعديل",
    "Record": "تسجيل",
    "Filter": "تصفية",
    "With customers": "عند الزبائن",
    "SIM consumed at": "تاريخ خصم الشريحة",
    "SIM deducted from": "خُصمت الشريحة من",
    "Insufficient main SIM stock for %(line)s.": "المخزون الرئيسي للشرائح غير كافٍ لـ %(line)s.",
    "Customer “%(name)s” has no SIM stock for %(line)s.": "الزبون «%(name)s» لا يملك مخزون شرائح لـ %(line)s.",
    "Serial lookup": "بحث برقم الشريحة",
    "Serial numbers (optional)": "أرقام الشرائح (اختياري)",
    "Add serial numbers…": "إضافة أرقام الشرائح…",
    "Serial numbers": "أرقام الشرائح",
    "Paste one serial or ICCID per line. Changes apply immediately.": (
        "الصق رقماً تسلسلياً أو ICCID في كل سطر. التغييرات تُطبَّق فوراً."
    ),
    "Serial count does not match quantity.": "عدد الأرقام لا يطابق الكمية.",
    "One serial or ICCID per line; count must match quantity.": (
        "رقم شريحة أو ICCID في كل سطر؛ يجب أن يطابق العدد."
    ),
    "One per line": "واحد في كل سطر",
    "Serial or ICCID": "الرقم التسلسلي أو ICCID",
    "SIM serial or ICCID": "رقم الشريحة أو ICCID",
    "Search tracked SIM serial numbers and ICCIDs.": "بحث في أرقام الشرائح و ICCID المسجّلة.",
    "Search serial / ICCID…": "بحث برقم الشريحة / ICCID…",
    "No SIM cards found.": "لم يُعثر على شرائح.",
    "SIM cards": "شرائح مسجّلة",
    "SIM card": "شريحة مسجّلة",
    "SIM stock": "مخزون الشرائح",
    "No SIM stock for this customer.": "لا يوجد مخزون شرائح لهذا الزبون.",
    "Manage": "إدارة",
    "Est. value": "القيمة التقديرية",
    "estimated unit cost (SIM)": "تكلفة الوحدة التقديرية (شريحة)",
    "New SIM: stock deducted on approval": "شريحة جديدة: يُخصم المخزون عند الموافقة",
    "Provide exactly %(qty)s serial number(s), one per line.": (
        "أدخل بالضبط %(qty)s رقماً تسلسلياً، واحداً في كل سطر."
    ),
    "Duplicate serial numbers in the list.": "أرقام تسلسلية مكررة في القائمة.",
    "Serial “%(serial)s” is already registered.": "الرقم «%(serial)s» مسجّل مسبقاً.",
    "Serial “%(serial)s” was not found in the expected stock location.": (
        "الرقم «%(serial)s» غير موجود في موقع المخزون المتوقع."
    ),
    # Phone refresh admin help (staff-facing)
    "Arabic display name, e.g. 'تم التحديث'.": "اسم العرض بالعربية، مثال: «تم التحديث».",
    "System-defined statuses cannot be deleted from the UI.": (
        "الحالات المعرّفة من النظام لا يمكن حذفها من الواجهة."
    ),
    "Substring, regex, or JSON path expression depending on match type.": (
        "جزء نصّي أو تعبير منتظم أو مسار JSON حسب نوع المطابقة."
    ),
    "For JSON path / HTTP status: the value to compare against.": (
        "لمسار JSON / حالة HTTP: القيمة المُقارَن بها."
    ),
    "Lower runs first; first match wins.": "الأقل أولوية يُنفَّذ أولاً؛ أول تطابق يفوز.",
    "Where the refresh attempt was triggered from.": "مصدر محاولة التحديث.",
    "First ~500 chars of the raw upstream response (for debugging).": (
        "أول ~500 حرف من رد المزوّد الخام (للتشخيص)."
    ),
    "When off, the public refresh API/page returns the SERVICE_OFF message.": (
        "عند الإيقاف، تُرجع واجهة/صفحة التحديث العامة رسالة SERVICE_OFF."
    ),
    "Per-phone cooldown between successful refreshes (in seconds).": (
        "مهلة بين التحديثات الناجحة لكل رقم (بالثواني)."
    ),
    "Used when DB precheck is disabled. Empty → return ERROR.": (
        "يُستخدم عند تعطيل الفحص المسبق في قاعدة البيانات. فارغ → خطأ."
    ),
    "Max public API requests per IP per minute.": "الحد الأقصى لطلبات API العامة لكل IP في الدقيقة.",
    "Max public API requests per IP per hour.": "الحد الأقصى لطلبات API العامة لكل IP في الساعة.",
    "One origin per line. Empty = allow any origin.": "أصل واحد في كل سطر. فارغ = السماح بأي أصل.",
    "Friendly label for this token (e.g. where it's used).": "وصف مختصر للرمز (مثلاً أين يُستخدم).",
    "sha256 of the raw token; raw value is shown only once on creation.": (
        "sha256 للرمز الخام؛ تُعرض القيمة الأصلية مرة واحدة عند الإنشاء."
    ),
    "First 8 chars of the raw token, for identification in lists.": (
        "أول 8 أحرف من الرمز الخام للتعرّف في القوائم."
    ),
    "submitted by": "مُقدَّم بواسطة",
    "reject reason": "سبب الرفض",
    "Employees": "الموظفين",
    "Employees & payroll": "الموظفون والرواتب",
    "employee": "موظف",
    "employees": "الموظفين",
    "monthly salary": "الراتب الشهري",
    "Salary accrual": "استحقاق راتب",
    "Sales payment received": "دفعة مبيعات مستلمة",
    "Movement statement": "كشف حركات",
    "Received sales payments": "دفعات مبيعات مستلمة",
    "Payment to employee": "دفع لدى موظف",
    "Payment to employee: %(name)s": "دفع لدى موظف: %(name)s",
    "payment to employee": "دفع لدى موظف",
    "Employee who received payment": "الموظف الذي استلم الدفعة",
    "Employee recipient": "موظف المستلم",
    "employee recipient": "موظف المستلم",
    "Add employee": "إضافة موظف",
    "Employee saved.": "تم حفظ الموظف.",
    "Employee updated.": "تم تحديث الموظف.",
    "New employee": "موظف جديد",
    "Edit employee": "تعديل موظف",
    "Run salary accrual": "تشغيل استحقاق الرواتب",
    "Accrue salaries for month": "استحقاق رواتب الشهر",
    "Payroll accounts, balances, and salary accrual.": "حسابات الرواتب والأرصدة واستحقاق الراتب.",
    "Shop owes the employee.": "المحل مدين للموظف.",
    "Employee holds cash for the shop.": "الموظف يحتفظ بمبلغ نيابة عن المحل.",
    "Select the employee who received the payment.": "اختر الموظف الذي استلم الدفعة.",
    "You can only edit or delete your last %(count)s entries.": "يمكنك تعديل أو حذف آخر %(count)s إدخالات فقط.",
    "Sales you entered. You can edit or delete only your last 10 entries.": "مبيعاتك المُدخلة. يمكنك تعديل أو حذف آخر 10 إدخالات فقط.",
    "Salaries": "رواتب",
    "Salary: %(name)s — %(month)s": "راتب: %(name)s — %(month)s",
    "Auto-created from salary accrual.": "أُنشئ تلقائياً من استحقاق الراتب.",
    "You are not registered as a payroll employee.": "حسابك غير مسجّل في نظام الموظفين.",
    "Payment will be recorded to your account: %(name)s": "ستُسجّل الدفعة في حسابك: %(name)s",
    "Your balance:": "رصيدك:",
    "Sale recorded; payment credited to employee ledger.": "تم تسجيل البيع؛ أُضيفت الدفعة إلى كشف الموظف.",
    "Sale recorded; employee payment awaits management approval.": "تم تسجيل البيع؛ دفعة الموظف بانتظار موافقة الإدارة.",
    "No payroll employees configured.": "لم يُعدّ موظفون في نظام الرواتب.",
    "Salary accrual complete: %(count)s new entries for %(month)s.": "اكتمل استحقاق الرواتب: %(count)s قيداً جديداً لشهر %(month)s.",
    # Remaining UI / model strings (2026 pass)
    "Monthly salary": "الراتب الشهري",
    "Not set": "غير محدّد",
    "Refresh": "تحديث",
    "Refreshing…": "جاري التحديث…",
    "Serial / ICCID": "الرقم التسلسلي / ICCID",
    "serial or ICCID": "رقم الشريحة أو ICCID",
    "Sales last %(n)s day": "مبيعات آخر %(n)s يوم",
    "Refresh phone number": "تحديث رقم الهاتف",
    "Enter a phone number.": "أدخل رقم الهاتف.",
    "phone refresh provider": "مزوّد تحديث الهاتف",
    "Payment to employee: ON": "دفع لدى موظف: مفعّل",
    "%(n)s item needs attention": "عنصر يحتاج متابعة",
    "Sale is not an employee payment sale.": "هذه ليست مبيعة دفع لدى موظف.",
    "Salary accrual row is missing salary month.": "صف استحقاق الراتب بلا شهر محدّد.",
    "Only salary accrual rows can create expenses.": "المصروفات تُنشأ من صفوف استحقاق الراتب فقط.",
    "First day of the month for salary accrual rows.": "أول يوم في الشهر لصفوف استحقاق الراتب.",
    "Optional. Used for SIM inventory valuation reports.": "اختياري. لحساب قيمة مخزون الشرائح في التقارير.",
    "Positive credits the employee; negative debits them.": "موجب يُضاف للموظف؛ سالب يُخصم منه.",
    "Employee payment sales require an employee recipient.": "مبيعات الدفع لدى موظف تتطلب موظفاً مستلماً.",
    "A sale cannot be both on-account and paid via employee.": "لا يمكن أن تكون المبيعة آجل ودفع لدى موظف معاً.",
    "Choose either on-account or payment to employee, not both.": "اختر إما الآجل أو الدفع لدى موظف، وليس الاثنين.",
    "Positive: shop owes employee. Negative: employee owes shop.": "موجب: المحل مدين للموظف. سالب: الموظف مدين للمحل.",
    "Positive: the shop owes the employee (salary / credits). Negative: the employee holds cash on behalf of the shop.": (
        "موجب: المحل مدين للموظف (راتب/دائن). سالب: الموظف يحتفظ بمبلغ نيابة عن المحل."
    ),
    "Also runnable via: python manage.py accrue_employee_salaries": (
        "يمكن تشغيله أيضاً عبر: python manage.py accrue_employee_salaries"
    ),
    "Optional. Links this sale to a tracked SIM card at approval time.": (
        "اختياري. ربط البيع بشريحة مسجّلة عند الموافقة."
    ),
    "Cash received by an employee on behalf of the shop; no payment method at entry.": (
        "نقد استلمه موظف نيابة عن المحل؛ بلا طريقة دفع عند الإدخال."
    ),
    "Customer not found. Check the name or ask management to add the customer.": (
        "الزبون غير موجود. تحقّق من الاسم أو اطلب من الإدارة إضافته."
    ),
    "Phone must be 10 digits and start with 050, 051, 052, 053, 054, 055, or 058.": (
        "يجب أن يكون الرقم 10 خانات ويبدأ بـ 050 أو 051 أو 052 أو 053 أو 054 أو 055 أو 058."
    ),
    "Only phone or shipment numbers already saved in sales can be refreshed. No public-site rate limits apply here.": (
        "يمكن تحديث أرقام الهاتف أو الشحنة المسجّلة في المبيعات فقط. لا تُطبَّق حدود الموقع العام هنا."
    ),
    "Which upstream API runs phone refresh for this company. Leave empty to match from the company name (legacy).": (
        "مزوّد API الذي ينفّذ التحديث لهذه الشركة. اتركه فارغاً للمطابقة من اسم الشركة (قديم)."
    ),
    "English slug used in API responses, e.g. 'refreshed', 'queued'. Lowercase letters, digits, dashes and underscores only.": (
        "رمز إنجليزي في ردود API، مثل refreshed أو queued. أحرف لاتينية صغيرة وأرقام وشرطات وشرطة سفلية فقط."
    ),
    "When on, look up the phone in sales_sale.reference_number to choose the provider. When off, every refresh routes through ``default_provider``.": (
        "عند التفعيل: البحث عن الرقم في المبيعات لاختيار المزوّد. عند الإيقاف: كل التحديثات عبر المزوّد الافتراضي."
    ),
    "Include last_refresh_at / seconds_since_last_refresh in the public API response.": (
        "إدراج last_refresh_at و seconds_since_last_refresh في رد API العام."
    ),
    "When ON, the public API endpoint requires a valid Authorization: Bearer <token> header.": (
        "عند التفعيل: نقطة API العامة تتطلب ترويسة Authorization: Bearer <token> صالحة."
    ),
    "When ON, the public /phone-refresh/ form remains accessible without a token even when require_token is ON.": (
        "عند التفعيل: نموذج /phone-refresh/ العام يبقى متاحاً بلا رمز حتى مع تفعيل require_token."
    ),
    "Ledger": "كشف الحركات",
    "Settled.": "مساوٍ.",
    "No employees yet.": "لا يوجد موظفون بعد.",
    "No sales payments yet.": "لا توجد دفعات مبيعات بعد.",
    "No ledger entries.": "لا توجد حركات في الكشف.",
    "Username or name…": "اسم المستخدم أو الاسم…",
    "Invalid month.": "شهر غير صالح.",
    "User": "المستخدم",
    "Balance": "الرصيد",
    "Amount cannot be zero.": "المبلغ لا يمكن أن يكون صفراً.",
    "Mark as on account": "تسجيل آجل",
    "On account: ON": "آجل: مفعّل",
    "Where did you receive the money?": "أين استلمت المبلغ؟",
    "Move SIMs from main stock to a customer.": "نقل شرائح من المخزون الرئيسي إلى زبون.",
    "Move SIMs back from this customer to main stock.": "إرجاع شرائح من هذا الزبون إلى المخزون الرئيسي.",
    "Payment method is required for this sale.": "طريقة الدفع مطلوبة لهذه المبيعة.",
    "Close": "إغلاق",
    "Adjust (+/-)": "تسوية (+/-)",
    "Set main stock": "تعيين المخزون الرئيسي",
    "employee ledger entry": "حركة كشف موظف",
    "employee ledger entries": "حركات كشف الموظف",
    "expense": "مصروف",
    "Paid via employee": "دفع لدى موظف",
    "On account": "آجل",
    "View all": "عرض الكل",
    "My entries": "إدخالاتي",
    "Edit entry": "تعديل الإدخال",
    "Delete": "حذف",
    "Delete this entry permanently? Supplier balance will be corrected. This cannot be undone.": (
        "حذف هذا الإدخال نهائياً؟ يُصحَّح رصيد المورّد. لا يمكن التراجع."
    ),
    "Entry was permanently removed.": "أُزيل الإدخال نهائياً.",
    "Sale updated.": "تم تحديث البيع.",
    "Company, product and eSIM flag can't be changed here. If those need fixing, delete this entry and create a new one.": (
        "لا يمكن تغيير الشركة أو المنتج أو خيار eSIM هنا. للتصحيح، احذف الإدخال وأنشئ واحداً جديداً."
    ),
    "Pick a payment method.": "اختر طريقة الدفع.",
    "eSIM: extra cost applied to this sale": "eSIM: تكلفة إضافية على هذه المبيعة",
}


def _strip_fuzzy_headers(head: str) -> str:
    head = re.sub(r"^#, fuzzy[^\n]*\n", "", head, flags=re.MULTILINE)
    head = re.sub(r"^#\| [^\n]*\n", "", head, flags=re.MULTILINE)
    return head


def _entry_has_translation(block: str) -> bool:
    for line in block.splitlines():
        if line.startswith("msgstr[") or line.startswith('msgstr "'):
            value = line.split(" ", 1)[1].strip().strip('"')
            if value:
                return True
    return False


def unfuzzy_translated_entries(text: str) -> tuple[str, int]:
    """Drop fuzzy flags on entries that already have Arabic msgstr."""
    unfuzzied = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal unfuzzied
        block = match.group(0)
        if "#, fuzzy" not in block or not _entry_has_translation(block):
            return block
        head = match.group("head")
        if "#, fuzzy" in head:
            unfuzzied += 1
            head = _strip_fuzzy_headers(head)
        return head + match.group("msgid") + match.group("msgstr") + "\n"

    text = _ENTRY_RE.sub(repl, text)
    return text, unfuzzied


def _po_unquote(block: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    return "".join(p.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\") for p in parts)


def _po_quote_msgstr(text: str) -> str:
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    if len(text) <= 72 and "\n" not in text:
        return f'msgstr "{text}"'
    lines = [text[i : i + 72] for i in range(0, len(text), 72)] if "\n" not in text else text.split("\n")
    out = ['msgstr ""']
    for line in lines:
        out.append(f'"{po_escape(line)}"')
    return "\n".join(out)


_ENTRY_RE = re.compile(
    r"(?P<head>(?:^#.*\n)*)"
    r"(?P<msgid>msgid\s+(?:\"(?:[^\"\\]|\\.)*\"(?:\n\"(?:[^\"\\]|\\.)*\")*)\s*\n)"
    r'(?P<msgstr>msgstr\s+(?:"(?:[^"\\]|\\.)*"(?:\n"(?:[^"\\]|\\.)*")*)\s*)',
    re.MULTILINE,
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    po_path = root / "locale" / "ar" / "LC_MESSAGES" / "django.po"
    text = po_path.read_text(encoding="utf-8")
    text = text.replace('"Language: \\n"', '"Language: ar\\n"', 1)

    updated = 0
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        nonlocal updated
        head = match.group("head")
        msgid_block = match.group("msgid")
        msgstr_block = match.group("msgstr")
        key = _po_unquote(msgid_block)
        if not key or key not in AR:
            return match.group(0)
        head = _strip_fuzzy_headers(head)
        new_msgstr = _po_quote_msgstr(AR[key])
        updated += 1
        return head + msgid_block + new_msgstr + "\n"

    text = _ENTRY_RE.sub(repl, text)

    text, unfuzzied = unfuzzy_translated_entries(text)

    for en in AR:
        if f'msgid "{po_escape(en)}"' not in text and f'msgid ""\n"{po_escape(en[:40])}' not in text:
            # multiline msgid check
            if en not in {_po_unquote(m.group("msgid")) for m in _ENTRY_RE.finditer(text)}:
                missing.append(en)

    po_path.write_text(text, encoding="utf-8")
    print(f"Updated {updated} entries in {po_path}")
    if unfuzzied:
        print(f"Unfuzzy: {unfuzzied} translated entries activated")
    if missing:
        print(f"Note: {len(missing)} AR keys not found in .po (run makemessages first):")
        for m in missing[:10]:
            print(f"  - {m[:70]}...")


if __name__ == "__main__":
    main()
