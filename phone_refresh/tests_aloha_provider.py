from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from phone_refresh.providers.aloha import AlohaProvider, _REFRESH_TOKEN_RE

_HOME_HTML = """
<input type="hidden" id="refresh_token" autocomplete="OFF" name="refresh_token"
    value="c57e2e562048815259fe1faabe077ffe0710a15cb3a00a6a13e473aaef276fa0">
"""


class RefreshTokenRegexTests(TestCase):
    def test_extracts_token_from_hidden_input(self):
        match = _REFRESH_TOKEN_RE.search(_HOME_HTML)
        self.assertIsNotNone(match)
        token = match.group(1) or match.group(2)
        self.assertEqual(
            token,
            "c57e2e562048815259fe1faabe077ffe0710a15cb3a00a6a13e473aaef276fa0",
        )


class AlohaProviderTests(TestCase):
    @patch("phone_refresh.providers.aloha.requests.Session")
    def test_call_posts_phone_and_refresh_token(self, session_cls: MagicMock) -> None:
        session = session_cls.return_value
        home_resp = MagicMock()
        home_resp.text = _HOME_HTML
        home_resp.raise_for_status = MagicMock()

        post_resp = MagicMock()
        post_resp.text = '{"ok":true}'
        post_resp.json.return_value = {"ok": True}
        post_resp.status_code = 200

        session.get.return_value = home_resp
        session.post.return_value = post_resp

        result = AlohaProvider().call("0501234567")

        session.get.assert_called_once()
        session.post.assert_called_once()
        _, kwargs = session.post.call_args
        self.assertEqual(
            kwargs["data"],
            {
                "phone_number": "0501234567",
                "refresh_token": (
                    "c57e2e562048815259fe1faabe077ffe0710a15cb3a00a6a13e473aaef276fa0"
                ),
            },
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json, {"ok": True})
