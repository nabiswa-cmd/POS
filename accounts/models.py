from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from .permissions import PERMISSIONS, RESTRICTED_ACTIONS


def validate_permission_code(value):
    if value not in PERMISSIONS:
        raise ValidationError(f"Unknown permission: {value}")


class Role(models.Model):
    name = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)
    is_system = models.BooleanField(default=False, help_text="Default roles created at setup.")

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="grants")
    code = models.CharField(max_length=60, validators=[validate_permission_code])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "code"], name="uniq_role_permission")]

    def __str__(self):
        return f"{self.role}: {self.code}"


class User(AbstractUser):
    """Staff account. Staff are deactivated, never deleted, so history keeps its author."""

    is_manager = models.BooleanField(default=False, help_text="Full access to every module.")
    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    pin_hash = models.CharField(max_length=256, blank=True, editable=False)
    biometric_enabled = models.BooleanField(
        default=False, help_text="Allow Windows Hello / fingerprint approval for this user (optional).")
    failed_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    locked_until = models.DateTimeField(null=True, blank=True, editable=False)

    def save(self, *args, **kwargs):
        # Only managers may enter the Django admin (Phase 1 management UI).
        self.is_staff = self.is_manager
        self.is_superuser = self.is_manager
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def permission_codes(self):
        if self.is_manager:
            return set(PERMISSIONS)
        if self.role_id is None:
            return set()
        return set(self.role.grants.values_list("code", flat=True))

    def can(self, code):
        if code not in PERMISSIONS:
            raise ValueError(f"Unknown permission code: {code}")
        if not self.is_active:
            return False
        return code in self.permission_codes()

    def set_pin(self, pin):
        pin = str(pin)
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            raise ValidationError("PIN must be 4 to 8 digits.")
        self.pin_hash = make_password(pin)

    def check_pin(self, pin):
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)


class LoginAttempt(models.Model):
    """Login history (append-only): successes and failures."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    username = models.CharField(max_length=150)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="login_attempts")
    success = models.BooleanField()
    method = models.CharField(max_length=10, blank=True)
    context = models.CharField(max_length=20, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Login history cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Login history cannot be deleted.")


class ApprovalPolicy(models.Model):
    """Which restricted actions need supervisor/manager approval (manager-configurable)."""

    action = models.CharField(max_length=40, unique=True,
                              choices=[(k, v["label"]) for k, v in RESTRICTED_ACTIONS.items()])
    required = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Approval policies"

    def __str__(self):
        return f"{self.get_action_display()}: {'required' if self.required else 'not required'}"


class Approval(models.Model):
    """One granted approval. Single-use, short-lived, bound to an action, requester and object."""

    action = models.CharField(max_length=40)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="approvals_requested")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="approvals_given")
    method = models.CharField(max_length=10)
    reason = models.TextField()
    object_ref = models.CharField(max_length=100, blank=True)
    till_number = models.PositiveSmallIntegerField(null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} approved by {self.approver_id} at {self.created_at:%Y-%m-%d %H:%M}"

    def delete(self, *args, **kwargs):
        raise PermissionError("Approvals cannot be deleted.")
