from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError

from core import audit

from .models import Approval, ApprovalPolicy, LoginAttempt, Role, RolePermission, User


class PinMixin(forms.Form):
    new_pin = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False), label="Set / reset PIN",
        help_text="4 to 8 digits. Leave blank to keep the current PIN.")

    def clean_new_pin(self):
        pin = self.cleaned_data.get("new_pin")
        if pin and not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise ValidationError("PIN must be 4 to 8 digits.")
        return pin


class StaffChangeForm(PinMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


class StaffCreationForm(PinMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "is_manager", "role")


@admin.register(User)
class StaffAdmin(BaseUserAdmin):
    form = StaffChangeForm
    add_form = StaffCreationForm
    list_display = ("username", "display_name", "role", "is_manager", "is_active", "biometric_enabled", "last_login")
    list_filter = ("is_manager", "is_active", "role")
    fieldsets = (
        (None, {"fields": ("username", "password", "new_pin")}),
        ("Person", {"fields": ("first_name", "last_name", "email")}),
        ("Access", {"fields": ("is_active", "is_manager", "role", "biometric_enabled")}),
        ("Activity", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": (
            "username", "first_name", "last_name", "password1", "password2", "new_pin",
            "is_manager", "role")}),
    )
    readonly_fields = ("last_login", "date_joined")

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead, so history keeps its author

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            old = User.objects.get(pk=obj.pk)
            previous = {"role": old.role_id, "is_manager": old.is_manager, "is_active": old.is_active,
                        "biometric_enabled": old.biometric_enabled}
        pin = form.cleaned_data.get("new_pin")
        if pin:
            obj.set_pin(pin)
        super().save_model(request, obj, form, change)
        new = {"role": obj.role_id, "is_manager": obj.is_manager, "is_active": obj.is_active,
               "biometric_enabled": obj.biometric_enabled}
        audit.record("staff.updated" if change else "staff.created", actor=request.user,
                     object_type="user", object_ref=obj.pk, previous=previous, new=new)
        if pin:
            audit.record("staff.pin_set", actor=request.user, object_type="user", object_ref=obj.pk)
        if change and "password" in form.changed_data:
            audit.record("staff.password_reset", actor=request.user, object_type="user", object_ref=obj.pk)


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "is_system")
    inlines = [RolePermissionInline]

    def save_related(self, request, form, formsets, change):
        before = set(form.instance.grants.values_list("code", flat=True)) if change else set()
        super().save_related(request, form, formsets, change)
        after = set(form.instance.grants.values_list("code", flat=True))
        if before != after or not change:
            audit.record("role.permissions_changed", actor=request.user, object_type="role",
                         object_ref=form.instance.pk, previous=sorted(before), new=sorted(after))


@admin.register(ApprovalPolicy)
class ApprovalPolicyAdmin(admin.ModelAdmin):
    list_display = ("action", "required")
    list_editable = ("required",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        audit.record("policy.changed", actor=request.user, object_type="approval_policy",
                     object_ref=obj.action, new={"required": obj.required})
        super().save_model(request, obj, form, change)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Approval)
class ApprovalAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "requester", "approver", "method", "object_ref", "consumed_at")
    list_filter = ("action", "method")
    exclude = ("token_hash",)


@admin.register(LoginAttempt)
class LoginAttemptAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "username", "success", "method", "context", "ip_address")
    list_filter = ("success", "context")
