from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)
from django.utils.translation import gettext_lazy as _

from accounts.models import UserProfile

User = get_user_model()


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": "form-control form-control-lg", "autofocus": True}
        )
        self.fields["password"].widget.attrs.update({"class": "form-control form-control-lg"})


class ManagementUserForm(UserCreationForm):
    full_name = forms.CharField(label=_("Full name"), max_length=200)
    role = forms.ChoiceField(
        label=_("Role"),
        choices=UserProfile.Role.choices,
        initial=UserProfile.Role.EMPLOYEE,
    )
    is_active_profile = forms.BooleanField(
        label=_("Profile active"),
        initial=True,
        required=False,
    )
    is_active = forms.BooleanField(
        label=_("Login enabled"),
        initial=True,
        required=False,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"class": "form-control"})
        for name in ("password1", "password2"):
            self.fields[name].widget.attrs.update({"class": "form-control"})
        self.fields["full_name"].widget.attrs.update({"class": "form-control"})
        self.fields["role"].widget.attrs.update({"class": "form-select"})
        self.fields["is_active_profile"].widget.attrs.update({"class": "form-check-input"})
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.is_active = self.cleaned_data.get("is_active", True)
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.full_name = self.cleaned_data["full_name"]
            profile.role = self.cleaned_data["role"]
            profile.is_active_profile = self.cleaned_data.get("is_active_profile", True)
            profile.save()
        return user


class UserProfileEditForm(forms.ModelForm):
    """Full management editor for any other user's account.

    Covers everything an admin can reasonably want to change in one place:
    identity (username / full name / avatar), role, gating flags
    (`is_active_profile`, `is_active`), and an *optional* password reset.
    The password fields are blank by default — leaving them empty just
    saves the rest of the form without touching the existing password,
    so management can edit a profile without forcing a credential rotation.
    """

    username = forms.CharField(
        label=_("Username"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    avatar = forms.ImageField(
        label=_("Profile picture"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    is_active = forms.BooleanField(label=_("Login enabled"), required=False)
    new_password1 = forms.CharField(
        label=_("New password"),
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text=_("Leave blank to keep the current password."),
    )
    new_password2 = forms.CharField(
        label=_("Confirm new password"),
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    class Meta:
        model = UserProfile
        fields = ["full_name", "role", "is_active_profile"]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "role": forms.Select(attrs={"class": "form-select"}),
            "is_active_profile": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["is_active"].initial = user.is_active
            self.fields["username"].initial = user.username
            self.fields["avatar"].initial = self.instance.avatar if self.instance else None
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username:
            raise forms.ValidationError(_("Username cannot be empty."))
        if self.user is not None:
            clash = (
                User.objects.exclude(pk=self.user.pk)
                .filter(username__iexact=username)
                .exists()
            )
            if clash:
                raise forms.ValidationError(_("This username is already taken."))
        return username

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("new_password1") or ""
        pw2 = cleaned.get("new_password2") or ""
        if pw1 or pw2:
            if pw1 != pw2:
                self.add_error("new_password2", _("The two password fields didn't match."))
            elif len(pw1) < 8:
                self.add_error(
                    "new_password1",
                    _("Password must be at least 8 characters long."),
                )
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=False)
        if commit and self.user is not None:
            self.user.username = self.cleaned_data["username"]
            self.user.is_active = self.cleaned_data.get("is_active", self.user.is_active)
            new_password = self.cleaned_data.get("new_password1") or ""
            if new_password:
                self.user.set_password(new_password)
            self.user.save()
            profile.user = self.user
            avatar = self.cleaned_data.get("avatar")
            # ClearableFileInput sends False when the user ticked "Clear".
            # Anything truthy is a fresh upload; None means "leave alone".
            if avatar is False:
                profile.avatar = None
            elif avatar:
                profile.avatar = avatar
            profile.save()
        return profile


class AccountSettingsForm(forms.Form):
    """Self-service profile editor: username, display name, avatar.

    Available to every logged-in user (employee or management) so each
    person can keep their own identity up to date without bothering an
    admin. Username is unique and validated against the User table.
    Password change is handled by a separate :class:`AccountPasswordForm`
    posted from the same screen so we don't conflate "save profile"
    with "rotate credentials".
    """

    username = forms.CharField(
        label=_("Username"),
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "username"}),
    )
    full_name = forms.CharField(
        label=_("Full name"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    avatar = forms.ImageField(
        label=_("Profile picture"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        initial = kwargs.pop("initial", {}) or {}
        initial.setdefault("username", user.username)
        initial.setdefault("full_name", getattr(user.profile, "full_name", "") or "")
        super().__init__(*args, initial=initial, **kwargs)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if not username:
            raise forms.ValidationError(_("Username cannot be empty."))
        clash = (
            User.objects.exclude(pk=self.user.pk)
            .filter(username__iexact=username)
            .exists()
        )
        if clash:
            raise forms.ValidationError(_("This username is already taken."))
        return username

    def save(self):
        self.user.username = self.cleaned_data["username"]
        self.user.save(update_fields=["username"])
        profile = self.user.profile
        profile.full_name = (self.cleaned_data.get("full_name") or "").strip()
        avatar = self.cleaned_data.get("avatar")
        # Django's ClearableFileInput sends False when the user ticked
        # "Clear" — wipe the field then. Otherwise only assign when a
        # fresh file came in so we don't clobber the existing avatar.
        if avatar is False:
            profile.avatar = None
        elif avatar:
            profile.avatar = avatar
        profile.save()
        return profile


class AccountPasswordForm(PasswordChangeForm):
    """Bootstrap-styled wrapper around Django's stock PasswordChangeForm."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("old_password", "new_password1", "new_password2"):
            self.fields[name].widget.attrs.update(
                {"class": "form-control", "autocomplete": "new-password"}
            )
