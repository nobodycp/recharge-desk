from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import translation

from core.templatetags.ui_tags import rd_sparkline_points, rd_trend, rd_weekday_initial


def _series(*counts, key="count"):
    return [{key: c} for c in counts]


def _pairs(points):
    return [tuple(float(n) for n in p.split(",")) for p in points.split()]


class SparklinePointsTests(SimpleTestCase):
    def test_sparkline_points_basic(self):
        pts = _pairs(rd_sparkline_points(_series(0, 5, 10)))
        self.assertEqual([p[0] for p in pts], [0, 50, 100])
        self.assertEqual(pts[0][1], 26)  # height 28 - 2px padding
        self.assertEqual(pts[1][1], 14)
        self.assertEqual(pts[-1][1], 2)

    def test_sparkline_points_all_zero_draws_nothing(self):
        # A flat line of zeros carries no information (reads like a progress bar).
        self.assertEqual(rd_sparkline_points(_series(0, 0, 0, 0)), "")
        self.assertEqual(rd_sparkline_points([{"count": None}, {}]), "")

    def test_sparkline_points_constant_non_zero_is_flat(self):
        pts = _pairs(rd_sparkline_points(_series(7, 7, 7)))
        self.assertEqual(len({p[1] for p in pts}), 1)

    def test_sparkline_points_too_short(self):
        self.assertEqual(rd_sparkline_points([]), "")
        self.assertEqual(rd_sparkline_points(_series(3)), "")
        self.assertEqual(rd_sparkline_points(None), "")

    def test_sparkline_points_key_width_height(self):
        pts = _pairs(rd_sparkline_points(_series(1, 3, key="volume"), key="volume", width=60, height=20))
        self.assertEqual(pts, [(0, 18), (60, 2)])

    def test_sparkline_points_tolerates_missing_values(self):
        pts = _pairs(rd_sparkline_points([{"count": None}, {}, {"count": 4}]))
        self.assertEqual([p[1] for p in pts], [26, 26, 2])


class TrendTests(SimpleTestCase):
    def test_trend_up_down_flat(self):
        self.assertEqual(rd_trend(_series(1, 10, 15)), {"direction": "up", "pct": 50})
        self.assertEqual(rd_trend(_series(9, 10, 5)), {"direction": "down", "pct": 50})
        self.assertEqual(rd_trend(_series(0, 8, 8)), {"direction": "flat", "pct": 0})

    def test_trend_uses_key(self):
        series = [{"count": 1, "volume": 100}, {"count": 1, "volume": 150}]
        self.assertEqual(rd_trend(series, key="volume"), {"direction": "up", "pct": 50})
        self.assertEqual(rd_trend(series)["direction"], "flat")

    def test_trend_handles_zero_previous(self):
        self.assertIsNone(rd_trend(_series(0, 5)))
        self.assertIsNone(rd_trend(_series(0, 0)))

    def test_trend_needs_two_buckets(self):
        self.assertIsNone(rd_trend([]))
        self.assertIsNone(rd_trend(_series(4)))
        self.assertIsNone(rd_trend(None))


class TrendChipTests(SimpleTestCase):
    def _render(self, trend):
        return render_to_string("partials/kpi_trend.html", {"trend": trend})

    def test_trend_chip_is_ltr_isolated(self):
        up = self._render({"direction": "up", "pct": 12})
        self.assertIn("rd-trend--up", up)
        self.assertIn('dir="ltr"', up)
        self.assertIn("+12%", up)
        self.assertIn("▲", up)
        down = self._render({"direction": "down", "pct": 8})
        self.assertIn("rd-trend--down", down)
        self.assertIn("▼", down)
        self.assertIn("−8%", down)
        flat = self._render({"direction": "flat", "pct": 0})
        self.assertIn("rd-trend--flat", flat)
        self.assertIn("0%", flat)

    def test_trend_chip_has_screen_reader_text(self):
        html = self._render({"direction": "up", "pct": 12})
        self.assertIn("visually-hidden", html)
        self.assertIn("vs yesterday", html)
        # The text is visually hidden, not duplicated in a tooltip attribute.
        self.assertNotIn("title=", html)

    def test_trend_chip_renders_nothing_without_trend(self):
        self.assertEqual(self._render(None).strip(), "")


class SparklinePartialTests(SimpleTestCase):
    def test_sparkline_partial(self):
        html = render_to_string(
            "partials/kpi_sparkline.html", {"points": "0,26 100,2", "accent": True}
        )
        self.assertIn("rd-spark", html)
        self.assertIn("rd-spark--accent", html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('points="0,26 100,2"', html)

    def test_sparkline_markup_spans_the_card_with_a_fixed_stroke(self):
        html = render_to_string("partials/kpi_sparkline.html", {"points": "0,26 100,2"})
        self.assertIn('preserveAspectRatio="none"', html)
        self.assertIn('vector-effect="non-scaling-stroke"', html)

    def test_sparkline_partial_empty(self):
        self.assertEqual(
            render_to_string("partials/kpi_sparkline.html", {"points": ""}).strip(), ""
        )


class WeekdayInitialTests(SimpleTestCase):
    def test_weekday_initial_english_and_arabic(self):
        # 2026-10-12 is a Monday.
        with translation.override("en"):
            self.assertEqual(rd_weekday_initial("2026-10-12"), "M")
        with translation.override("ar"):
            self.assertEqual(rd_weekday_initial("2026-10-12"), "ن")

    def test_weekday_initial_covers_the_whole_week(self):
        with translation.override("en"):
            letters = [rd_weekday_initial(f"2026-10-{12 + i}") for i in range(7)]
        self.assertEqual(letters, ["M", "T", "W", "T", "F", "S", "S"])
        with translation.override("ar"):
            letters = [rd_weekday_initial(f"2026-10-{12 + i}") for i in range(7)]
        self.assertEqual(letters, ["ن", "ث", "ر", "خ", "ج", "س", "ح"])

    def test_weekday_initial_accepts_date_objects(self):
        from datetime import date

        with translation.override("en"):
            self.assertEqual(rd_weekday_initial(date(2026, 10, 14)), "W")

    def test_weekday_initial_bad_input_is_empty(self):
        self.assertEqual(rd_weekday_initial(""), "")
        self.assertEqual(rd_weekday_initial(None), "")
        self.assertEqual(rd_weekday_initial("2026-10"), "")
        self.assertEqual(rd_weekday_initial("nope"), "")


class EmptyStatePartialTests(SimpleTestCase):
    def test_empty_state_partial_renders_title_and_action(self):
        html = render_to_string(
            "partials/empty_state.html",
            {
                "title": "Nothing here",
                "text": "Add your first record.",
                "action_url": "/go/",
                "action_label": "Add record",
            },
        )
        self.assertIn("empty-state", html)
        self.assertIn("Nothing here", html)
        self.assertIn("Add your first record.", html)
        self.assertIn('href="/go/"', html)
        self.assertIn("Add record", html)

    def test_empty_state_partial_title_only(self):
        html = render_to_string("partials/empty_state.html", {"title": "Nothing here"})
        self.assertIn("Nothing here", html)
        self.assertNotIn("<a ", html)
        self.assertNotIn("empty-state__text", html)
        self.assertNotIn("role=", html)  # static content, not a live region

    def test_empty_state_partial_icon_is_decorative(self):
        html = render_to_string(
            "partials/empty_state.html", {"title": "T", "icon": "inbox"}
        )
        self.assertIn("empty-state__icon", html)
        self.assertIn('aria-hidden="true"', html)
