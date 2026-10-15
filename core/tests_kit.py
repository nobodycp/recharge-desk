from datetime import date

from django import forms
from django.template import Context, Template
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import translation


class _StoredFile:
    """Stands in for a FieldFile: the widget only needs a name and a url."""

    def __init__(self, name):
        self.name = name
        self.url = f"/media/{name}"

    def __str__(self):
        return self.name

    def __bool__(self):
        return True


class F(forms.Form):
    name = forms.CharField(label="Name", help_text="Shown on invoices")
    amount = forms.DecimalField(label="Amount", required=False)
    kind = forms.ChoiceField(label="Kind", choices=[("a", "A"), ("b", "B")])
    tags = forms.MultipleChoiceField(label="Tags", choices=[("a", "A")], required=False)
    notes = forms.CharField(label="Notes", widget=forms.Textarea, required=False)
    is_active = forms.BooleanField(label="Active", required=False)
    due = forms.DateField(label="Due", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    styled = forms.CharField(
        label="Styled", required=False, widget=forms.TextInput(attrs={"class": "form-control x"})
    )
    password = forms.CharField(label="Password", required=False, help_text="<ul><li>a</li></ul>")
    secret = forms.CharField(label="Secret", required=False, widget=forms.HiddenInput)


class UploadForm(forms.Form):
    logo = forms.FileField(label="Logo", required=False)
    backup = forms.FileField(label="Backup")


def render_field(bound_field, args=""):
    return Template("{% load kit %}{% rd_field field " + args + " %}").render(Context({"field": bound_field}))


def render_fields(form, args=""):
    return Template("{% load kit %}{% rd_fields form " + args + " %}").render(Context({"form": form}))


class FieldTests(SimpleTestCase):
    def test_text_field_gets_form_control_label_without_colon(self):
        html = render_field(F()["name"])
        self.assertIn('class="form-label"', html)
        self.assertNotIn(":</label>", html)
        self.assertRegex(html, r'<input[^>]*class="[^"]*\bform-control\b')

    def test_size_sm_adds_small_classes(self):
        self.assertRegex(render_field(F()["name"], 'size="sm"'), r'<input[^>]*\bform-control-sm\b')
        self.assertRegex(render_field(F()["kind"], 'size="sm"'), r'<select[^>]*\bform-select-sm\b')

    def test_select_gets_form_select_not_form_control(self):
        for name in ("kind", "tags"):
            html = render_field(F()[name])
            self.assertRegex(html, r'<select[^>]*class="[^"]*\bform-select\b')
            self.assertNotIn("form-control", html)

    def test_existing_widget_class_is_kept_once(self):
        html = render_field(F()["styled"])
        self.assertEqual(html.count('class="form-control x"'), 1)

    def test_checkbox_renders_form_check(self):
        html = render_field(F()["is_active"])
        self.assertIn("rd-field--check", html)
        self.assertRegex(html, r'<input[^>]*class="[^"]*\bform-check-input\b')
        self.assertIn("form-check-label", html)
        self.assertNotIn("form-control", html)

    def test_bound_error_marks_invalid_and_keeps_value(self):
        f = F(data={"name": "abc", "amount": "x"})
        f.is_valid()
        html = render_field(f["amount"])
        self.assertIn("is-invalid", html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn('value="x"', html)
        self.assertIn("rd-field__error", html)

    def test_valid_field_is_not_marked_invalid(self):
        f = F(data={"name": "abc"})
        f.is_valid()
        html = render_field(f["name"])
        self.assertNotIn("is-invalid", html)
        self.assertNotIn("aria-invalid", html)

    def test_help_text_linked_by_aria_describedby(self):
        html = render_field(F()["name"])
        self.assertIn('class="form-text rd-help" id="id_name-help"', html)
        self.assertIn("Shown on invoices", html)
        self.assertIn('aria-describedby="id_name-help"', html)

    def test_help_and_error_both_described(self):
        f = F(data={"amount": "x"})
        f.is_valid()
        self.assertIn('aria-describedby="id_amount-error"', render_field(f["amount"]))
        f = F(data={})
        f.is_valid()
        self.assertIn('aria-describedby="id_name-help id_name-error"', render_field(f["name"]))

    def test_help_and_label_overrides(self):
        html = render_field(F()["name"], 'label="Title" help="Custom <b>tip</b>"')
        self.assertIn(">Title<", html)
        self.assertIn("Custom <b>tip</b>", html)
        self.assertNotIn("Shown on invoices", html)

    def test_password_help_list_makes_field_full_width(self):
        self.assertIn("rd-field--full", render_field(F()["password"]))
        self.assertNotIn("rd-field--full", render_field(F()["name"]))

    def test_full_argument_makes_field_full_width(self):
        self.assertIn("rd-field--full", render_field(F()["name"], "full=True"))

    def test_date_initial_renders_iso_value(self):
        html = render_field(F(initial={"due": date(2026, 9, 3)})["due"])
        self.assertIn('value="2026-09-03"', html)
        self.assertIsNone(F.base_fields["due"].widget.format)

    def test_textarea_is_full_width(self):
        html = render_field(F()["notes"])
        self.assertIn("rd-field--full", html)
        self.assertRegex(html, r'<textarea[^>]*class="[^"]*\bform-control\b')

    def test_required_marker(self):
        self.assertIn('<span class="rd-field__req" aria-hidden="true">*</span>', render_field(F()["name"]))
        self.assertNotIn("rd-field__req", render_field(F()["amount"]))


class FileFieldTests(SimpleTestCase):
    def test_file_input_wrapped_in_arabic_picker(self):
        html = render_field(UploadForm()["backup"])
        self.assertIn("data-rd-file-pick", html)
        self.assertIn("data-rd-file-name", html)
        self.assertIn("Choose a file or drop it here", html)
        self.assertRegex(html, r'<input[^>]*type="file"[^>]*class="[^"]*\bvisually-hidden\b|<input[^>]*class="[^"]*\bvisually-hidden\b[^>]*type="file"')
        self.assertIn('name="backup"', html)
        self.assertIn("rd-field--full", html)
        self.assertNotIn("Currently", html)

    def test_clearable_with_initial_shows_name_and_clear_checkbox(self):
        html = render_field(UploadForm(initial={"logo": _StoredFile("logos/acme.png")})["logo"])
        self.assertIn("acme.png", html)
        self.assertIn('name="logo-clear"', html)
        self.assertIn("Remove current file", html)
        self.assertNotIn("Currently", html)
        self.assertNotRegex(html, r'<input[^>]*type="file"[^>]*value=')

    def test_picker_and_clear_strings_are_translated(self):
        form = UploadForm(initial={"logo": _StoredFile("logos/acme.png")})
        with translation.override("ar"):
            html = render_field(form["logo"])
        for text in ("اختر ملفاً أو اسحبه هنا", "الملف الحالي", "إزالة الملف الحالي"):
            self.assertIn(text, html)

    def test_clearable_without_initial_has_no_clear_checkbox(self):
        self.assertNotIn("-clear", render_field(UploadForm()["logo"]))

    def test_required_file_has_no_clear_checkbox(self):
        html = render_field(UploadForm(initial={"backup": _StoredFile("b.zip")})["backup"])
        self.assertNotIn("-clear", html)


class FieldsTests(SimpleTestCase):
    def test_hidden_fields_rendered_and_grid_wraps_visible(self):
        html = render_fields(F())
        self.assertIn("rd-form-grid", html)
        self.assertIn('name="secret"', html)
        self.assertLess(html.index('name="secret"'), html.index("rd-form-grid"))
        self.assertGreater(html.index('name="name"'), html.index("rd-form-grid"))

    def test_non_field_errors_go_to_toast(self):
        class Broken(forms.Form):
            a = forms.CharField(required=False)

            def clean(self):
                raise forms.ValidationError("Totals do not match")

        f = Broken(data={})
        f.is_valid()
        html = render_fields(f)
        self.assertIn("data-rd-flash", html)
        self.assertIn("Totals do not match", html)

    def test_full_names_span(self):
        html = render_fields(F(), 'full="amount"')
        self.assertRegex(html, r'<div class="[^"]*rd-field--full[^"]*">\s*<label class="form-label" for="id_amount"')
        self.assertNotRegex(html, r'<div class="[^"]*rd-field--full[^"]*">\s*<label class="form-label" for="id_name"')

    def test_exclude_skips_fields(self):
        html = render_fields(F(), 'exclude="is_active kind"')
        self.assertNotIn('name="is_active"', html)
        self.assertNotIn('name="kind"', html)
        self.assertIn('name="name"', html)

    def test_size_reaches_every_field(self):
        html = render_fields(F(), 'size="sm"')
        self.assertIn("form-control-sm", html)
        self.assertIn("form-select-sm", html)


def render_header(args, body="", context=None):
    source = "{% load kit %}{% rd_page_header " + args + " %}" + body + "{% end_rd_page_header %}"
    return Template(source).render(Context(context or {}))


class PageHeaderTests(SimpleTestCase):
    def test_title_only(self):
        html = render_header('"Customers"')
        self.assertRegex(html, r'<header class="rd-page-head">')
        self.assertEqual(html.count("<h1"), 1)
        self.assertRegex(html, r'<h1 class="rd-heading-xl">Customers</h1>')
        self.assertNotIn("rd-page-head__back", html)
        self.assertNotIn("rd-page-head__sub", html)
        self.assertNotIn("rd-page-head__meta", html)
        self.assertNotIn("rd-page-head__aside", html)

    def test_back_link_subtitle_meta_and_actions(self):
        html = render_header(
            'title=t subtitle="Everyone we bill" back_url="/customers/" back_label="All customers" meta="12 total"',
            body='<a class="btn btn-primary" href="/new/">New</a>',
            context={"t": "Customer"},
        )
        self.assertRegex(html, r'<a class="rd-page-head__back" href="/customers/">\s*<svg[^>]*aria-hidden="true"')
        self.assertIn("All customers", html)
        self.assertRegex(html, r'<p class="rd-page-head__sub">Everyone we bill</p>')
        self.assertRegex(html, r'<span class="rd-page-head__meta tabular-nums">12 total</span>')
        aside = html[html.index('class="rd-page-head__aside"'):]
        self.assertIn('<a class="btn btn-primary" href="/new/">New</a>', aside)
        self.assertLess(aside.index("12 total"), aside.index("btn-primary"))

    def test_back_label_defaults_to_translated_back(self):
        html = render_header('"X" back_url="/x/"')
        self.assertRegex(html, r"</svg>\s*Back\s*</a>")
        with translation.override("ar"):
            html = render_header('"X" back_url="/x/"')
        self.assertIn("رجوع", html)

    def test_actions_only_still_renders_aside(self):
        html = render_header('"X"', body="<button>Go</button>")
        self.assertIn("rd-page-head__aside", html)
        self.assertNotIn("rd-page-head__meta", html)

    def test_title_is_escaped(self):
        html = render_header("title", context={"title": "<b>x</b>"})
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)

    def test_subtitle_meta_and_back_are_escaped(self):
        html = render_header(
            'title subtitle=s meta=s back_url="/x/" back_label=s',
            context={"title": "T", "s": "<i>y</i>"},
        )
        self.assertNotIn("<i>y</i>", html)
        self.assertIn("&lt;i&gt;y&lt;/i&gt;", html)

    def test_zero_meta_is_shown(self):
        html = render_header('"X" meta=0')
        self.assertRegex(html, r'<span class="rd-page-head__meta tabular-nums">0</span>')
        html = render_header('"X" meta=count', context={"count": 0})
        self.assertRegex(html, r'<span class="rd-page-head__meta tabular-nums">0</span>')

    def test_empty_or_missing_meta_is_hidden(self):
        self.assertNotIn("rd-page-head__meta", render_header('"X" meta=""'))
        self.assertNotIn("rd-page-head__meta", render_header('"X" meta=missing'))
        self.assertNotIn("rd-page-head__meta", render_header('"X"'))

    def test_aside_form_buttons_fill_on_phones(self):
        css = open("static/css/design-system.css", encoding="utf-8").read()
        start = css.index(".rd-page-head__aside {\n    flex: 1 1 100%;")
        phone = css[start:start + 600]
        self.assertIn(".rd-page-head__aside > form .btn", phone)
        self.assertRegex(phone, r"\.rd-page-head__aside > form \.btn\s*\{[^}]*width:\s*100%")


class SearchForm(forms.Form):
    q = forms.CharField(label="Search", required=False)
    status = forms.ChoiceField(label="Status", required=False, choices=[("", "All"), ("paid", "Paid")])
    date_from = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise forms.ValidationError("From must be before To")
        return data


def request_for(query=""):
    return RequestFactory().get("/list/" + query)


def render_filter_card(args="form", query="", form=None, extra=None):
    request = request_for(query)
    form = form if form is not None else SearchForm(request.GET or None)
    context = {"request": request, "form": form}
    context.update(extra or {})
    return Template("{% load kit %}{% rd_filter_card " + args + " %}").render(Context(context))


class FilterCardTests(SimpleTestCase):
    def test_counts_only_form_fields(self):
        html = render_filter_card(query="?tab=x&sort=a&order=asc&page=2&q=ab")
        self.assertRegex(html, r"<span class=\"rd-filter-active-badge\" data-rd-filter-count>1</span>")

    def test_blank_and_whitespace_values_do_not_count(self):
        html = render_filter_card(query="?q=%20%20&status=&date_from=2024-01-01")
        self.assertRegex(html, r"data-rd-filter-count>1</span>")
        html = render_filter_card(query="?q=%20&status=")
        self.assertIn('data-rd-filter-count style="display:none"', html)

    def test_prefixed_form_counts_prefixed_names(self):
        form = SearchForm(prefix="f")
        html = render_filter_card(query="?f-q=abc&q=zzz&status=paid", form=form)
        self.assertRegex(html, r"data-rd-filter-count>1</span>")

    def test_keep_reposts_hidden_inputs(self):
        html = render_filter_card('form keep="sort order tab"', query="?sort=name&order=desc&other=1")
        self.assertIn('<input type="hidden" name="sort" value="name">', html)
        self.assertIn('<input type="hidden" name="order" value="desc">', html)
        self.assertNotIn('name="tab"', html)
        self.assertNotIn('name="other"', html)

    def test_keep_value_is_escaped(self):
        html = render_filter_card('form keep="tab"', query='?tab=%22%3E%3Cb%3E')
        self.assertNotIn("<b>", html)
        self.assertIn("&quot;&gt;&lt;b&gt;", html)

    def test_plain_get_form_without_target(self):
        html = render_filter_card('form action="/other/" reset_url="/list/"')
        self.assertRegex(html, r'<form method="get" action="/other/" class="rd-filter-grid"')
        self.assertNotIn("hx-", html.split("<form", 1)[1].split(">", 1)[0])
        self.assertIn('<form method="get" action="/list/" class="rd-filter-grid"', render_filter_card())

    def test_htmx_attrs_only_with_target(self):
        html = render_filter_card('form action="/list/" hx_target="#results" indicator_id="spin"')
        tag = html.split("<form", 1)[1].split(">", 1)[0]
        self.assertIn('hx-get="/list/"', tag)
        self.assertIn('hx-target="#results"', tag)
        self.assertIn('hx-push-url="true"', tag)
        self.assertIn('hx-indicator="#spin"', tag)
        self.assertIn('id="spin"', html)
        plain = render_filter_card('form action="/list/"')
        self.assertNotIn("hx-get", plain)

    def test_hx_get_defaults_to_current_path(self):
        html = render_filter_card('form hx_target="#r"')
        self.assertIn('hx-get="/list/"', html)

    def test_buttons_and_reset_link(self):
        html = render_filter_card('form reset_url="/list/"')
        actions = html[html.index("rd-filter-grid__actions"):]
        self.assertRegex(actions, r'<button type="submit" class="btn btn-primary btn-sm">Apply</button>')
        self.assertRegex(actions, r'<a class="btn btn-outline-secondary btn-sm" href="/list/">Reset</a>')

    def test_reset_defaults_to_current_path(self):
        html = render_filter_card()
        self.assertRegex(html, r'<a class="btn btn-outline-secondary btn-sm" href="/list/">Reset</a>')

    def test_non_field_error_visible(self):
        form = SearchForm({"date_from": "2024-02-01", "date_to": "2024-01-01"})
        html = render_filter_card(form=form, query="?date_from=2024-02-01&date_to=2024-01-01")
        self.assertRegex(html, r'<div class="rd-field__error[^"]*"[^>]*>\s*<div>From must be before To</div>')
        self.assertIn("<details", html)
        self.assertRegex(html, r"<details[^>]* open")

    def test_fields_small_and_without_colons(self):
        html = render_filter_card()
        self.assertRegex(html, r"<input[^>]*\bform-control-sm\b")
        self.assertRegex(html, r"<select[^>]*\bform-select-sm\b")
        self.assertNotIn(":</label>", html)

    def test_search_field_is_wide(self):
        html = render_filter_card()
        self.assertEqual(html.count("rd-field--wide"), 1)
        self.assertRegex(html, r'class="rd-field rd-field--wide"[^>]*>\s*<label class="form-label" for="id_q"')

    def test_open_state(self):
        self.assertNotRegex(render_filter_card(), r"<details[^>]* open")
        self.assertRegex(render_filter_card(query="?q=a"), r"<details[^>]* open")
        self.assertRegex(render_filter_card("form open=True"), r"<details[^>]* open")
        self.assertNotRegex(render_filter_card("form open=False", query="?q=a"), r"<details[^>]* open")

    def test_title_override(self):
        self.assertIn("<span>Refine</span>", render_filter_card('form title="Refine"'))
        self.assertIn("<span>Search & filters</span>", render_filter_card())


SORT_HAND_WRITTEN = (
    "{% load ui_tags %}"
    "{% if sort == 'role' and order == 'desc' %}{% querystring sort='role' order='asc' page=1 %}"
    "{% else %}{% querystring sort='role' order='desc' page=1 %}{% endif %}"
)


def render_sort(args, query="", context=None):
    ctx = {"request": request_for(query), "sort": "", "order": "asc"}
    ctx.update(context or {})
    return Template("{% load kit %}{% rd_sort_link " + args + " %}").render(Context(ctx))


def href_of(html):
    return html.split('href="', 1)[1].split('"', 1)[0]


class SortLinkTests(SimpleTestCase):
    def test_inactive_column_uses_first_order_and_resets_page(self):
        html = render_sort('"role" "Role" sort order', "?q=a&page=3&sort=username&order=asc",
                           {"sort": "username"})
        self.assertIn('class="rd-datagrid-sort"', html)
        self.assertEqual(href_of(html), "?q=a&amp;page=1&amp;sort=role&amp;order=desc")
        self.assertTrue(html.endswith(">Role</a>"))

    def test_first_order_can_be_asc(self):
        html = render_sort('"role" "Role" sort order first="asc"', "?page=3", {"sort": "username"})
        self.assertIn("order=asc", href_of(html))

    def test_active_asc_toggles_to_desc(self):
        html = render_sort('"role" "Role" sort order', "?sort=role&order=asc", {"sort": "role", "order": "asc"})
        self.assertIn('class="rd-datagrid-sort is-active is-asc"', html)
        self.assertIn("order=desc", href_of(html))

    def test_active_desc_toggles_to_asc(self):
        html = render_sort('"role" "Role" sort order', "?sort=role&order=desc", {"sort": "role", "order": "desc"})
        self.assertIn('class="rd-datagrid-sort is-active is-desc"', html)
        self.assertIn("order=asc", href_of(html))

    def test_hrefs_match_hand_written_headers(self):
        cases = [
            ("", {"sort": "", "order": "asc"}),
            ("?q=a&page=3", {"sort": "username", "order": "asc"}),
            ("?sort=role&order=asc&page=2&status=x", {"sort": "role", "order": "asc"}),
            ("?sort=role&order=desc&page=2&x=%C3%A9%26", {"sort": "role", "order": "desc"}),
        ]
        for query, ctx in cases:
            with self.subTest(query=query):
                full = {"request": request_for(query), **ctx}
                expected = Template(SORT_HAND_WRITTEN).render(Context(full))
                self.assertEqual(href_of(render_sort('"role" "Role" sort order', query, ctx)), expected)

    def test_custom_keys(self):
        html = render_sort(
            '"ref" "Ref" payments_sort payments_order page_key="payments_page" '
            'sort_param="payments_sort" order_param="payments_order"',
            "?payments_page=4&tab=p",
            {"payments_sort": "ref", "payments_order": "desc"},
        )
        self.assertIn("is-active is-desc", html)
        self.assertEqual(
            href_of(html), "?payments_page=1&amp;tab=p&amp;payments_sort=ref&amp;payments_order=asc"
        )

    def test_label_is_escaped(self):
        html = render_sort('"role" label sort order', context={"label": "<b>R</b>"})
        self.assertIn("&lt;b&gt;R&lt;/b&gt;", html)
        self.assertNotIn("<b>", html)


def render_actions(**kwargs):
    context = {"request": request_for(), "csrf_token": "tok"}
    context.update(kwargs)
    return render_to_string("partials/kit/row_actions.html", context)


class RowActionsTests(SimpleTestCase):
    def test_icon_variant_has_labels_and_escaped_confirm(self):
        html = render_actions(
            edit_url="/e/1/", delete_url="/d/1/", confirm='Delete "Ann" & her data?'
        )
        self.assertIn('<span class="rd-row-actions">', html)
        edit = html[html.index("<a "):html.index("</a>")]
        self.assertIn('class="rd-icon-btn"', edit)
        self.assertIn('href="/e/1/"', edit)
        self.assertIn('hx-boost="false" data-rd-page', edit)
        self.assertIn('aria-label="Edit"', edit)
        self.assertIn('title="Edit"', edit)
        self.assertIn('hx-confirm="Delete &quot;Ann&quot; &amp; her data?"', html)
        button = html[html.index("<button"):html.index("</button>")]
        self.assertIn('class="rd-icon-btn rd-icon-btn--danger"', button)
        self.assertIn('aria-label="Delete"', button)
        self.assertIn('title="Delete"', button)
        self.assertIn("<svg", button)
        self.assertIn('method="post" action="/d/1/"', html)

    def test_labels_are_overridable_and_translated(self):
        html = render_actions(edit_url="/e/", delete_url="/d/", edit_label="Edit user", delete_label="Delete user")
        self.assertIn('aria-label="Edit user"', html)
        self.assertIn('aria-label="Delete user"', html)
        with translation.override("ar"):
            html = render_actions(edit_url="/e/", delete_url="/d/")
        self.assertIn('aria-label="تعديل"', html)
        self.assertIn('aria-label="حذف"', html)

    def test_omits_delete_without_url(self):
        html = render_actions(edit_url="/e/")
        self.assertIn("rd-icon-btn", html)
        self.assertNotIn("<form", html)
        self.assertNotIn("<button", html)

    def test_omits_edit_without_url(self):
        html = render_actions(delete_url="/d/")
        self.assertNotIn("<a ", html)
        self.assertIn("<form", html)

    def test_next_is_hidden_input(self):
        self.assertIn('<input type="hidden" name="next" value="/back/?a=1&amp;b=2">',
                      render_actions(delete_url="/d/", next="/back/?a=1&b=2"))
        self.assertNotIn('name="next"', render_actions(delete_url="/d/"))

    def test_no_confirm_attr_without_text(self):
        self.assertNotIn("hx-confirm", render_actions(delete_url="/d/"))

    def test_hx_target_uses_hx_post(self):
        html = render_actions(delete_url="/d/", hx_target="#rows", hx_swap="outerHTML")
        form = html[html.index("<form"):html.index(">", html.index("<form"))]
        self.assertIn('hx-post="/d/"', form)
        self.assertIn('hx-target="#rows"', form)
        self.assertIn('hx-swap="outerHTML"', form)
        self.assertNotIn("hx-post", render_actions(delete_url="/d/"))

    def test_text_variant(self):
        html = render_actions(edit_url="/e/", delete_url="/d/", variant="text")
        self.assertNotIn("rd-icon-btn", html)
        self.assertIn('class="btn btn-sm btn-outline-secondary"', html)
        self.assertIn('class="btn btn-sm btn-outline-danger"', html)
        self.assertRegex(html, r">\s*Edit\s*</a>")
        self.assertRegex(html, r">\s*Delete\s*</button>")


class EmptyRowTests(SimpleTestCase):
    def test_empty_row_wraps_empty_state(self):
        html = render_to_string(
            "partials/kit/empty_row.html",
            {"colspan": 5, "title": "No users", "text": "Add one", "icon": "list"},
        )
        self.assertRegex(html, r'<tr class="rd-empty-row">\s*<td colspan="5">')
        self.assertIn('class="empty-state"', html)
        self.assertIn("No users", html)
        self.assertIn("Add one", html)
        self.assertIn("empty-state__icon", html)


class PaginationTests(TestCase):
    def render(self, query="?x=1", **context):
        request = request_for(query)
        page = Paginator(list(range(100)), 10).page(2)
        base = {"request": request, "page_obj": page}
        base.update(context)
        return render_to_string("partials/pagination.html", base)

    def per_page_hrefs(self, html):
        block = html[html.index("rd-datagrid-per-page"):]
        return [h for h in block.split('href="')[1:]]

    def test_per_page_links_reset_custom_page_key(self):
        html = self.render("?payments_page=2&q=a", page_key="payments_page")
        links = self.per_page_hrefs(html)
        self.assertTrue(links)
        for link in links:
            href = link.split('"', 1)[0]
            self.assertIn("payments_page=1", href)
            self.assertIn("per_page=", href)

    def test_per_page_links_reset_default_page_key(self):
        for href in (h.split('"', 1)[0] for h in self.per_page_hrefs(self.render("?page=2"))):
            self.assertIn("page=1", href)

    def test_known_sibling_keys_still_reset(self):
        for key in ("ledger_page", "sales_page", "phones_page"):
            with self.subTest(key=key):
                html = self.render(f"?{key}=2", page_key=key)
                self.assertIn(f"{key}=1", self.per_page_hrefs(html)[0])

    def test_sibling_reset_flag_still_resets_all(self):
        html = self.render("?ledger_page=2", pagination_reset_sibling_pages=True)
        href = self.per_page_hrefs(html)[0].split('"', 1)[0]
        for key in ("page", "ledger_page", "sales_page", "phones_page"):
            self.assertIn(f"{key}=1", href)
