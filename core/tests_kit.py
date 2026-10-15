from datetime import date

from django import forms
from django.template import Context, Template
from django.test import SimpleTestCase
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
