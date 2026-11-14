from django.contrib import admin

from . import audit
from .forms import IntegrationConfigurationForm
from .models import AuditLog, IntegrationConfiguration, ReceiptSettings, ShopSettings


class SingletonAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ShopSettings)
class ShopSettingsAdmin(SingletonAdmin):
    def get_readonly_fields(self, request, obj=None):
        # Costing method may only change while no sale has locked it.
        return ["costing_locked"] + (["costing_method"] if obj and obj.costing_locked else [])


@admin.register(ReceiptSettings)
class ReceiptSettingsAdmin(SingletonAdmin):
    pass


@admin.register(IntegrationConfiguration)
class IntegrationConfigurationAdmin(SingletonAdmin):
    form = IntegrationConfigurationForm
    fields = (
        "mpesa_source", "mpesa_env", "mpesa_account_type", "mpesa_shortcode", "mpesa_till_number",
        "mpesa_consumer_key", "clear_mpesa_consumer_key", "mpesa_consumer_secret",
        "clear_mpesa_consumer_secret", "mpesa_passkey", "clear_mpesa_passkey",
        "mpesa_callback_base_url", "mpesa_callback_secret", "clear_mpesa_callback_secret",
        "mpesa_timeout", "mpesa_relay_url", "mpesa_relay_token", "clear_mpesa_relay_token",
        "mpesa_initiator_name", "mpesa_security_credential", "clear_mpesa_security_credential",
        "etims_source", "etims_mode", "etims_integration", "etims_base_url", "etims_branch_id",
        "etims_device_serial", "etims_comm_key", "clear_etims_comm_key", "etims_timeout_seconds",
        "etims_qr_url_template",
    )
    list_display = ("__str__", "mpesa_source", "etims_source", "updated_at")

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            prior = IntegrationConfiguration.objects.get(pk=obj.pk)
            previous = {"mpesa_source": prior.mpesa_source, "etims_source": prior.etims_source}
        super().save_model(request, obj, form, change)
        audit.record(
            "integration.settings_changed", actor=request.user, object_type="integration_settings",
            object_ref="1", previous=previous,
            new={"mpesa_source": obj.mpesa_source, "etims_source": obj.etims_source,
                 "changed_fields": [name for name in form.changed_data
                                   if not name.startswith("clear_")]},
            reason="Sensitive values are encrypted and never copied to the audit log.",
        )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "approver", "object_type", "object_ref", "till_number")
    list_filter = ("action",)
    search_fields = ("action", "object_ref", "reason", "actor__username", "approver__username")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
