from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
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
    is_active = forms.BooleanField(label=_("Login enabled"), required=False)

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
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})

    def save(self, commit=True):
        profile = super().save(commit=False)
        if commit and self.user is not None:
            self.user.is_active = self.cleaned_data.get("is_active", self.user.is_active)
            self.user.save()
            profile.user = self.user
            profile.save()
        return profile
