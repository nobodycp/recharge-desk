"""Filter form for the audit log viewer.

Rendering only: ``audit.views.audit_log_list`` still parses ``request.GET`` itself;
this form exists so the filter card is drawn by the page kit with the same GET names.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from audit.models import AuditAction
from core.templatetags.ui_tags import model_verbose


class AuditLogFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label=_("Search target"),
        widget=forms.TextInput(attrs={"type": "search", "placeholder": _("Object id or text")}),
    )
    action = forms.ChoiceField(required=False, label=_("Action"))
    model = forms.ChoiceField(required=False, label=_("Model"))
    actor = forms.ChoiceField(required=False, label=_("Actor"))
    date_from = forms.DateField(
        required=False,
        label=_("Date from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Date to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, actors=(), models_seen=(), action_choices=AuditAction.choices, **kwargs):
        super().__init__(*args, **kwargs)
        all_label = ("", _("All"))
        self.fields["action"].choices = [all_label, *action_choices]
        self.fields["model"].choices = [all_label, *((m, model_verbose(m)) for m in models_seen)]
        self.fields["actor"].choices = [all_label, *((str(a["id"]), a["username"]) for a in actors)]
