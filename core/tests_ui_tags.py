from django.template.loader import render_to_string
from django.test import SimpleTestCase

from core.templatetags.ui_tags import rd_sparkline_points, rd_trend


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

    def test_sparkline_points_all_zero(self):
        pts = _pairs(rd_sparkline_points(_series(0, 0, 0, 0)))
        self.assertEqual(len(pts), 4)
        self.assertEqual({p[1] for p in pts}, {26})

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

    def test_sparkline_partial_empty(self):
        self.assertEqual(
            render_to_string("partials/kpi_sparkline.html", {"points": ""}).strip(), ""
        )
