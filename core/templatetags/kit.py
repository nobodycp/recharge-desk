import copy
import numbers
import os

from django import forms, template
from django.template.loader import get_template
from django.utils.html import format_html

from core.templatetags.ui_tags import querystring

register = template.Library()


def _merge_classes(existing, *added):
    names = []
    for name in str(existing or "").split() + list(added):
        if name not in names:
            names.append(name)
    return " ".join(names)


def _is_file(widget):
    return isinstance(widget, forms.FileInput)


def _is_choices(widget):
    """Radio / checkbox lists: several inputs inside one wrapper."""
    return isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple))


def rd_widget_attrs(bound_field, size="", has_help=None):
    """Attributes the kit adds to a field's widget: Bootstrap classes, validity and help wiring."""
    widget = bound_field.field.widget
    attrs = dict(widget.attrs)
    small = size == "sm"

    if isinstance(widget, forms.Select):
        classes = ["form-select"] + (["form-select-sm"] if small else [])
    elif isinstance(widget, forms.CheckboxInput):
        classes = ["form-check-input"]
    elif _is_file(widget):
        classes = ["visually-hidden"]
    elif _is_choices(widget):
        # The wrapper gets the list class; `_render_widget` gives each option `form-check-input`.
        classes = ["rd-field__choices"]
    else:
        classes = ["form-control"] + (["form-control-sm"] if small else [])

    invalid = bool(bound_field.errors)
    if invalid:
        if not _is_choices(widget):
            classes.append("is-invalid")
        attrs["aria-invalid"] = "true"
    attrs["class"] = _merge_classes(attrs.get("class"), *classes)

    if has_help is None:
        has_help = bool(bound_field.help_text)
    described = attrs.get("aria-describedby", "").split()
    if bound_field.auto_id:
        if has_help:
            described.append(f"{bound_field.auto_id}-help")
        if invalid:
            described.append(f"{bound_field.auto_id}-error")
    if described:
        attrs["aria-describedby"] = " ".join(described)
    return attrs


def _render_widget(bound_field, attrs):
    widget = bound_field.field.widget
    if isinstance(widget, forms.DateInput):
        widget = copy.copy(widget)
        widget.format = "%Y-%m-%d"
    elif _is_file(widget):
        # The kit draws the current file and the clear checkbox itself.
        widget = forms.FileInput(attrs=widget.attrs)
    elif _is_choices(widget):
        widget = copy.copy(widget)
        widget.create_option = _option_factory(widget, invalid=bool(bound_field.errors))
    else:
        widget = None
    return bound_field.as_widget(widget=widget, attrs=attrs)


def _option_factory(widget, invalid):
    """Wrap `create_option` so each radio / checkbox is a `form-check-input`, not a copy of the wrapper's attrs."""
    create_option = widget.create_option

    def wrapper(*args, **kwargs):
        option = create_option(*args, **kwargs)
        attrs = {k: v for k, v in option["attrs"].items() if k not in ("class", "role", "aria-labelledby", "aria-describedby", "aria-invalid")}
        attrs["class"] = "form-check-input is-invalid" if invalid else "form-check-input"
        option["attrs"] = attrs
        return option

    return wrapper


@register.inclusion_tag("partials/kit/field.html")
def rd_field(field, size="", full=False, label=None, help=None, wide=False):
    widget = field.field.widget
    help_html = field.help_text if help is None else help
    is_file = _is_file(widget)
    is_check = isinstance(widget, forms.CheckboxInput)

    wrapper = ["rd-field"]
    if is_check:
        wrapper.append("rd-field--check")
    if full or is_file or isinstance(widget, forms.Textarea) or "<ul" in str(help_html):
        wrapper.append("rd-field--full")
    if wide:
        wrapper.append("rd-field--wide")

    widget_attrs = rd_widget_attrs(field, size, has_help=bool(help_html))
    context = {
        "field": field,
        "wrapper_class": " ".join(wrapper),
        "label": field.label if label is None else label,
        "help": help_html,
        "help_id": f"{field.auto_id}-help",
        "error_id": f"{field.auto_id}-error",
        "widget_html": _render_widget(field, widget_attrs),
        "described_by": widget_attrs.get("aria-describedby", ""),
        "is_file": is_file,
        "is_check": is_check,
        "is_choices": _is_choices(widget),
        "is_switch": is_check and widget.attrs.get("role") == "switch",
    }
    if is_file:
        initial = field.initial
        has_initial = bool(initial and getattr(initial, "url", False))
        clearable = isinstance(widget, forms.ClearableFileInput) and not field.field.required
        context["current_file"] = os.path.basename(str(initial)) if has_initial else ""
        if has_initial and clearable:
            context["clear_name"] = widget.clear_checkbox_name(field.html_name)
            context["clear_id"] = widget.clear_checkbox_id(context["clear_name"])
    return context


@register.inclusion_tag("partials/kit/fields.html")
def rd_fields(form, size="", full="", exclude=""):
    spanning = full.split()
    skipped = exclude.split()
    return {
        "form": form,
        "errors": form.non_field_errors(),
        "fields": [
            (field, field.name in spanning)
            for field in form.visible_fields()
            if field.name not in skipped
        ],
        "size": size,
    }


PAGE_HEADER_ARGS = ("title", "subtitle", "back_url", "back_label", "meta")


class PageHeaderNode(template.Node):
    """`{% rd_page_header title ... %}actions{% end_rd_page_header %}`: the body is the actions slot."""

    def __init__(self, args, kwargs, nodelist):
        self.args = args
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context):
        values = dict(zip(PAGE_HEADER_ARGS, (arg.resolve(context) for arg in self.args)))
        values.update({name: expr.resolve(context) for name, expr in self.kwargs.items()})
        actions = self.nodelist.render(context)
        values = {name: ("" if value is None else value) for name, value in values.items()}
        if isinstance(values.get("meta"), numbers.Number):
            values["meta"] = str(values["meta"])  # a count of 0 must still show
        values["actions"] = actions if actions.strip() else ""
        return get_template("partials/kit/page_header.html").render(values)


@register.tag("rd_page_header")
def rd_page_header(parser, token):
    bits = token.split_contents()[1:]
    args, kwargs = [], {}
    for bit in bits:
        if "=" in bit and not bit.startswith(("'", '"')):
            name, _, expr = bit.partition("=")
            if name not in PAGE_HEADER_ARGS:
                raise template.TemplateSyntaxError(f"rd_page_header: unknown argument '{name}'")
            kwargs[name] = parser.compile_filter(expr)
        elif kwargs:
            raise template.TemplateSyntaxError("rd_page_header: positional argument after keyword argument")
        else:
            args.append(parser.compile_filter(bit))
    if len(args) > len(PAGE_HEADER_ARGS):
        raise template.TemplateSyntaxError("rd_page_header: too many arguments")
    if "title" not in kwargs and not args:
        raise template.TemplateSyntaxError("rd_page_header requires a title")
    nodelist = parser.parse(("end_rd_page_header",))
    parser.delete_first_token()
    return PageHeaderNode(args, kwargs, nodelist)


SEARCH_FIELDS = ("q", "search")


def _is_search_name(name):
    """`q` / `search`, or a prefixed twin such as `ledger_q` (several grids on one page)."""
    return name in SEARCH_FIELDS or name.endswith("_q")


def _filled(request, key):
    return any(str(value).strip() for value in request.GET.getlist(key))


@register.inclusion_tag("partials/kit/filter_card.html", takes_context=True)
def rd_filter_card(context, form, action="", reset_url="", hx_target="", indicator_id="", keep="", title="", open=None, hx_vals="", show_errors=True):
    """Collapsible filter card around a GET form. Counts only the form's own fields as active."""
    request = context["request"]
    active_count = sum(1 for name in form.fields if _filled(request, form.add_prefix(name)))
    is_open = bool(active_count or form.errors) if open is None else bool(open)
    path = action or request.path
    visible = form.visible_fields()
    own_names = {form.add_prefix(name) for name in form.fields}  # the form already posts these
    kept = [(key, value) for key in keep.split() if key not in own_names for value in request.GET.getlist(key)]
    return {
        "form": form,
        "title": title,
        "active_count": active_count,
        "is_open": is_open,
        "action_url": path,
        "reset_url": reset_url or request.path,
        "hx_target": hx_target,
        "hx_vals": hx_vals,
        "indicator_id": indicator_id,
        "hidden_fields": form.hidden_fields(),
        "kept": kept,
        "fields": [(field, i == 0 and _is_search_name(field.name)) for i, field in enumerate(visible)],
        "errors": form.non_field_errors() if show_errors else [],
    }


@register.simple_tag(takes_context=True)
def rd_sort_link(context, key, label, sort, order, page_key="page", sort_param="sort", order_param="order", first="desc"):
    """Sortable column header. Same hrefs as the hand-written headers:
    the active column in desc flips to asc, any other click gives `first` ("desc")."""
    active = sort == key
    direction = order if order in ("asc", "desc") else "asc"
    new_order = ("asc" if direction == "desc" else "desc") if active else first
    href = querystring(context, **{sort_param: key, order_param: new_order, page_key: 1})
    classes = f"rd-datagrid-sort is-active is-{direction}" if active else "rd-datagrid-sort"
    return format_html('<a class="{}" href="{}">{}</a>', classes, href, label)
