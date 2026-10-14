import json

from django.contrib import messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase

from core.middleware import HtmxNavigationMiddleware


def _request(**headers):
    request = RequestFactory().get("/somewhere/", **headers)
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request._messages = FallbackStorage(request)
    return request


class BoostedRequestsRenderFullPagesTests(SimpleTestCase):
    def _seen_hx_request(self, **headers):
        seen = {}

        def view(request):
            seen["hx"] = request.headers.get("HX-Request")
            seen["target"] = request.headers.get("HX-Target")
            return HttpResponse("ok")

        HtmxNavigationMiddleware(view)(_request(**headers))
        return seen

    def test_boosted_page_navigation_looks_like_a_normal_request(self):
        seen = self._seen_hx_request(HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true", HTTP_HX_TRIGGER="a1")
        self.assertIsNone(seen["hx"])

    def test_boosted_link_inside_a_results_grid_keeps_its_partial(self):
        seen = self._seen_hx_request(HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true", HTTP_HX_TARGET="sale-list-results")
        self.assertEqual(seen["hx"], "true")
        self.assertEqual(seen["target"], "sale-list-results")

    def test_history_restore_request_looks_like_a_normal_request(self):
        seen = self._seen_hx_request(HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
        self.assertIsNone(seen["hx"])

    def test_partial_htmx_request_is_left_alone(self):
        seen = self._seen_hx_request(HTTP_HX_REQUEST="true", HTTP_HX_TARGET="rows")
        self.assertEqual(seen["hx"], "true")
        self.assertEqual(seen["target"], "rows")

    def test_responses_vary_on_hx_request(self):
        response = HtmxNavigationMiddleware(lambda r: HttpResponse("ok"))(_request())
        self.assertIn("HX-Request", response["Vary"])


class PartialHtmxResponsesCarryMessagesTests(SimpleTestCase):
    def _run(self, view, **headers):
        request = _request(**headers)
        response = HtmxNavigationMiddleware(view)(request)
        return request, response

    def test_messages_become_toast_trigger(self):
        def view(request):
            messages.success(request, "Saved")
            messages.error(request, "Nope")
            return HttpResponse("<tr></tr>")

        request, response = self._run(view, HTTP_HX_REQUEST="true")
        payload = json.loads(response["HX-Trigger"])
        self.assertEqual(
            payload["rdToast"],
            [{"message": "Saved", "level": "success"}, {"message": "Nope", "level": "error"}],
        )
        self.assertTrue(request._messages.used)

    def test_existing_triggers_are_kept(self):
        def view(request):
            messages.info(request, "Also")
            response = HttpResponse("")
            response["HX-Trigger"] = json.dumps({"rdToast": {"message": "First", "level": "success"}, "other": 1})
            return response

        _, response = self._run(view, HTTP_HX_REQUEST="true")
        payload = json.loads(response["HX-Trigger"])
        self.assertEqual(payload["other"], 1)
        self.assertEqual(
            payload["rdToast"],
            [{"message": "First", "level": "success"}, {"message": "Also", "level": "info"}],
        )

    def test_plain_event_name_trigger_is_kept(self):
        def view(request):
            messages.info(request, "Hi")
            response = HttpResponse("")
            response["HX-Trigger"] = "rowsChanged"
            return response

        _, response = self._run(view, HTTP_HX_REQUEST="true")
        payload = json.loads(response["HX-Trigger"])
        self.assertIn("rowsChanged", payload)
        self.assertEqual(payload["rdToast"], [{"message": "Hi", "level": "info"}])

    def test_redirects_keep_messages_for_the_next_page(self):
        def view(request):
            messages.success(request, "Later")
            response = HttpResponse(status=204)
            response["HX-Redirect"] = "/next/"
            return response

        request, response = self._run(view, HTTP_HX_REQUEST="true")
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(request._messages.used)

        def redirect_view(request):
            messages.success(request, "Later")
            return HttpResponseRedirect("/next/")

        request, response = self._run(redirect_view, HTTP_HX_REQUEST="true")
        self.assertNotIn("HX-Trigger", response)

    def test_full_page_requests_leave_messages_to_the_template(self):
        def view(request):
            messages.success(request, "Rendered by base template")
            return HttpResponse("<html></html>")

        request, response = self._run(view, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true")
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(request._messages.used)
