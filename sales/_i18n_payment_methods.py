"""
Translation anchors for PaymentMethod display names that live in the database.

These calls do nothing at runtime; they exist so `manage.py makemessages`
finds the literals and emits matching `msgid` entries into the locale
catalogues. Add a new line whenever a fresh PaymentMethod row is created
in production whose label needs to be displayed in another language.
"""

from django.utils.translation import gettext_lazy as _

_("Jawwal Pay")
_("Bank of Palestine")
_("Palpay")
