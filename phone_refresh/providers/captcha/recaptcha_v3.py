"""Headless reCAPTCHA v3 token extractor.

Ported from the standalone Flask project (``refresh_numbers/sky_app.py``)
with the class renamed and trivial cleanup. The giant ``BG_PAYLOAD``
constant below is intentionally preserved verbatim — it's the captured
``bg=`` blob the Sky site expects when requesting a v3 token, and any
edit to it will break token acquisition.
"""
from __future__ import annotations

import re

import requests

_DEFAULT_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
        "Gecko/20100101 Firefox/118.0"
    ),
}

# Captured ``bg=`` payload — must be sent verbatim. Editing breaks the bypass.
BG_PAYLOAD = (
    "!q62grYxHRvVxjUIjSFNd0mlvrZ-iCgIHAAAB6FcAAAANnAkBySdqTJGFRK7SirleWAwPVhv9"
    "-XwP8ugGSTJJgQ46-0IMBKN8HUnfPqm4sCefwxOOEURND35prc9DJYG0pbmg_jD18qC0c-lQ"
    "zuPsOtUhHTtfv3--SVCcRvJWZ0V3cia65HGfUys0e1K-IZoArlxM9qZfUMXJKAFuWqZiBn-Q"
    "i8VnDqI2rRnAQcIB8Wra6xWzmFbRR2NZqF7lDPKZ0_SZBEc99_49j07ISW4X65sMHL139EAR"
    "IOipdsj5js5JyM19a2TCZJtAu4XL1h0ZLfomM8KDHkcl_b0L-jW9cvAe2K2uQXKRPzruAvtj"
    "dhMdODzVWU5VawKhpmi2NCKAiCRUlJW5lToYkR_X-07AqFLY6qi4ZbJ_sSrD7fCNNYFKmLfA"
    "axPwPmp5Dgei7KKvEQmeUEZwTQAS1p2gaBmt6SCOgId3QBfF_robIkJMcXFzj7R0G-s8rwGU"
    "Sc8EQzT_DCe9SZsJyobu3Ps0-YK-W3MPWk6a69o618zPSIIQtSCor9w_oUYTLiptaBAEY03N"
    "WINhc1mmiYu2Yz5apkW_KbAp3HD3G0bhzcCIYZOGZxyJ44HdGsCJ-7ZFTcEAUST-aLbS-YN1"
    "AyuC7ClFO86CMICVDg6aIDyCJyIcaJXiN-bN5xQD_NixaXatJy9Mx1XEnU4Q7E_KISDJfKUh"
    "DktK5LMqBJa-x1EIOcY99E-eyry7crf3-Hax3Uj-e-euzRwLxn2VB1Uki8nqJQVYUgcjlVXQ"
    "hj1X7tx4jzUb0yB1TPU9uMBtZLRvMCRKvFdnn77HgYs5bwOo2mRECiFButgigKXaaJup6NM4"
    "KRUevhaDtnD6aJ8ZWQZTXz_OJ74a_OvPK9eD1_5pTG2tUyYNSyz-alhvHdMt5_MAdI3op4Z"
    "mcvBQBV9VC2JLjphDuTW8eW_nuK9hN17zin6vjEL8YIm_MekB_dIUK3T1Nbyqmyzigy-Lg8t"
    "RL6jSinzdwOTc9hS5SCsPjMeiblc65aJC8AKmA5i80f-6Eg4BT305UeXKI3QwhI3ZJyyQAJT"
    "ata41FoOXl3EF9Pyy8diYFK2G-CS8lxEpV7jcRYduz4tEPeCpBxU4O_KtM2iv4STkwO4Z_-c"
    "-fMLlYu9H7jiFnk6Yh8XlPE__3q0FHIBFf15zVSZ3qroshYiHBMxM5BVQBOExbjoEdYKx4-m"
    "9c23K3suA2sCkxHytptG-6yhHJR3EyWwSRTY7OpX_yvhbFri0vgchw7U6ujyoXeCXS9N4oOo"
    "GYpS5OyFyRPLxJH7yjXOG2Play5HJ91LL6J6qg1iY8MIq9XQtiVZHadVpZVlz3iKcX4vXcQ3"
    "rv_qQwhntObGXPAGJWEel5OiJ1App7mWy961q3mPg9aDEp9VLKU5yDDw1xf6tOFMwg2Q-PND"
    "aKXAyP_FOkxOjnu8dPhuKGut6cJr449BKDwbnA9BOomcVSztEzHGU6HPXXyNdZbfA6D12f5l"
    "WxX2B_pobw3a1gFLnO6mWaNRuK1zfzZcfGTYMATf6d7sj9RcKNS230XPHWGaMlLmNxsgXkEN"
    "7a9PwsSVwcKdHg_HU4vYdRX6vkEauOIwVPs4dS7yZXmtvbDaX1zOU4ZYWg0T42sT3nIIl9M2"
    "EeFS5Rqms_YzNp8J-YtRz1h5RhtTTNcA5jX4N-xDEVx-vD36bZVzfoMSL2k85PKv7pQGLH-0"
    "a3DsR0pePCTBWNORK0g_RZCU_H898-nT1syGzNKWGoPCstWPRvpL9cnHRPM1ZKemRn0nPVm9"
    "Bgo0ksuUijgXc5yyrf5K49UU2J5JgFYpSp7aMGOUb1ibrj2sr-D63d61DtzFJ2mwrLm_KHBi"
    "N_ECpVhDsRvHe5iOx_APHtImevOUxghtkj-8RJruPgkTVaML2MEDOdL_UYaldeo-5ckZo3VH"
    "ss7IpLArGOMTEd0bSH8tA8CL8RLQQeSokOMZ79Haxj8yE0EAVZ-k9-O72mmu5I0wH5IPgapN"
    "vExeX6O1l3mC4MqLhKPdOZOnTiEBlSrV4ZDH_9fhLUahe5ocZXvXqrud9QGNeTpZsSPeIYub"
    "eOC0sOsuqk10sWB7NP-lhifWeDob-IK1JWcgFTytVc99RkZTjUcdG9t8prPlKAagZIsDr1Ti"
    "X3dy8sXKZ7d9EXQF5P_rHJ8xvmUtCWqbc3V5jL-qe8ANypwHsuva75Q6dtqoBR8vCE5xWgfw"
    "B0GzR3Xi_l7KDTsYAQIrDZVyY1UxdzWBwJCrvDrtrNsnt0S7BhBJ4ATCrW5VFPqXyXRiLxHC"
    "Iv9zgo-NdBZQ4hEXXxMtbem3KgYUB1Rals1bbi8X8MsmselnHfY5LdOseyXWIR2QcrANSAyp"
    "QUAhwVpsModw7HMdXgV9Uc-HwCMWafOChhBr88tOowqVHttPtwYorYrzriXNRt9LkigESMy1"
    "bEDx79CJguitwjQ9IyIEu8quEQb_-7AEXrfDzl_FKgASnnZLrAfZMtgyyddIhBpgAvgR_c8a"
    "8Nuro-RGV0aNuunVg8NjL8binz9kgmZvOS38QaP5anf2vgzJ9wC0ZKDg2Ad77dPjBCiCRtVe"
    "_dqm7FDA_cS97DkAwVfFawgce1wfWqsrjZvu4k6x3PAUH1UNzQUxVgOGUbqJsaFs3GZIMiI8"
    "O6-tZktz8i8oqpr0RjkfUhw_I2szHF3LM20_bFwhtINwg0rZxRTrg4il-_q7jDnVOTqQ7fdg"
    "HgiJHZw_OOB7JWoRW6ZlJmx3La8oV93fl1wMGNrpojSR0b6pc8SThsKCUgoY6zajWWa3CesX"
    "1ZLUtE7Pfk9eDey3stIWf2acKolZ9fU-gspeACUCN20EhGT-HvBtNBGr_xWk1zVJBgNG29ol"
    "XCpF26eXNKNCCovsILNDgH06vulDUG_vR5RrGe5LsXksIoTMYsCUitLz4HEehUOd9mWCmLCl"
    "00eGRCkwr9EB557lyr7mBK2KPgJkXhNmmPSbDy6hPaQ057zfAd5s_43UBCMtI-aAs5NN4TXH"
    "d6IlLwynwc1zsYOQ6z_HARlcMpCV9ac-8eOKsaepgjOAX4YHfg3NekrxA2ynrvwk9U-gCtpx"
    "MJ4f1cVx3jExNlIX5LxE46FYIhQ"
)


def _parse_between(text: str, before: str, after: str) -> str:
    """Return the substring between ``before`` and ``after`` (non-greedy).

    Returns an empty string instead of raising when nothing matches —
    callers fall back to extracting from the reload URL.
    """
    try:
        match = re.search(rf"{before}(.*?){after}", text)
        if match is None:
            return ""
        return match.group(1).strip().rstrip()
    except re.error:
        return ""


class RecaptchaV3Bypass:
    """Acquire a reCAPTCHA v3 ``rresp`` token without running a browser.

    Two upstream calls: GET the anchor URL to scrape the internal token,
    then POST it back to the reload URL together with the captured
    ``bg=`` payload to receive the final ``rresp`` token.
    """

    def __init__(self, anchor_url: str, reload_url: str, timeout: int = 20):
        self.anchor_url = anchor_url
        self.reload_url = reload_url
        self.timeout = timeout
        self.session = requests.Session()

        self.v = _parse_between(anchor_url, "v=", "&")
        self.k = _parse_between(anchor_url, "k=", "&")
        self.co = _parse_between(anchor_url, "co=", "&")
        self.headers = dict(_DEFAULT_HEADERS)

    def _get_internal_token(self) -> str:
        try:
            req = self.session.get(self.anchor_url, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot reach reCAPTCHA anchor: {exc}") from exc

        results = re.findall(r'id="recaptcha-token" value="(.*?)"', req.text)
        if not results:
            results = re.findall(r'value="(.*?)" id="recaptcha-token"', req.text)
        if not results:
            raise RuntimeError("reCAPTCHA anchor did not contain an internal token")
        return results[0]

    def _resolved_key(self) -> str:
        if self.k:
            return self.k
        return _parse_between(self.reload_url, "k=", "$")

    def response(self) -> str:
        """Return the final reCAPTCHA ``rresp`` token, raising on failure."""
        token = self._get_internal_token()
        payload = (
            f"v={self.v}&reason=q&c={token}&k={self._resolved_key()}"
            f"&co={self.co}&hl=en&size=invisible&chr=%5B89%2C64%2C27%5D"
            f"&vh=13599012192&bg={BG_PAYLOAD}"
        )

        try:
            req = self.session.post(
                self.reload_url,
                headers=self.headers,
                data=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"reCAPTCHA reload failed: {exc}") from exc

        results = re.findall(r'"rresp","(.*?)"', req.text)
        if not results:
            raise RuntimeError("reCAPTCHA reload response did not contain rresp token")
        return results[0]
