from __future__ import annotations

import requests

from providers.accounts import get_credentials
from providers.adapters.areen.client import (
    AreenError,
    AreenNotFound,
    AreenOtpRequired,
    AreenPortal,
    FORM_FAILED,
    NO_SESSION,
    apply_device,
    device_needs_renewal,
    load_device,
    load_portal_session,
    save_device,
    save_portal_session,
)
from providers.adapters.areen.number_info import build_number_info, component_map, main_window
from providers.contracts import (
    AuthRequired,
    BaseAdapter,
    Capability,
    NumberInfo,
    PermanentError,
    RawResponse,
    TransientError,
)


class AreenAdapter(BaseAdapter):
    key = "areen"
    capabilities = frozenset({Capability.REFRESH, Capability.NUMBER_INFO})
    # Refresh stays on the public endpoint. Number check uses the agent portal.
    needs_login = True
    login_capabilities = frozenset({Capability.NUMBER_INFO})
    otp_code = ""
    force_login = False
    prompt_device = False

    URL = "https://api.areen.net/api/common/RefreshMobileNumber"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Content-Type": "application/json",
        "HX-Current-URL": "https://update.areen.net/",
        "HX-Request": "true",
        "HX-Target": "result",
        "HX-Trigger": "mobileForm",
        "Origin": "https://update.areen.net",
        "Referer": "https://update.areen.net/",
    }

    def refresh(self, phone: str) -> RawResponse:
        # NOTE: upstream uses ``data=`` (form-encoded) even though the
        # Content-Type header advertises JSON — preserved from the original
        # Flask client because it's what the live endpoint actually accepts.
        try:
            r = requests.post(
                self.URL,
                data={"MobNumber": phone},
                headers=self.HEADERS,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return RawResponse(text="", json=None, status_code=0, error=str(exc))

        try:
            payload = r.json()
        except ValueError:
            payload = None
        return RawResponse(text=r.text, json=payload, status_code=r.status_code)

    def ping(self) -> None:
        device = load_device(self._credentials())
        # No saved browser data: stay quiet. Only the login test may register
        # the device, and that is the only path that can send an SMS.
        if self.force_login or device_needs_renewal(device):
            raise AuthRequired("login")

    def login(self) -> None:
        credentials = self._credentials()
        saved = load_device(credentials)
        portal = AreenPortal()
        apply_device(portal.http, saved)
        # A code is asked only the first time, when nothing is saved yet.
        prompt_device = self.prompt_device and saved is None
        try:
            if self.otp_code:
                portal.submit_code(self.otp_code)
            else:
                portal.sign_in(
                    portal.open_login(),
                    credentials.username,
                    credentials.password,
                    prompt_device=prompt_device,
                )
        except AreenOtpRequired:
            raise
        device = portal.device_cookies()
        if device is None:
            raise AreenError(NO_SESSION)
        save_device(device)
        self.force_login = False

    def number_info(self, phone: str) -> NumberInfo:
        phone = phone.strip()
        credentials = self._credentials()
        portal = AreenPortal()
        apply_device(portal.http, load_device(credentials))
        session = load_portal_session(credentials)
        if session is not None:
            try:
                return self._checked(portal, session, phone)
            except AreenNotFound:
                raise PermanentError("not_found")
            except AreenError:
                portal = AreenPortal()
                apply_device(portal.http, load_device(credentials))
                session = None
        if session is None:
            try:
                page = portal.open_login()
                script = portal.sign_in(
                    page,
                    credentials.username,
                    credentials.password,
                    prompt_device=False,
                )
            except AreenOtpRequired:
                raise
            except AreenError as exc:
                raise PermanentError(str(exc)) from exc
            except requests.RequestException as exc:
                raise TransientError("connection_error") from exc
            script = portal.open_subscriber_search(page.session_id, script)
            names = component_map(script)
            window = main_window(script, "edtCelNum")
            session = {
                "session_id": page.session_id,
                "window": window,
                "seq": portal._seq,
                "names": names,
            }
            if not window or "edtCelNum" not in names:
                raise PermanentError(FORM_FAILED)
        try:
            return self._checked(portal, session, phone)
        except AreenNotFound:
            raise PermanentError("not_found")
        except AreenError as exc:
            raise PermanentError(str(exc)) from exc
        except requests.RequestException as exc:
            raise TransientError("connection_error") from exc

    def _checked(self, portal: AreenPortal, session: dict, phone: str) -> NumberInfo:
        from django.utils import timezone

        try:
            fields = portal.read_subscription(session, phone)
        finally:
            save_portal_session(session)
        return build_number_info(fields, timezone.localdate())

    def _credentials(self):
        return get_credentials(self.key)
