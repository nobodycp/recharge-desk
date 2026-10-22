"""Areen agent portal over HTTP (uniGUI), not a browser.

The first login from a new device asks for an SMS code. After that the portal
trusts the saved browser data (``ARN_Session`` and ``SIDX_CXR``). A later
password login sends those cookies and does not ask for a new code, including
after their date has passed. A successful login replaces them with a fresh pair.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from providers.accounts import Credentials, load_extra
from providers.crypto import encrypt

PROVIDER_KEY = "areen"
LOGIN_PAGE = "https://agents.areen.net/AreenWeb.dll/"
EVENT_URL = "https://agents.areen.net/areenweb.dll/HandleEvent"
REQUEST_TIMEOUT = 40
PENDING_TTL_SECONDS = 10 * 60
# Renew while the saved cookies are still the ones the portal last issued.
RENEW_BEFORE_SECONDS = 24 * 3600
DEVICE_COOKIE_NAMES = ("ARN_Session", "SIDX_CXR")
PENDING_KEY = "provider:areen:pending-login"

OTP_SENT = "تم إرسال رمز التحقق إلى الموبايل. أدخله ثم أعد الاختبار."
OTP_REJECTED = "رمز التحقق غير صحيح."
OTP_EXPIRED = "انتهت خطوة التحقق. أعد الاختبار لإرسال رمز جديد."
DEVICE_UNKNOWN = "هذا الجهاز غير معرّف. ابدأ اختبار تسجيل الدخول لإرسال رمز التحقق."
LOGIN_FAILED = "تعذّر تسجيل الدخول."
PAGE_FAILED = "تعذّر فتح صفحة الدخول."
FORM_FAILED = "لم تصل نافذة رمز التحقق."
NO_SESSION = "تم الدخول لكن الجلسة لم تُحفظ."
NOT_FOUND_MESSAGE = "الزبون / الاشتراك غير موجود."


def login_needs_otp(error: str) -> bool:
    """True when the portal is waiting for the SMS code, or rejected the last one."""
    text = error or ""
    return OTP_SENT in text or OTP_REJECTED in text

_COMPONENT = re.compile(r'_cdo_\("([^"]+)",([A-Za-z0-9]+)')
_LOGIN_WINDOW = re.compile(r'([A-Za-z0-9]+)\.nm="\1";_cdo_\("frmNewLogin"\)')
_VERIFY_WINDOW = re.compile(r"([A-Za-z0-9]+)=new Ext\.window\.Window")
_SCRIPT_COOKIE = re.compile(r'document\.cookie\s*=\s*"([^"]*)"')


class AreenError(Exception):
    """The portal refused the call or the account cannot sign in."""


class AreenOtpRequired(AreenError):
    """An SMS code was sent. The next login attempt should submit it at once."""

    retry_without_cooldown = True


class AreenNotFound(AreenError):
    """The portal has no subscription for this number."""


@dataclass
class PortalPage:
    session_id: str
    window: str
    username_field: str
    password_field: str
    login_button: str


def _component_map(text: str) -> dict[str, str]:
    return {name: obj for name, obj in _COMPONENT.findall(text)}


def parse_login_page(text: str, session_id: str) -> PortalPage:
    components = _component_map(text)
    window = _LOGIN_WINDOW.search(text)
    missing = [
        name
        for name in ("edtUserName", "edtPassword", "btnLogin")
        if name not in components
    ]
    if not session_id or window is None or missing:
        raise AreenError(PAGE_FAILED)
    return PortalPage(
        session_id=session_id,
        window=window.group(1),
        username_field=components["edtUserName"],
        password_field=components["edtPassword"],
        login_button=components["btnLogin"],
    )


def parse_verify_form(text: str) -> tuple[str, str, str]:
    """Return ``(window, code field, save button)`` from the SMS dialog."""
    components = _component_map(text)
    window = None
    for match in _VERIFY_WINDOW.finditer(text):
        chunk = text[match.end() :]
        end = chunk.find(");")
        if "frmVerifyCodeSMS" in chunk[: end if end != -1 else 200]:
            window = match.group(1)
            break
    if window is None:
        found = _VERIFY_WINDOW.search(text)
        window = found.group(1) if found else None
    if window is None or "edtCode" not in components or "btnSave" not in components:
        raise AreenError(FORM_FAILED)
    return window, components["edtCode"], components["btnSave"]


def _field_state(name: str, value: str, state: str = "0") -> str:
    """One uniGUI field in the fingerprint (``STX`` + state + value)."""
    encoded = quote("\x02" + state + "\x02" + "\x02" + value, safe="")
    return f"&{name}={encoded}"


class AreenPortal:
    def __init__(self, http: requests.Session | None = None):
        self.http = http or requests.Session()
        self.http.headers.setdefault("User-Agent", "Mozilla/5.0")
        self._seq = 0

    def open_login(self) -> PortalPage:
        response = self.http.get(LOGIN_PAGE, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        session_id = (response.headers.get("session_id") or "").strip()
        return parse_login_page(response.text, session_id)

    def sign_in(
        self,
        page: PortalPage,
        username: str,
        password: str,
        *,
        prompt_device: bool,
    ) -> str:
        """Password login. Returns the click response body.

        Raises ``AreenOtpRequired`` after the portal has sent the SMS, when
        ``prompt_device`` is set. Otherwise an unknown device is an error and
        no SMS is sent.
        """
        self._seq = 0
        client_info = quote("br=1;os=4;bv=120;ww=1280;wh=800", safe="")
        self._event(page, page.window, "cinfo", extra=f"ci={client_info}")
        self._event(page, page.window, "show")
        fingerprint = _field_state(page.username_field, username) + _field_state(
            page.password_field, password
        )
        response = self._event(
            page,
            page.login_button,
            "click",
            extra=f"this={page.login_button}",
            fingerprint=fingerprint,
        )
        if "MainForm" in response:
            return response
        if "غير معرف" not in response:
            raise AreenError(LOGIN_FAILED)
        if not prompt_device:
            raise AreenError(DEVICE_UNKNOWN)
        verified = self._event(
            page,
            page.window,
            "callback",
            extra="ButtonID=yes",
            in_callback=True,
        )
        window, code_field, save_button = parse_verify_form(verified)
        save_pending(
            {
                "session_id": page.session_id,
                "seq": self._seq,
                "window": window,
                "code_field": code_field,
                "save_button": save_button,
                "cookies": _jar_dict(self.http),
            }
        )
        raise AreenOtpRequired(OTP_SENT)

    def submit_code(self, code: str) -> None:
        pending = load_pending()
        if not pending:
            raise AreenError(OTP_EXPIRED)
        _apply_jar(self.http, pending.get("cookies") or {})
        page = PortalPage(
            session_id=str(pending["session_id"]),
            window=str(pending["window"]),
            username_field="",
            password_field="",
            login_button="",
        )
        self._seq = int(pending["seq"])
        code_field = str(pending["code_field"])
        fingerprint = _field_state(code_field, code.strip())
        # The portal stores the typed code on the field's change event.
        # The save click alone leaves the server-side value empty.
        self._event(
            page,
            code_field,
            "change",
            extra=f"this={code_field}",
            fingerprint=fingerprint,
            in_callback=True,
        )
        response = self._event(
            page,
            str(pending["save_button"]),
            "click",
            extra=f"this={pending['save_button']}",
            fingerprint=fingerprint,
            in_callback=True,
        )
        pending["seq"] = self._seq
        save_pending(pending)
        if "MainForm" not in response:
            raise AreenError(OTP_REJECTED)
        clear_pending()

    def open_subscriber_search(self, session_id: str, script: str) -> str:
        """Open «اشتراك وشحن». A password login lands on the menu, not the search."""
        from providers.adapters.areen.number_info import component_map, main_window

        names = component_map(script)
        if "edtCelNum" in names:
            return script
        button = names.get("btnMMtfaseel")
        window = main_window(script, "btnMMtfaseel")
        if not session_id or not button or not window:
            raise AreenError(FORM_FAILED)
        page = PortalPage(session_id, window, "", "", "")
        opened = self._event(page, button, "click", extra=f"this={button}")
        if "edtCelNum" not in component_map(opened):
            raise AreenError(FORM_FAILED)
        return opened

    def read_subscription(self, session: dict, phone: str) -> dict:
        """Look ``phone`` up on an already open portal session.

        ``session`` keeps ``session_id``, ``window``, ``seq`` and ``names``.
        The sequence counter is written back so the next check continues it.
        """
        from providers.adapters.areen.number_info import (
            DETAILS_BUTTON,
            button_id,
            error_toast,
            fields_from_parts,
            missing_subscription,
            root_window,
            store_ids,
        )

        self._seq = int(session.get("seq") or 0)
        names = session.get("names") or {}
        page = PortalPage(
            session_id=str(session.get("session_id") or ""),
            window=str(session.get("window") or ""),
            username_field="",
            password_field="",
            login_button="",
        )
        phone_field = str(names.get("edtCelNum") or "")
        search = str(names.get("btnFind") or "")
        details = str(names.get("btnCstmrDLYButton") or "")
        if not page.session_id or not page.window or not phone_field or not search or not details:
            raise AreenError(FORM_FAILED)
        phone = phone.strip()
        # Empty the field first so a repeat lookup still returns every label.
        self._event(
            page,
            phone_field,
            "change",
            extra=f"This={phone_field}&newValue=&oldValue=",
            fingerprint=_field_state(phone_field, "", state="1"),
        )
        self._event(
            page,
            phone_field,
            "change",
            extra=f"This={phone_field}&newValue={quote(phone, safe='')}&oldValue=",
            fingerprint=_field_state(phone_field, phone, state="2"),
        )
        found = self._event(page, search, "click", extra=f"this={search}")
        session["seq"] = self._seq
        if missing_subscription(found):
            raise AreenNotFound(NOT_FOUND_MESSAGE)
        toast = error_toast(found)
        if toast:
            raise AreenError(toast)
        customer = self._event(page, details, "click", extra=f"this={details}")
        customer_rows: list[dict] = []
        for store in store_ids(customer):
            customer_rows.extend(self._store_rows(page.session_id, store))
        opened = button_id(customer, DETAILS_BUTTON)
        if opened:
            popup = PortalPage(
                session_id=page.session_id,
                window=root_window(customer) or page.window,
                username_field="",
                password_field="",
                login_button="",
            )
            usage = self._event(
                popup,
                opened,
                "click",
                extra=f"this={opened}",
                in_callback=True,
            )
        else:
            usage = customer
        rows: list[dict] = []
        for store in store_ids(usage):
            rows.extend(self._store_rows(page.session_id, store))
        session["seq"] = self._seq
        fields = fields_from_parts(names, found, usage, rows, customer_rows)
        if not fields.get("status") and not fields.get("plan_name"):
            raise AreenError(FORM_FAILED)
        return fields

    def _store_rows(self, session_id: str, store: str) -> list[dict]:
        response = self.http.post(
            f"https://agents.areen.net/areenweb.dll/HandleEvent?IsEvent=1&Obj={store}&Evt=data",
            data="page=1&start=0&limit=25",
            headers={
                "_S_ID": session_id,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": LOGIN_PAGE,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            raise AreenError(LOGIN_FAILED)
        try:
            payload = response.json()
        except ValueError:
            return []
        rows = payload.get("rows") if isinstance(payload, dict) else None
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def device_cookies(self) -> dict[str, Any] | None:
        found: dict[str, str] = {}
        expires_at = None
        for cookie in self.http.cookies:
            if cookie.name not in DEVICE_COOKIE_NAMES:
                continue
            found[cookie.name] = cookie.value
            if cookie.expires:
                expires_at = (
                    cookie.expires if expires_at is None else min(expires_at, cookie.expires)
                )
        if set(found) != set(DEVICE_COOKIE_NAMES):
            return None
        if expires_at is None:
            expires_at = time.time() + 10 * 24 * 3600
        return {"cookies": found, "expires_at": float(expires_at)}

    def _event(
        self,
        page: PortalPage,
        obj: str,
        event: str,
        *,
        extra: str = "",
        fingerprint: str = "",
        in_callback: bool = False,
    ) -> str:
        parts = ["Ajax=1", "IsEvent=1", f"Obj={obj}", f"Evt={event}"]
        if extra:
            parts.append(extra.lstrip("&"))
        parts.append(f"_S_ID={page.session_id}")
        if fingerprint:
            parts.append("_fp_=" + quote(fingerprint, safe=""))
        parts.append(f"_seq_={self._seq:x}")
        self._seq += 1
        if in_callback:
            parts.append("_a_=1")
        parts.append(f"_uo_={page.window}")
        response = self.http.post(
            EVENT_URL,
            data="&".join(parts).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": LOGIN_PAGE,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            raise AreenError(LOGIN_FAILED)
        _absorb_script_cookies(self.http, response.text)
        return response.text


def _absorb_script_cookies(http: requests.Session, text: str) -> None:
    """uniGUI sometimes sets cookies from the response script, not Set-Cookie."""
    now = time.time()
    for raw in _SCRIPT_COOKIE.findall(text):
        pieces = [piece.strip() for piece in raw.split(";") if piece.strip()]
        if not pieces or "=" not in pieces[0]:
            continue
        name, value = pieces[0].split("=", 1)
        expires = None
        for piece in pieces[1:]:
            if piece.lower().startswith("max-age="):
                try:
                    expires = int(now + int(piece.split("=", 1)[1]))
                except ValueError:
                    expires = None
        http.cookies.set(name, value, domain="agents.areen.net", path="/")
        if expires is None:
            continue
        for cookie in http.cookies:
            if cookie.name == name:
                cookie.expires = expires


def _jar_dict(http: requests.Session) -> dict[str, str]:
    return {cookie.name: cookie.value for cookie in http.cookies}


def _apply_jar(http: requests.Session, cookies: dict[str, str]) -> None:
    for name, value in cookies.items():
        http.cookies.set(name, value, domain="agents.areen.net", path="/")


def _redis():
    from providers.redis_conn import get_redis

    return get_redis()


def save_pending(data: dict) -> None:
    _redis().set(PENDING_KEY, json.dumps(data), ex=PENDING_TTL_SECONDS)


def load_pending() -> dict | None:
    raw = _redis().get(PENDING_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def clear_pending() -> None:
    _redis().delete(PENDING_KEY)


def load_device(credentials: Credentials | None) -> dict[str, Any] | None:
    extra = credentials.extra if credentials else {}
    device = extra.get("areen_device")
    if not isinstance(device, dict):
        return None
    cookies = device.get("cookies")
    if not isinstance(cookies, dict) or set(DEVICE_COOKIE_NAMES) - set(cookies):
        return None
    # The date is when the portal last renewed the pair. The values themselves
    # stay saved, the way a browser keeps them, so a later login is not a new device.
    return device


def device_needs_renewal(device: dict[str, Any] | None) -> bool:
    """True when saved browser data should be sent again to refresh the session."""
    if not device:
        return False
    expires_at = float(device.get("expires_at") or 0)
    return expires_at <= time.time() + RENEW_BEFORE_SECONDS


def save_device(device: dict[str, Any]) -> None:
    from providers.models import ProviderConnection

    account = ProviderConnection.for_key(PROVIDER_KEY)
    extra = load_extra(account)
    extra["areen_device"] = {
        "cookies": {name: device["cookies"][name] for name in DEVICE_COOKIE_NAMES},
        "expires_at": float(device["expires_at"]),
    }
    account.extra_encrypted = encrypt(json.dumps(extra))
    account.save(update_fields=["extra_encrypted", "updated_at"])


def apply_device(http: requests.Session, device: dict[str, Any] | None) -> None:
    if device:
        _apply_jar(http, device["cookies"])


def load_portal_session(credentials: Credentials | None) -> dict[str, Any] | None:
    extra = credentials.extra if credentials else {}
    session = extra.get("areen_portal")
    if not isinstance(session, dict):
        return None
    names = session.get("names")
    if not session.get("session_id") or not session.get("window") or not isinstance(names, dict):
        return None
    return session


def save_portal_session(session: dict[str, Any]) -> None:
    from providers.models import ProviderConnection

    account = ProviderConnection.for_key(PROVIDER_KEY)
    extra = load_extra(account)
    extra["areen_portal"] = {
        "session_id": str(session["session_id"]),
        "window": str(session["window"]),
        "seq": int(session.get("seq") or 0),
        "names": dict(session.get("names") or {}),
    }
    account.extra_encrypted = encrypt(json.dumps(extra))
    account.save(update_fields=["extra_encrypted", "updated_at"])
