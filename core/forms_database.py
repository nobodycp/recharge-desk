from django import forms
from django.utils.translation import gettext_lazy as _

IMPORT_CONFIRM_WORD = "RESTORE"


class DatabaseImportForm(forms.Form):
    backup_file = forms.FileField(
        label=_("Backup file"),
        help_text=_("JSON (.json / .json.gz), PostgreSQL custom dump (.dump), or SQLite (.sqlite3)."),
    )
    confirm = forms.CharField(
        label=_("Confirmation"),
        help_text=_('Type %(word)s to confirm you want to replace all current data.') % {"word": IMPORT_CONFIRM_WORD},
        max_length=32,
    )
    acknowledge = forms.BooleanField(
        label=_("I understand this will erase existing data and replace it with the backup."),
        required=True,
    )

    def clean_confirm(self):
        value = (self.cleaned_data.get("confirm") or "").strip().upper()
        if value != IMPORT_CONFIRM_WORD:
            raise forms.ValidationError(
                _('Type "%(word)s" exactly to confirm.') % {"word": IMPORT_CONFIRM_WORD}
            )
        return value
