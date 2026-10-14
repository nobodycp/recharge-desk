"""Tests for the "تقارير المزودين" provider operations report page."""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from providers.models import ProviderOperation

User = get_user_model()


def _make_user(username, role):
    user = User.objects.create_user(username, password="x")
    UserProfile.objects.update_or_create(
        user=user, defaults={"role": role, "is_active_profile": True}
    )
    return user


class ProviderOperationsReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = _make_user("ops_mgr", UserProfile.Role.MANAGEMENT)
        cls.employee = _make_user("ops_emp", UserProfile.Role.EMPLOYEE)

    def setUp(self):
        self.client.force_login(self.manager)
        self.url = reverse("reports:provider_operations")

    def _op(self, age_days=0, **kwargs):
        defaults = dict(
            provider_key="sky",
            service="refresh",
            phone="0591000000",
            outcome="success",
            error_code="",
            message="تمّ التحديث",
            source="web",
        )
        defaults.update(kwargs)
        op = ProviderOperation.objects.create(**defaults)
        if age_days:
            ProviderOperation.objects.filter(pk=op.pk).update(
                created_at=timezone.now() - timedelta(days=age_days)
            )
        return op

    def _page_ids(self, response):
        return [op.pk for op in response.context["page_obj"]]

    # -- access ---------------------------------------------------------

    def test_employee_is_redirected(self):
        self.client.force_login(self.employee)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:forbidden"))

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_management_sees_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقارير المزودين")
        self.assertContains(response, "لا توجد عمليات في هذه الفترة")

    def test_nav_links_to_report(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'href="{self.url}"')

    # -- provider tabs --------------------------------------------------

    def test_provider_filter_and_tabs(self):
        sky = self._op(provider_key="sky", message="رسالة-سكاي")
        layan = self._op(provider_key="layan", message="رسالة-ليان")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(self._page_ids(response), [sky.pk])
        self.assertContains(response, "رسالة-سكاي")
        self.assertNotContains(response, "رسالة-ليان")
        tabs = {t["key"]: t["count"] for t in response.context["providers"]}
        self.assertEqual(tabs["sky"], 1)
        self.assertEqual(tabs["layan"], 1)
        self.assertIn("aloha", tabs)
        response = self.client.get(self.url, {"provider": "layan"})
        self.assertEqual(self._page_ids(response), [layan.pk])

    def test_tab_names_are_arabic_for_sky_and_titled_otherwise(self):
        response = self.client.get(self.url)
        names = {t["key"]: t["name"] for t in response.context["providers"]}
        self.assertEqual(names["sky"], "سكاي")
        self.assertEqual(names["layan"], "Layan")
        self.assertContains(response, "سكاي")

    def test_tab_counts_ignore_other_filters_but_respect_period(self):
        self._op(provider_key="sky", outcome="failed", error_code="connection")
        self._op(provider_key="sky", outcome="success")
        self._op(provider_key="sky", age_days=20)
        response = self.client.get(
            self.url, {"provider": "sky", "outcome": "failed", "period": "7d"}
        )
        tabs = {t["key"]: t["count"] for t in response.context["providers"]}
        self.assertEqual(tabs["sky"], 2)
        self.assertEqual(response.context["summary"]["total"], 1)

    def test_default_provider_is_first_with_rows(self):
        op = self._op(provider_key="layan")
        response = self.client.get(self.url)
        self.assertEqual(response.context["provider"], "layan")
        self.assertEqual(self._page_ids(response), [op.pk])

    def test_default_provider_is_sky_without_rows(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["provider"], "sky")

    def test_invalid_params_fall_back_to_defaults(self):
        op = self._op()
        response = self.client.get(
            self.url,
            {"provider": "nope", "period": "custom", "from": "bad", "to": "worse"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"], "7d")
        self.assertEqual(response.context["provider"], "sky")
        self.assertEqual(self._page_ids(response), [op.pk])

    # -- filters --------------------------------------------------------

    def test_service_filter(self):
        refresh = self._op(service="refresh")
        self._op(service="charge")
        response = self.client.get(self.url, {"provider": "sky", "service": "refresh"})
        self.assertEqual(self._page_ids(response), [refresh.pk])

    def test_outcome_filter(self):
        self._op(outcome="success")
        failed = self._op(outcome="failed", error_code="connection")
        response = self.client.get(self.url, {"provider": "sky", "outcome": "failed"})
        self.assertEqual(self._page_ids(response), [failed.pk])

    def test_source_filter(self):
        self._op(source="web")
        sms = self._op(source="sms")
        response = self.client.get(self.url, {"provider": "sky", "source": "sms"})
        self.assertEqual(self._page_ids(response), [sms.pk])

    def test_unknown_filter_values_are_ignored(self):
        a = self._op(source="web")
        b = self._op(source="sms", outcome="failed", error_code="connection")
        response = self.client.get(
            self.url, {"provider": "sky", "source": "zzz", "outcome": "zzz", "service": "zzz"}
        )
        self.assertEqual(set(self._page_ids(response)), {a.pk, b.pk})

    def test_phone_filter_is_stripped_contains(self):
        hit = self._op(phone="0591234567")
        self._op(phone="0599999999")
        response = self.client.get(self.url, {"provider": "sky", "phone": " 0591 "})
        self.assertEqual(self._page_ids(response), [hit.pk])

    def test_period_today_excludes_older_rows(self):
        today = self._op()
        self._op(age_days=2)
        response = self.client.get(self.url, {"provider": "sky", "period": "today"})
        self.assertEqual(self._page_ids(response), [today.pk])

    def test_period_7d_and_30d(self):
        recent = self._op(age_days=2)
        mid = self._op(age_days=10)
        old = self._op(age_days=40)
        r7 = self.client.get(self.url, {"provider": "sky", "period": "7d"})
        self.assertEqual(self._page_ids(r7), [recent.pk])
        r30 = self.client.get(self.url, {"provider": "sky", "period": "30d"})
        self.assertEqual(set(self._page_ids(r30)), {recent.pk, mid.pk})
        self.assertNotIn(old.pk, self._page_ids(r30))

    def test_custom_range_is_inclusive(self):
        today = timezone.localdate()
        inside = self._op(age_days=3)
        self._op(age_days=10)
        self._op()  # today, after the range end
        start = (today - timedelta(days=4)).isoformat()
        end = (today - timedelta(days=3)).isoformat()
        response = self.client.get(
            self.url,
            {"provider": "sky", "period": "custom", "from": start, "to": end},
        )
        self.assertEqual(response.context["period"], "custom")
        self.assertEqual(self._page_ids(response), [inside.pk])

    # -- summary --------------------------------------------------------

    def test_summary_and_success_rate(self):
        self._op(service="refresh", outcome="success")
        self._op(service="refresh", outcome="success")
        self._op(service="refresh", outcome="failed", error_code="connection")
        self._op(service="charge", outcome="pending", message="الشحن قيد المتابعة")
        self._op(provider_key="layan", outcome="failed", error_code="internal")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(
            response.context["summary"],
            {"total": 4, "success": 2, "failed": 1, "pending": 1},
        )
        per_service = {label: (n, pct) for label, n, pct in response.context["per_service"]}
        self.assertEqual(per_service["التحديث"], (3, 67))
        self.assertEqual(per_service["الشحن"], (1, 0))

    def test_empty_summary_has_zero_rate(self):
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(
            response.context["summary"],
            {"total": 0, "success": 0, "failed": 0, "pending": 0},
        )
        self.assertEqual(response.context["per_service"], [])

    def test_top_causes_ordered_by_count(self):
        for _ in range(3):
            self._op(
                outcome="failed",
                error_code="login_failed",
                message="فشل التحديث — خطأ تسجيل الدخول",
            )
        self._op(
            outcome="failed",
            error_code="connection",
            message="فشل التحديث — تعذّر الاتصال بالمزوّد",
        )
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(
            response.context["top_causes"],
            [("خطأ تسجيل الدخول", 3), ("تعذّر الاتصال بالمزوّد", 1)],
        )

    def test_top_causes_limited_to_five_and_fallback_to_message(self):
        for i in range(7):
            self._op(
                outcome="failed",
                error_code=f"code_{i}",
                message=f"فشل الشحن — سبب {i}",
            )
        response = self.client.get(self.url, {"provider": "sky"})
        causes = response.context["top_causes"]
        self.assertEqual(len(causes), 5)
        self.assertEqual(causes[0], ("سبب 0", 1))

    def test_top_causes_ignore_non_failed_rows(self):
        self._op(outcome="success")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(response.context["top_causes"], [])

    # -- table ----------------------------------------------------------

    def test_pagination_50_per_page(self):
        ProviderOperation.objects.bulk_create(
            [
                ProviderOperation(
                    provider_key="sky",
                    service="refresh",
                    outcome="success",
                    message="m",
                    source="web",
                )
                for _ in range(60)
            ]
        )
        page1 = self.client.get(self.url, {"provider": "sky"})
        self.assertEqual(len(page1.context["page_obj"]), 50)
        page2 = self.client.get(self.url, {"provider": "sky", "page": 2})
        self.assertEqual(len(page2.context["page_obj"]), 10)
        self.assertContains(page1, "provider=sky")

    def test_detail_is_escaped(self):
        self._op(detail="<script>x</script>")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertContains(response, "&lt;script&gt;x&lt;/script&gt;")
        self.assertNotContains(response, "<script>x</script>")

    def test_sale_link_and_user_name(self):
        self._op(sale_id=4242, user=self.employee, source="employee")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertContains(response, reverse("sales:sale_edit", args=[4242]))
        self.assertContains(response, "data-sale-link")
        self.assertContains(response, "ops_emp")
        self.assertContains(response, "الموظف")

    def test_no_sale_link_without_sale_id(self):
        self._op()
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertNotContains(response, "data-sale-link")

    def test_badges_duration_and_attempts(self):
        self._op(outcome="success", duration_ms=1234, attempts=2)
        self._op(outcome="failed", error_code="connection", duration_ms=None, attempts=1)
        self._op(outcome="pending", message="الشحن قيد المتابعة")
        response = self.client.get(self.url, {"provider": "sky"})
        self.assertContains(response, "rd-badge--paid")
        self.assertContains(response, "rd-badge--cancelled")
        self.assertContains(response, "rd-badge--pending")
        self.assertContains(response, "1.2 ث")
        self.assertContains(response, "محاولات: 2")
        self.assertNotContains(response, "محاولات: 1")
