"""Sky Sales Portal (EoT4R) — HTTP client without browser."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import requests

from providers.accounts import Credentials, get_credentials
from providers.contracts import PermanentError
from providers.sessions import SessionStore

try:
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None  # type: ignore

BASE = "https://sales-ps.sky5g.ps:8888"
HOST = "sales-ps.sky5g.ps"
PROVIDER_KEY = "sky"
REQUEST_TIMEOUT_SECONDS = 90
# Login is bounded so it always finishes inside the Redis login lock
# (providers.sessions.LOGIN_LOCK_TIMEOUT_SECONDS = 180): no request starts after
# the deadline and each takes at most connect + read = 20 s → worst case ~120 s.
LOGIN_CONNECT_TIMEOUT_SECONDS = 5
LOGIN_READ_TIMEOUT_SECONDS = 15
LOGIN_DEADLINE_SECONDS = 100
# The OTP retry (login, init2fa, confirm2fa, getLoginInfo, genJwtToken) only
# starts if its five requests fit before the deadline at the read timeout each;
# the deadline guard still caps the worst case.
OTP_RETRY_REQUESTS = 5
# The balance report runs inside a web request (gunicorn timeout 60 s): bound the
# read so a slow Sky ends in a readable error instead of a gateway timeout.
REPORT_READ_TIMEOUT_SECONDS = 40


@dataclass
class SkySalesSession:
    jwt_token: str
    session_id: str = ""
    eot_user_id: int | None = None
    user_name: str = ""
    pending_otp: bool = False
    cookies: dict[str, str] = field(default_factory=dict)
    bound_ip: str = ""
    proxy_fingerprint: str = ""
    logged_in_at: float = 0.0


class SkySalesError(Exception):
    pass


class SkySalesSessionError(SkySalesError):
    """Auth/session rejected — safe to re-login and retry once."""


def proxy_fingerprint(proxy_url: str) -> str:
    if not proxy_url:
        return "direct"
    return hashlib.sha256(proxy_url.encode()).hexdigest()[:16]


def _debug(msg: str) -> None:
    if os.environ.get("SKY_SALES_DEBUG", "").strip() in ("1", "true", "yes"):
        print(f"[sky_sales] {msg}", flush=True)


def refresh_response(
    *,
    success: bool,
    message: str,
    phone: str,
    reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": success,
        "message": message,
        "phone": phone,
    }
    if reason:
        payload["reason"] = reason
    payload.update(extra)
    return payload


def refresh_not_updated(phone: str, reason: str, **extra: Any) -> dict[str, Any]:
    return refresh_response(
        success=False,
        message="لم يتم التحديث",
        phone=phone,
        reason=reason,
        **extra,
    )


def refresh_system_error(phone: str, reason: str, message: str, error: str = "") -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if error and error != message:
        extra["error"] = error
    return refresh_response(
        success=False,
        message=message,
        phone=phone,
        reason=reason,
        **extra,
    )


def error_response(phone: str, exc: Exception) -> dict[str, Any]:
    msg = str(exc)
    if msg == "OTP_REQUIRED":
        return refresh_system_error(
            phone,
            "otp_required",
            "مطلوب كود OTP",
            error=msg,
        ) | {"otp_required": True}

    if "Failed to detect egress IP" in msg:
        return refresh_system_error(
            phone,
            "proxy_error",
            "فشل الاتصال — تحقق من البروكسي",
            error=msg,
        )

    if "Subscriber not found" in msg:
        return refresh_not_updated(phone, "not_found")

    if "تعذّر إصدار مهمة التحديث" in msg or (
        "Task not created" in msg and "ERROR" in msg.upper()
    ):
        return refresh_not_updated(phone, "not_eligible")

    if any(
        token in msg
        for token in (
            "Wrong username or password",
            "Login failed:",
            "OTP rejected:",
            "OTP init failed:",
            "getLoginInfo failed:",
        )
    ):
        return refresh_system_error(
            phone,
            "login_error",
            "فشل تسجيل الدخول",
            error=msg,
        )

    if "SKY_SALES_USER" in msg or "SKY_SALES_PASSWORD" in msg:
        return refresh_system_error(phone, "config_error", msg, error=msg)

    if "ثبّت pyotp" in msg:
        return refresh_system_error(phone, "config_error", msg, error=msg)

    if any(token in msg for token in ("REJECTED on", "session probe failed", "FAILED on")):
        return refresh_system_error(
            phone,
            "session_error",
            "انتهت الجلسة",
            error=msg,
        )

    if "HTTP error on" in msg:
        return refresh_system_error(
            phone,
            "connection_error",
            "فشل الاتصال بـ Sky",
            error=msg,
        )

    return refresh_system_error(
        phone,
        "api_error",
        "خطأ أثناء التحديث",
        error=msg,
    )


class SkySalesClient:
    @staticmethod
    def _sim_field(sim: dict[str, Any], *keys: str, default: str = "") -> str:
        for key in keys:
            val = sim.get(key) or sim.get(key.upper()) or sim.get(key.lower())
            if val is not None and str(val) != "null":
                return str(val)
        return default

    def __init__(
        self,
        session: SkySalesSession | None = None,
        credentials: Credentials | None = None,
    ):
        self._credentials = credentials
        self._credentials_loaded = credentials is not None
        self._logging_in = False
        self._login_deadline: float | None = None
        self.http = requests.Session()
        self._configure_http()
        self.session = session or SkySalesSession(jwt_token="")
        for name, value in self.session.cookies.items():
            self.http.cookies.set(name, value)

    @property
    def credentials(self) -> Credentials | None:
        """Sky account credentials, looked up at most once per client."""
        if not self._credentials_loaded:
            try:
                self._credentials = get_credentials(PROVIDER_KEY)
            except PermanentError:
                self._credentials = None
            self._credentials_loaded = True
        return self._credentials

    @property
    def proxy_url(self) -> str:
        """Proxy from the Sky account (``Credentials.extra['proxy_url']``), or ``""``."""
        creds = self.credentials
        if creds is None:
            return ""
        return str(creds.extra.get("proxy_url", "") or "").strip()

    def _configure_http(self) -> None:
        """Build headers/proxy for Sky calls.

        ``trust_env=False`` so HTTP(S)_PROXY from the shell/IDE does not
        hijack ipify or the Sky portal — only the Sky account's proxy.
        """
        self.http.trust_env = False
        self.http.headers.update(
            {
                "Content-Type": "application/json; charset=utf-8",
                "Origin": f"https://{HOST}",
                "Referer": f"https://{HOST}/",
            }
        )
        self._apply_proxy()

    def _apply_proxy(self) -> None:
        proxy = self.proxy_url
        if proxy:
            self.http.proxies.update({"http": proxy, "https": proxy})
        else:
            # Drop any leftover proxies (e.g. after env change / session reuse).
            self.http.proxies.clear()

    def detect_egress_ip(self) -> str:
        try:
            timeout = self._request_timeout() if self._login_deadline else 20
            resp = self.http.get("https://api.ipify.org", timeout=timeout)
            resp.raise_for_status()
            return resp.text.strip()
        except requests.RequestException as exc:
            raise SkySalesError(f"Failed to detect egress IP: {exc}") from exc

    def current_egress_ip(self) -> str:
        """Egress IP used to invalidate sessions when the proxy path changes.

        Direct (no Sky account proxy): skip ipify entirely — local/Django
        processes often cannot resolve it, and Sky login does not need it.
        """
        if not self.proxy_url:
            return ""
        return self.detect_egress_ip()

    def _is_session_ready(self) -> bool:
        return bool(
            self.session.jwt_token
            and self.session.eot_user_id
            and not self.session.pending_otp
        )

    def _ip_or_proxy_changed(self, current_ip: str) -> bool:
        # Direct mode: never treat missing/blank IP as a change.
        if current_ip and self.session.bound_ip and self.session.bound_ip != current_ip:
            _debug(f"IP changed {self.session.bound_ip} -> {current_ip}")
            return True
        current_fp = proxy_fingerprint(self.proxy_url)
        if (
            self.session.proxy_fingerprint
            and self.session.proxy_fingerprint != current_fp
        ):
            _debug(f"proxy changed {self.session.proxy_fingerprint} -> {current_fp}")
            return True
        return False

    def _probe_session(self) -> None:
        self._refresh_jwt()
        if not self.session.user_name:
            raise SkySalesSessionError("missing user_name")
        data = self.generic_api(
            "GetAllUserCustomersAndSubcustomers",
            [self.session.user_name],
        )
        if data.get("result") != "SUCCESS":
            raise SkySalesSessionError(f"session probe failed: {data}")

    def _reset_http_session(self) -> None:
        self.http.cookies.clear()
        self.http = requests.Session()
        self._configure_http()

    def _request_timeout(self) -> float | tuple[float, float]:
        if self._login_deadline is None:
            return REQUEST_TIMEOUT_SECONDS
        remaining = self._login_deadline - time.monotonic()
        if remaining <= 0:
            raise SkySalesError("Login failed: deadline exceeded")
        return (LOGIN_CONNECT_TIMEOUT_SECONDS, min(LOGIN_READ_TIMEOUT_SECONDS, remaining))

    def force_new_session(self, username: str, password: str, otp_code: str = "") -> str:
        """Login + TOTP (bounded); optionally bind session to egress IP when using a proxy."""
        self._login_deadline = time.monotonic() + LOGIN_DEADLINE_SECONDS
        self._logging_in = True
        try:
            current_ip = self.current_egress_ip()
            _debug(f"new session login via IP {current_ip or 'direct'}")
            self._reset_http_session()
            self.session = SkySalesSession(jwt_token="", user_name=username)
            self.auto_login(username, password, otp_code)
        finally:
            self._logging_in = False
            self._login_deadline = None
        self.session.bound_ip = current_ip
        self.session.proxy_fingerprint = proxy_fingerprint(self.proxy_url)
        self.session.logged_in_at = time.time()
        self.save_session()
        return current_ip

    def is_session_usable(self) -> bool:
        """Ready session still bound to the current egress IP / proxy."""
        return self._is_session_ready() and not self._ip_or_proxy_changed(
            self.current_egress_ip()
        )

    def save_session(self) -> None:
        """Persist the session to ``SessionStore("sky")`` (never mid-login)."""
        if self._logging_in:
            return
        store = SessionStore(PROVIDER_KEY)
        stored = store.load() or {}
        if float(stored.get("logged_in_at") or 0) > self.session.logged_in_at:
            _debug("session superseded by a newer login — not saving")
            return
        payload = asdict(self.session)
        payload["cookies"] = self.http.cookies.get_dict()
        store.save(payload)

    @classmethod
    def from_store(cls, credentials: Credentials | None = None) -> SkySalesClient | None:
        store = SessionStore(PROVIDER_KEY)
        data = store.load()
        if not data:
            return None
        sky = SkySalesSession(
            jwt_token=data.get("jwt_token", ""),
            session_id=data.get("session_id", ""),
            eot_user_id=data.get("eot_user_id"),
            user_name=data.get("user_name", ""),
            pending_otp=bool(data.get("pending_otp")),
            cookies=data.get("cookies") or {},
            bound_ip=data.get("bound_ip", ""),
            proxy_fingerprint=data.get("proxy_fingerprint", ""),
            logged_in_at=float(data.get("logged_in_at") or 0),
        )
        if sky.pending_otp:
            # Half-login sessions can never be resumed (Sky needs a fresh init2fa).
            store.clear()
            return None
        return cls(sky, credentials=credentials)

    def begin_login(self, username: str, password: str) -> None:
        self.session.user_name = username
        self.login(username, password)
        self.session.pending_otp = True
        # Do not persist half-login: a saved pending_otp file makes the next
        # request call confirm2fa without a fresh init2fa and Sky rejects it.

    def _totp_secret(self) -> str:
        creds = self.credentials
        return creds.totp_secret if creds else ""

    def _otp_retry_fits_deadline(self) -> bool:
        if self._login_deadline is None:
            return True
        needed = OTP_RETRY_REQUESTS * LOGIN_READ_TIMEOUT_SECONDS
        return self._login_deadline - time.monotonic() >= needed

    def auto_login(self, username: str, password: str, otp_code: str = "") -> None:
        code = resolve_otp_code(otp_code, self._totp_secret())
        try:
            self.begin_login(username, password)
            self.confirm_otp(code)
        except SkySalesError as exc:
            if "OTP rejected:" not in str(exc) or not self._otp_retry_fits_deadline():
                raise
            _debug("OTP rejected — fresh login + retry once")
            self._reset_http_session()
            self.session = SkySalesSession(jwt_token="", user_name=username)
            self.begin_login(username, password)
            self.confirm_otp(resolve_otp_code(otp_code, self._totp_secret()))

    def _refresh_jwt(self) -> None:
        token = self._post("/ipa/apis/json/general/genJwtToken", {})
        if token.get("result") == "SUCCESS" and token.get("jwtToken"):
            self.session.jwt_token = token["jwtToken"]
            self.save_session()

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        body = dict(payload)
        if self.session.jwt_token:
            body["jwtToken"] = self.session.jwt_token
        if self.session.session_id:
            body["sessionId"] = self.session.session_id

        url = f"{BASE}{path}"
        resp = self.http.post(
            url,
            json=body,
            timeout=self._request_timeout() if timeout is None else timeout,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise SkySalesSessionError(f"HTTP error on {path}: {exc}") from exc

        data = resp.json()
        if not isinstance(data, dict):
            raise SkySalesError(f"Unexpected response from {path}")

        result = (data.get("result") or "").upper()
        if result == "REJECTED":
            raise SkySalesSessionError(f"REJECTED on {path}: {data}")

        if path.endswith("/generic/v2") and result == "FAILED" and not data.get("data"):
            raise SkySalesSessionError(f"FAILED on {path}: {data}")

        return data

    def login(self, username: str, password: str) -> dict[str, Any]:
        data = self._post(
            "/ipa/apis/json/general/login",
            {"username": username, "password": password},
        )
        if data.get("result") not in (None, "SUCCESS") and not data.get("jwtToken") and not data.get("userId"):
            raise SkySalesError(f"Login failed: {data}")

        if data.get("clientAddress"):
            _debug(f"login clientAddress={data['clientAddress']}")

        if data.get("jwtToken"):
            self.session.jwt_token = data["jwtToken"]
            self.session.session_id = data.get("sessionId", "")
            self.session.user_name = username
            init = self._post(
                "/ipa/apis/json/factor/init2fa",
                {"jwtToken": self.session.jwt_token, "sessionId": self.session.session_id},
            )
            if init.get("result") != "SUCCESS":
                raise SkySalesError(f"OTP init failed: {init}")
            return init

        if data.get("userId"):
            self.session.eot_user_id = int(data["userId"])
            self.session.session_id = data.get("sessionId", "")
            self.session.user_name = username
            self._refresh_jwt()
            return data

        raise SkySalesError("Wrong username or password")

    def confirm_otp(self, otp_code: str) -> None:
        data = self._post("/ipa/apis/json/factor/confirm2fa", {"otpCode": otp_code})
        if data.get("result") != "SUCCESS" or not data.get("confirmed"):
            raise SkySalesError(f"OTP rejected: {data}")

        info = self._post("/ipa/apis/json/general/getLoginInfo", {})
        if info.get("result") != "SUCCESS":
            raise SkySalesError(f"getLoginInfo failed: {info}")
        self.session.eot_user_id = int(info["userId"])
        self.session.user_name = info.get("userName", self.session.user_name)
        self.session.pending_otp = False
        self._refresh_jwt()

    def load_global_sub_customer_id(self) -> str:
        if not self.session.user_name:
            return ""
        data = self.generic_api(
            "GetAllUserCustomersAndSubcustomers",
            [self.session.user_name],
        )
        rows = self._rows(data)
        if not rows:
            return ""
        return str(self._first(rows[0], "SUB_CUSTOMER_ID", "sub_customer_id") or "")

    def generic_api(
        self,
        api_name: str,
        wildcards: list[Any],
        *,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/ipa/apis/json/internal/generic/v2",
            {"apiName": api_name, "wildcards": wildcards},
            timeout=timeout,
        )

    def fetch_balance_report(self, date_from: date, date_to: date) -> list[dict[str, Any]]:
        """Pull Sky template 22 (بيانات رصيد) for the logged-in eot user."""
        if not self.session.eot_user_id:
            raise SkySalesError("eot_user_id missing — login first")
        from_s = date_from.strftime("%d/%m/%Y")
        to_s = date_to.strftime("%d/%m/%Y")
        data = self.generic_api(
            "GetEot4RReportData",
            [22, self.session.eot_user_id, from_s, to_s],
            timeout=(LOGIN_CONNECT_TIMEOUT_SECONDS, REPORT_READ_TIMEOUT_SECONDS),
        )
        if data.get("result") and str(data.get("result")).upper() not in (
            "SUCCESS",
            "OK",
        ):
            raise SkySalesError(f"GetEot4RReportData failed: {data.get('result')}")
        return self._rows(data)

    @staticmethod
    def _rows(data: dict[str, Any]) -> list[dict[str, Any]]:
        rows = data.get("data") or []
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _first(row: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in row:
                return row[key]
            upper = key.upper()
            if upper in row:
                return row[upper]
            lower = key.lower()
            if lower in row:
                return row[lower]
        return None

    def search_subscriber(self, phone: str, lang: str = "AR") -> dict[str, Any] | None:
        if not self.session.eot_user_id:
            raise SkySalesError("eot_user_id missing — login first")
        pattern = f"%{phone.strip()}%"
        data = self.generic_api(
            "GetSubscribers4TasksByPatternV3",
            [self.session.eot_user_id, lang, pattern],
        )
        if data.get("result") != "SUCCESS":
            raise SkySalesError(f"Subscriber search failed: {data}")
        rows = self._rows(data)
        if not rows:
            return None
        for row in rows:
            msisdn = str(self._first(row, "NE_ID", "msisdn") or "")
            if msisdn == phone or msisdn.endswith(phone.lstrip("0")):
                return row
        return rows[0]

    @staticmethod
    def _build_task_description(sim: dict[str, Any], costs: dict[str, str]) -> str:
        def g(*keys: str, default: str = "") -> str:
            for key in keys:
                val = sim.get(key) or sim.get(key.upper()) or sim.get(key.lower())
                if val is not None and str(val) != "null":
                    return str(val)
            return default

        return (
            '<b id="task-title">تعديل زبون</b><br/>'
            f'شركة الاتصالات : {g("MNO_NAME", "mnoName")}<br/>'
            f'رقم الهاتف : {g("NE_ID", "msisdn")}<br/>'
            f'رقم الشريحة : {g("ICCID", "iccid")}<br/>'
            f'الرزمة : {g("RATE_PLAN_NAME", "planName")}<br/>'
            f'مدة الخدمة : <br/>'
            f'سعر الطلبية : {costs["order_cost"]}<br/>'
            f'اعادة المال : {costs["order_refund"]}<br/>'
            f'سعر نهائي : {costs["order_total_cost"]}<br/>'
            f'اسم الزبون : {g("SUBSCRIBER_NAME", "subscriberName")}<br/>'
            f'السعر للزبون : {g("SUBSCRIBER_PRICE", "subscriberPrice")}<br/>'
        )

    @staticmethod
    def _build_task_details(sim: dict[str, Any], costs: dict[str, str], global_sub_customer_id: str) -> dict[str, Any]:
        def g(*keys: str, default: Any = None) -> Any:
            for key in keys:
                val = sim.get(key) or sim.get(key.upper()) or sim.get(key.lower())
                if val is not None and str(val) != "null":
                    return val
            return default

        sim_id = str(g("ID", "simId"))
        msisdn = str(g("NE_ID", "msisdn"))
        iccid = str(g("ICCID", "iccid"))
        plan_id = str(g("RATE_PLAN_ID", "planId"))
        mno_id = str(g("MNO_ID", "mnoId"))
        status_id = str(g("STATUS_ID", "statusId"))
        period_id = str(g("PERIOD_ID", "periodId") or "0")
        sub_customer_id = str(g("SUB_CUSTOMER_ID", "subCustomerId"))
        customer_id = str(g("CUSTOMER_ID", "customerId"))
        subscriber_name = str(g("SUBSCRIBER_NAME", "subscriberName") or "")
        subscriber_price = str(g("SUBSCRIBER_PRICE", "subscriberPrice") or "0")
        auto_renew = str(g("AUTO_RENEW", "autoRenew") or "0")
        secondary_iccid = str(g("SECONDARY_ICCID", "secondaryIccid") or "0")
        secondary_msisdn = str(g("SECONDARY_MSISDN", "secondaryMsisdn") or "")

        return {
            "actionName": "change-status",
            "actionType": "تعديل زبون",
            "taskDescription": "",
            "confirmMessage": "",
            "existingData": {
                "subscriber_id": sim_id,
                "msisdn": msisdn,
                "iccid": iccid,
                "rate_plan_id": plan_id,
                "mno_id": mno_id,
                "status_id": status_id,
                "period_id": period_id if period_id != "0" else "",
                "subscriberName": subscriber_name,
                "customer_id": customer_id,
                "subCustomer_id": sub_customer_id,
                "price": None,
                "vas_id": None,
                "vas_status": None,
                "vas_details": None,
                "auto_renew": auto_renew,
                "secondary_iccid": secondary_iccid,
                "secondary_msisdn": secondary_msisdn,
            },
            "newData": {
                "subscriber_id": sim_id,
                "msisdn": msisdn,
                "iccid": iccid,
                "rate_plan_id": plan_id,
                "mno_id": mno_id,
                "status_id": "",
                "period_id": period_id if period_id else "0",
                "subscriber_name": subscriber_name,
                "customer_id": global_sub_customer_id or sub_customer_id,
                "sub_customer_id": sub_customer_id,
                "price": subscriber_price,
                "vas_id": None,
                "vas_status": None,
                "vas_details": None,
                "sub_task_type_id": None,
                "sub_customer_obligo_update_value": costs["order_total_cost"],
                "temporary_msisdn": None,
                "orderRefund": costs["order_refund"],
                "orderCost": costs["order_cost"],
                "orderTotalCost": costs["order_total_cost"],
                "auto_renew": auto_renew,
                "secondary_iccid": secondary_iccid,
                "secondary_msisdn": secondary_msisdn,
            },
        }

    def _refresh_sim_once(self, phone: str) -> dict[str, Any]:
        sim = self.search_subscriber(phone)
        if sim is None:
            return refresh_not_updated(phone, "not_found")

        status_id = self._sim_field(sim, "STATUS_ID", "statusId")
        status_name = self._sim_field(sim, "STATUS_NAME", "statusName")
        if status_name != "مفعل":
            return refresh_not_updated(
                phone,
                "not_active",
                status_name=status_name,
                status_id=status_id,
            )

        sim_id = str(self._first(sim, "ID", "simId"))
        msisdn = str(self._first(sim, "NE_ID", "msisdn"))
        sub_customer_id = str(self._first(sim, "SUB_CUSTOMER_ID", "subCustomerId"))
        plan_id = str(self._first(sim, "RATE_PLAN_ID", "planId") or "0")
        period_id = str(self._first(sim, "PERIOD_ID", "periodId") or "0")
        status_id = str(self._first(sim, "STATUS_ID", "statusId"))

        costs_resp = self.generic_api(
            "GetSubscriberCosts",
            [msisdn, sub_customer_id, plan_id, period_id or "0", "change-status", "0"],
        )
        if costs_resp.get("result") != "SUCCESS" or not self._rows(costs_resp):
            raise SkySalesError(f"GetSubscriberCosts failed: {costs_resp}")

        cost_row = self._rows(costs_resp)[0]
        costs = {
            "order_cost": f'{float(self._first(cost_row, "ORDER_COST", "order_cost") or 0):.2f}',
            "order_refund": f'{float(self._first(cost_row, "ORDER_REFUND", "order_refund") or 0):.2f}',
            "order_total_cost": f'{float(self._first(cost_row, "ORDER_TOTAL_COST", "order_total_cost") or 0):.2f}',
        }

        validate_resp = self.generic_api(
            "ValidateTaskDataBeforeCreate",
            [msisdn, ""],
        )
        if validate_resp.get("result") != "SUCCESS":
            raise SkySalesError(f"ValidateTaskDataBeforeCreate failed: {validate_resp}")

        status_resp = self.generic_api("GetSubscriberStatus", [sim_id])
        if status_resp.get("result") != "SUCCESS" or not self._rows(status_resp):
            raise SkySalesError(f"GetSubscriberStatus failed: {status_resp}")
        current_status = str(self._first(self._rows(status_resp)[0], "STATUS_ID", "status_id"))
        if current_status != status_id:
            raise SkySalesError(
                f"SIM status changed (was {status_id}, now {current_status})"
            )

        global_sub_customer_id = self.load_global_sub_customer_id() or sub_customer_id
        task_details = self._build_task_details(sim, costs, global_sub_customer_id)
        task_details["taskDescription"] = self._build_task_description(sim, costs)

        if not self.session.eot_user_id:
            raise SkySalesError("eot_user_id missing — login first")

        create_resp = self.generic_api(
            "CreateNewTaskWithBalanceCheck",
            [
                "change-status",
                self.session.eot_user_id,
                sim_id,
                task_details["taskDescription"],
                json.dumps(task_details, ensure_ascii=False),
            ],
        )

        if create_resp.get("result") != "SUCCESS" or not self._rows(create_resp):
            raise SkySalesError(f"CreateNewTaskWithBalanceCheck failed: {create_resp}")

        task_id = str(self._first(self._rows(create_resp)[0], "ID", "id") or "").strip()
        if not task_id or task_id == "-1":
            return refresh_not_updated(phone, "not_eligible")
        if task_id.upper() == "ERROR" or not task_id.isdigit():
            return refresh_not_updated(phone, "not_eligible")

        return refresh_response(
            success=True,
            message=f"تم التحديث بنجاح - رقم العملية: {task_id}",
            phone=phone,
            reason="success",
            task_id=task_id,
            status_name=status_name,
            status_id=status_id,
        )

    def refresh_sim(self, phone: str) -> dict[str, Any]:
        """Business outcome dict; session errors (and transport errors) propagate."""
        try:
            return self._refresh_sim_once(phone)
        except SkySalesSessionError:
            raise
        except SkySalesError as exc:
            return error_response(phone, exc)


def generate_totp_code(secret: str) -> str:
    if not secret:
        raise SkySalesError("OTP_REQUIRED")
    if pyotp is None:
        raise SkySalesError("ثبّت pyotp: pip install pyotp")
    return pyotp.TOTP(secret).now()


def resolve_otp_code(manual_otp: str = "", secret: str = "") -> str:
    if manual_otp.strip():
        return manual_otp.strip()
    return generate_totp_code(secret)
