from django.contrib import admin

from . import audit
from .forms import IntegrationConfigurationForm
from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError

from . import runtime
from .models import AuditLog, IntegrationConfiguration, ReceiptSettings, RuntimeSetting, ShopSettings, SyncReceipt, SyncState


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
        "barcode_lookup_source", "barcode_lookup_api_key", "clear_barcode_lookup_api_key",
        "etims_source", "etims_mode", "etims_integration", "etims_base_url", "etims_branch_id",
        "etims_device_serial", "etims_comm_key", "clear_etims_comm_key", "etims_timeout_seconds",
        "etims_qr_url_template",
    )
    list_display = ("__str__", "mpesa_source", "etims_source", "barcode_lookup_source", "updated_at")

    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            prior = IntegrationConfiguration.objects.get(pk=obj.pk)
            previous = {"mpesa_source": prior.mpesa_source, "etims_source": prior.etims_source,
                        "barcode_lookup_source": prior.barcode_lookup_source}
        super().save_model(request, obj, form, change)
        audit.record(
            "integration.settings_changed", actor=request.user, object_type="integration_settings",
            object_ref="1", previous=previous,
            new={"mpesa_source": obj.mpesa_source, "etims_source": obj.etims_source,
                 "barcode_lookup_source": obj.barcode_lookup_source,
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


class RuntimeSettingForm(forms.ModelForm):
    """One setting. Blank = use the .env / default value. Secrets show as dots; leave blank to keep what is saved."""
    clear_value = forms.BooleanField(required=False, label="Clear (go back to the .env / default value)")

    class Meta:
        model = RuntimeSetting
        fields = ["value"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.defn = runtime.BY_NAME.get(self.instance.name)
        f = self.fields["value"]
        f.required = False
        if self.defn:
            f.help_text = self.defn.help
            if self.defn.secret:
                f.widget = forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"})
                f.help_text += " Leave blank to keep the saved value."
            elif self.defn.kind == "bool":
                f.widget = forms.Select(choices=[("", "(use .env / default)"), ("true", "true"), ("false", "false")])
        if not (self.defn and self.defn.secret):
            self.initial["value"] = self.instance.value

    def clean_value(self):
        raw = (self.cleaned_data.get("value") or "").strip()
        if self.cleaned_data.get("clear_value") or not self.defn:
            return ""
        if not raw:
            return self.instance.value if self.defn.secret else ""
        runtime.parse(self.defn, raw)          # raises ValidationError with a plain message
        try:
            return runtime.encode(self.defn, raw)
        except ImproperlyConfigured:
            raise ValidationError("Secrets are stored encrypted. Put INTEGRATION_SETTINGS_KEY in the .env file first (one time).")


@admin.register(RuntimeSetting)
class RuntimeSettingAdmin(admin.ModelAdmin):
    """Every .env value that can change while the program runs. No adding or deleting: the list is fixed."""
    form = RuntimeSettingForm
    list_display = ("label", "name", "group", "saved", "in_use")
    list_filter = ()
    search_fields = ("name",)
    fields = ("name_ro", "value", "clear_value")
    readonly_fields = ("name_ro",)
    ordering = ("id",)          # rows are created in registry order, so groups stay together

    def get_queryset(self, request):
        runtime.ensure_rows()
        return super().get_queryset(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Setting")
    def label(self, obj):
        d = runtime.BY_NAME.get(obj.name)
        return d.label if d else obj.name

    @admin.display(description="Group")
    def group(self, obj):
        d = runtime.BY_NAME.get(obj.name)
        return d.group if d else ""

    @admin.display(description="Saved in database")
    def saved(self, obj):
        d = runtime.BY_NAME.get(obj.name)
        if not obj.value:
            return "-"
        return "(secret saved)" if d and d.secret else obj.value

    @admin.display(description="In use now")
    def in_use(self, obj):
        d = runtime.BY_NAME.get(obj.name)
        v = runtime.effective(obj.name)
        if d and d.secret:
            return "set" if v else "-"
        return str(v)

    @admin.display(description="Name")
    def name_ro(self, obj):
        d = runtime.BY_NAME.get(obj.name)
        return f"{d.label} ({obj.name})" if d else obj.name

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        runtime.apply(force=True)
        audit.record("settings.server_changed", actor=request.user, object_type="server_setting", object_ref=obj.name,
                     new={"changed": True, "secret": bool(form.defn and form.defn.secret)},
                     reason="Values of secret settings are never copied to the audit log.")


@admin.register(SyncReceipt)
class SyncReceiptAdmin(admin.ModelAdmin):
    list_display = ("received_at", "name", "size", "source_ip", "checked")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SyncState)
class SyncStateAdmin(SingletonAdmin):
    readonly_fields = [f.name for f in SyncState._meta.fields]
