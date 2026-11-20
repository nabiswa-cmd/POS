from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Role

User = get_user_model()


class StaffRoleChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return "Till supervisor" if obj.name == "Supervisor" else obj.name


class StaffCreationForm(forms.Form):
    username = forms.CharField(
        max_length=150, validators=User._meta.get_field("username").validators, label="Username")
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    role = StaffRoleChoiceField(
        queryset=Role.objects.none(), empty_label=None, label="Staff role")
    password = forms.CharField(
        widget=forms.PasswordInput, label="Password",
        help_text="At least 8 characters; staff can also sign in with a PIN if you set one.")
    pin = forms.CharField(
        required=False, max_length=8, widget=forms.PasswordInput, label="POS PIN",
        help_text="Optional: 4 to 8 digits.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.filter(
            name__in=("Cashier", "Supervisor")).order_by("name")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is already in use.")
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        try:
            password_validation.validate_password(password)
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return password

    def clean_pin(self):
        pin = self.cleaned_data.get("pin", "")
        if pin and not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise ValidationError("PIN must be 4 to 8 digits.")
        return pin

    @transaction.atomic
    def save(self, *, actor):
        user = User(
            username=self.cleaned_data["username"],
            first_name=self.cleaned_data["first_name"],
            last_name=self.cleaned_data["last_name"],
            role=self.cleaned_data["role"],
        )
        user.set_password(self.cleaned_data["password"])
        if self.cleaned_data["pin"]:
            user.set_pin(self.cleaned_data["pin"])
        user.save()

        from core import audit

        audit.record("staff.created", actor=actor, object_type="user", object_ref=user.pk,
                     new={"role": user.role.name, "is_manager": False})
        if self.cleaned_data["pin"]:
            audit.record("staff.pin_set", actor=actor, object_type="user", object_ref=user.pk)
        return user
