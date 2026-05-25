/* =====================================================================
   Pro Sim — i18n, theme, validation, animated background
   Adapted from the original Flask UI to talk to the Django
   `phone_refresh:public_api` endpoint, which returns:
     {
       status: "refreshed" | "wait" | "not_found" | "service_off" | "error",
       message: { title, body },
       last_refresh_at?: ISO string,
       seconds_since_last_refresh?: number
     }
   ===================================================================== */

const TRANSLATIONS = {
    ar: {
        "page.title": "تحديث الهاتف - Pro Sim",
        "app.title": "مرحبا بك",
        "app.subtitle": "الرجاء ادخال الرقم المراد تحديثه",
        "form.label": "رقم الهاتف",
        "form.submit": "تحديث",
        "form.submitting": "جارِ التنفيذ",
        "validation.length": "الرقم يجب أن يكون 10 أرقام",
        "validation.prefix": "الرقم يجب أن يبدأ بـ 05",
        "validation.digits": "أرقام فقط",
        "validation.ok": "جاهز للإرسال",
        "msg.success": "تم تحديث الرقم بنجاح",
        "msg.wait": "لا يمكن التحديث الآن، الرجاء الانتظار",
        "msg.wait_with_time": "{body} (آخر تحديث منذ {time})",
        "msg.notfound": "الرقم غير موجود",
        "msg.error": "حدث خطأ غير متوقع",
        "msg.network": "تعذّر الاتصال بالخادم",
        "msg.sending": "جاري الإرسال...",
        "msg.ratelimit": "عدد كبير من المحاولات، الرجاء الانتظار قليلاً",
        "msg.session": "انتهت الجلسة، يتم التحديث...",
        "msg.service_off": "الخدمة متوقفة حالياً",
        "time.hour": "ساعة",
        "time.hours": "ساعات",
        "time.minute": "دقيقة",
        "time.minutes": "دقائق",
        "time.and": "و",
        "time.lessThanMinute": "أقل من دقيقة",
        "bg.stars": "نجوم وشهب",
        "bg.network": "شبكة بيانات",
        "bg.dots": "نقاط ناعمة",
        "footer.tagline": "أكبر شبكة موزعين في قطاع غزة"
    },
    en: {
        "page.title": "Phone Refresh - Pro Sim",
        "app.title": "Welcome",
        "app.subtitle": "Please enter the number you want to refresh",
        "form.label": "Phone Number",
        "form.submit": "Refresh",
        "form.submitting": "Processing",
        "validation.length": "Number must be 10 digits",
        "validation.prefix": "Number must start with 05",
        "validation.digits": "Numbers only",
        "validation.ok": "Ready",
        "msg.success": "Number refreshed successfully",
        "msg.wait": "You can't refresh right now, please wait",
        "msg.wait_with_time": "{body} (last refresh was {time} ago)",
        "msg.notfound": "Number not found",
        "msg.error": "An unexpected error occurred",
        "msg.network": "Failed to connect to the server",
        "msg.sending": "Sending request...",
        "msg.ratelimit": "Too many attempts, please wait a moment",
        "msg.session": "Session expired, reloading...",
        "msg.service_off": "Service is currently offline",
        "time.hour": "hour",
        "time.hours": "hours",
        "time.minute": "minute",
        "time.minutes": "minutes",
        "time.and": "and",
        "time.lessThanMinute": "less than a minute",
        "bg.stars": "Stars & meteors",
        "bg.network": "Data network",
        "bg.dots": "Soft dots",
        "footer.tagline": "The largest distributor network in the Gaza Strip"
    }
};

const STORAGE = { LANG: "rn_lang", THEME: "rn_theme" };

const META = (name) => (document.querySelector(`meta[name="${name}"]`) || {}).content || "";
const API_URL = META("refresh-api");

const $ = (s) => document.querySelector(s);

const html = document.documentElement;
const langToggle = $("#lang-toggle");
const langLabel = langToggle.querySelector("[data-lang-label]");
const themeToggle = $("#theme-toggle");
const bgToggle = $("#bg-toggle");
const form = $("#refresh-form");
const inputWrap = $(".input-wrap");
const phoneInput = $("#phone");
const fieldHint = $("#field-hint");
const submitBtn = $("#submit-btn");
const submitLabel = submitBtn.querySelector(".cta-label");
const alertBox = $("#alert");
const alertText = alertBox.querySelector(".alert-text");
const footerText = $("#footer-text");

let currentLang = localStorage.getItem(STORAGE.LANG) || "ar";
let currentTheme = localStorage.getItem(STORAGE.THEME) || "dark";

function t(key) {
    return (TRANSLATIONS[currentLang] && TRANSLATIONS[currentLang][key]) || key;
}

/* ---------- CSRF ---------- */

function getCsrfToken() {
    const el = form.querySelector('input[name="csrfmiddlewaretoken"]');
    if (el && el.value) return el.value;
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
}

/* ---------- i18n ---------- */

function applyLanguage(lang) {
    currentLang = lang;
    localStorage.setItem(STORAGE.LANG, lang);

    html.setAttribute("lang", lang);
    html.setAttribute("dir", lang === "ar" ? "rtl" : "ltr");

    document.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = t(el.getAttribute("data-i18n"));
    });

    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
        el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
    });

    document.title = t("page.title");
    langLabel.textContent = lang === "ar" ? "EN" : "عربي";
    footerText.textContent = `Pro Sim ${new Date().getFullYear()}`;

    validatePhone(phoneInput.value, false);
}

/* ---------- Theme ---------- */

function applyTheme(theme) {
    currentTheme = theme;
    localStorage.setItem(STORAGE.THEME, theme);
    html.setAttribute("data-theme", theme);
    requestAnimationFrame(() => {
        if (window.BG && typeof window.BG.refreshColor === "function") {
            window.BG.refreshColor();
        }
    });
}

/* ---------- Validation ---------- */

function validatePhone(value, markInvalid = true) {
    inputWrap.classList.remove("invalid", "valid");
    fieldHint.classList.remove("error", "ok");

    if (!value) {
        fieldHint.textContent = "";
        return { ok: false, silent: true };
    }

    if (!/^\d+$/.test(value)) {
        fieldHint.textContent = t("validation.digits");
        fieldHint.classList.add("error");
        if (markInvalid) inputWrap.classList.add("invalid");
        return { ok: false };
    }

    if (!value.startsWith("05")) {
        fieldHint.textContent = t("validation.prefix");
        fieldHint.classList.add("error");
        if (markInvalid) inputWrap.classList.add("invalid");
        return { ok: false };
    }

    if (value.length !== 10) {
        fieldHint.textContent = t("validation.length");
        if (markInvalid) inputWrap.classList.add("invalid");
        return { ok: false };
    }

    fieldHint.textContent = t("validation.ok");
    fieldHint.classList.add("ok");
    inputWrap.classList.add("valid");
    return { ok: true };
}

/* ---------- Alert ---------- */

function showAlert(text, type) {
    alertText.textContent = text;
    alertBox.className = "alert";
    void alertBox.offsetWidth;
    alertBox.classList.add("visible", type);
}

function hideAlert() {
    alertBox.className = "alert";
    alertText.textContent = "";
}

/* ---------- Loading ---------- */

function setLoading(loading) {
    submitBtn.disabled = loading;
    submitBtn.classList.toggle("loading", loading);
    submitLabel.textContent = loading ? t("form.submitting") : t("form.submit");
}

/* ---------- Submit ---------- */

// Map Django backend status codes to the alert visual variants used by
// the original Flask UI (success / warn / info / error).
const STATUS_TO_TYPE = {
    refreshed: "success",
    wait: "warn",
    not_found: "error",
    error: "error",
    service_off: "info",
};

const STATUS_TO_FALLBACK_KEY = {
    refreshed: "msg.success",
    wait: "msg.wait",
    not_found: "msg.notfound",
    error: "msg.error",
    service_off: "msg.service_off",
};

function formatElapsed(seconds) {
    if (!Number.isFinite(seconds) || seconds < 60) {
        return t("time.lessThanMinute");
    }
    const totalMinutes = Math.floor(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;

    const hourWord = () => (hours === 1 ? t("time.hour") : t("time.hours"));
    const minuteWord = () => (minutes === 1 ? t("time.minute") : t("time.minutes"));

    if (hours > 0 && minutes > 0) {
        return `${hours} ${hourWord()} ${t("time.and")} ${minutes} ${minuteWord()}`;
    }
    if (hours > 0) {
        return `${hours} ${hourWord()}`;
    }
    return `${minutes} ${minuteWord()}`;
}

function buildMessage(status, message, secondsSinceLastRefresh) {
    const body = (message && message.body) || t(STATUS_TO_FALLBACK_KEY[status] || "msg.error");
    if (status === "wait" && typeof secondsSinceLastRefresh === "number" && secondsSinceLastRefresh > 0) {
        return t("msg.wait_with_time")
            .replace("{body}", body)
            .replace("{time}", formatElapsed(secondsSinceLastRefresh));
    }
    return body;
}

async function submitRefresh(number) {
    setLoading(true);
    showAlert(t("msg.sending"), "info");

    try {
        const body = {
            phone_number: number,
            client: "web",
            // Honeypot field name matches the Django view (`website`).
            website: (document.getElementById("hp-website") || {}).value || "",
        };

        const res = await fetch(API_URL, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRFToken": getCsrfToken(),
                "X-Requested-With": "XMLHttpRequest",
            },
            body: JSON.stringify(body),
        });

        let data = {};
        try { data = await res.json(); } catch (_) {}

        if (res.status === 429) {
            const fallback = t("msg.ratelimit");
            const msg = (data && data.message && data.message.body) || fallback;
            showAlert(msg, "error");
            return;
        }

        if (res.status === 403 && data && data.reload) {
            showAlert(t("msg.session"), "error");
            setTimeout(() => window.location.reload(), 1400);
            return;
        }

        const status = (data && data.status) || "error";
        const type = STATUS_TO_TYPE[status] || "error";
        const message = buildMessage(status, data && data.message, data && data.seconds_since_last_refresh);
        showAlert(message, type);
    } catch (_) {
        showAlert(t("msg.network"), "error");
    } finally {
        setLoading(false);
    }
}

/* ---------- Events ---------- */

phoneInput.addEventListener("input", () => {
    phoneInput.value = phoneInput.value.replace(/\D/g, "").slice(0, 10);
    validatePhone(phoneInput.value);
});

phoneInput.addEventListener("focus", hideAlert);

form.addEventListener("submit", (e) => {
    e.preventDefault();
    const number = phoneInput.value.trim();
    const result = validatePhone(number);
    if (!result.ok) {
        showAlert(fieldHint.textContent || t("validation.length"), "error");
        phoneInput.focus();
        return;
    }
    submitRefresh(number);
});

themeToggle.addEventListener("click", () => {
    applyTheme(currentTheme === "light" ? "dark" : "light");
});

langToggle.addEventListener("click", () => {
    applyLanguage(currentLang === "ar" ? "en" : "ar");
});

const bgMenu = $("#bg-menu");
const bgOptions = bgMenu ? Array.from(bgMenu.querySelectorAll(".bg-option")) : [];

function markActiveBg() {
    if (!window.BG || !bgOptions.length) return;
    const cur = window.BG.current();
    bgOptions.forEach((btn) => {
        const on = btn.getAttribute("data-bg") === cur;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-checked", on ? "true" : "false");
    });
}

function openBgMenu() {
    if (!bgMenu) return;
    bgMenu.hidden = false;
    bgToggle.setAttribute("aria-expanded", "true");
    markActiveBg();
}

function closeBgMenu() {
    if (!bgMenu) return;
    bgMenu.hidden = true;
    bgToggle.setAttribute("aria-expanded", "false");
}

if (bgToggle && bgMenu) {
    bgToggle.addEventListener("click", (e) => {
        e.stopPropagation();
        if (bgMenu.hidden) openBgMenu();
        else closeBgMenu();
    });

    bgOptions.forEach((btn) => {
        btn.addEventListener("click", () => {
            const name = btn.getAttribute("data-bg");
            if (window.BG && name) window.BG.set(name);
            markActiveBg();
            closeBgMenu();
        });
    });

    document.addEventListener("click", (e) => {
        if (!bgMenu.hidden && !bgMenu.contains(e.target) && e.target !== bgToggle) {
            closeBgMenu();
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeBgMenu();
    });
}

/* ---------- Init ---------- */

applyTheme(currentTheme);
applyLanguage(currentLang);
