from django import forms
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from urllib.parse import urlsplit

from . import integration_settings
from .models import IntegrationConfiguration


MPESA_FIELDS = {
    "ENV": "mpesa_env",
    "CONSUMER_KEY": "mpesa_consumer_key",
    "CONSUMER_SECRET": "mpesa_consumer_secret",
    "SHORTCODE": "mpesa_shortcode",
    "TILL_NUMBER": "mpesa_till_number",
    "PASSKEY": "mpesa_passkey",
    "ACCOUNT_TYPE": "mpesa_account_type",
    "CALLBACK_BASE_URL": "mpesa_callback_base_url",
    "CALLBACK_SECRET": "mpesa_callback_secret",
    "TIMEOUT": "mpesa_timeout",
    "RELAY_URL": "mpesa_relay_url",
    "RELAY_TOKEN": "mpesa_relay_token",
    "INITIATOR_NAME": "mpesa_initiator_name",
    "SECURITY_CREDENTIAL": "mpesa_security_credential",
}
ETIMS_FIELDS = {
    "MODE": "etims_mode",
    "INTEGRATION": "etims_integration",
    "BASE_URL": "etims_base_url",
    "BRANCH_ID": "etims_branch_id",
    "DEVICE_SERIAL": "etims_device_serial",
    "COMM_KEY": "etims_comm_key",
    "TIMEOUT_SECONDS": "etims_timeout_seconds",
    "QR_URL_TEMPLATE": "etims_qr_url_template",
}
SECRET_FIELDS = {
    "mpesa_consumer_key": "CONSUMER_KEY",
    "mpesa_consumer_secret": "CONSUMER_SECRET",
    "mpesa_passkey": "PASSKEY",
    "mpesa_callback_secret": "CALLBACK_SECRET",
    "mpesa_relay_token": "RELAY_TOKEN",
    "mpesa_security_credential": "SECURITY_CREDENTIAL",
    "etims_comm_key": "COMM_KEY",
}


class IntegrationConfigurationForm(forms.ModelForm):
    mpesa_env = forms.ChoiceField(choices=[("sandbox", "Sandbox"), ("live", "Live")])
    mpesa_consumer_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    mpesa_consumer_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    mpesa_shortcode = forms.CharField(required=False, max_length=40)
    mpesa_till_number = forms.CharField(required=False, max_length=40)
    mpesa_passkey = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    mpesa_account_type = forms.ChoiceField(choices=[("till", "Buy Goods till"), ("paybill", "Paybill")])
    mpesa_callback_base_url = forms.URLField(required=False, max_length=250)
    mpesa_callback_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    mpesa_timeout = forms.IntegerField(min_value=1, max_value=120)
    mpesa_relay_url = forms.URLField(required=False, max_length=250)
    mpesa_relay_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    mpesa_initiator_name = forms.CharField(required=False, max_length=100)
    mpesa_security_credential = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    etims_mode = forms.ChoiceField(choices=[("off", "Off"), ("sandbox", "Sandbox"), ("live", "Live")])
    etims_integration = forms.ChoiceField(choices=[("vscu", "VSCU"), ("oscu", "OSCU")])
    etims_base_url = forms.URLField(required=False, max_length=250)
    etims_branch_id = forms.CharField(max_length=8)
    etims_device_serial = forms.CharField(required=False, max_length=100)
    etims_comm_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    etims_timeout_seconds = forms.IntegerField(min_value=1, max_value=120)
    etims_qr_url_template = forms.CharField(required=False, max_length=500)

    clear_mpesa_consumer_key = forms.BooleanField(required=False)
    clear_mpesa_consumer_secret = forms.BooleanField(required=False)
    clear_mpesa_passkey = forms.BooleanField(required=False)
    clear_mpesa_callback_secret = forms.BooleanField(required=False)
    clear_mpesa_relay_token = forms.BooleanField(required=False)
    clear_mpesa_security_credential = forms.BooleanField(required=False)
    clear_etims_comm_key = forms.BooleanField(required=False)

    class Meta:
        model = IntegrationConfiguration
        fields = ["mpesa_source", "etims_source"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mpesa_source"].help_text = (
            "Select whether all M-PESA settings come from the external environment or encrypted database values.")
        self.fields["etims_source"].help_text = (
            "Select whether all eTIMS settings come from the external environment or encrypted database values.")
        mpesa = integration_settings.environment_mpesa()
        etims = integration_settings.environment_etims()
        stored_mpesa = {}
        stored_etims = {}
        self._decryption_error = None
        if self.instance.pk:
            try:
                if self.instance.mpesa_config_ciphertext:
                    stored_mpesa = self.instance.mpesa_values()
                    mpesa.update(stored_mpesa)
                if self.instance.etims_config_ciphertext:
                    stored_etims = self.instance.etims_values()
                    etims.update(stored_etims)
            except ImproperlyConfigured as exc:
                self._decryption_error = str(exc)
        self._existing_mpesa = stored_mpesa if not self._decryption_error else {}
        self._existing_etims = stored_etims if not self._decryption_error else {}
        for key, field in MPESA_FIELDS.items():
            if field in SECRET_FIELDS:
                self.fields[field].initial = ""
                self.fields[field].help_text = "Leave blank to keep the saved value; use Clear to remove it."
            else:
                self.fields[field].initial = mpesa[key]
        for key, field in ETIMS_FIELDS.items():
            if field in SECRET_FIELDS:
                self.fields[field].initial = ""
                self.fields[field].help_text = "Leave blank to keep the saved value; use Clear to remove it."
            else:
                self.fields[field].initial = etims[key]

    def clean(self):
        cleaned = super().clean()
        database_source = (
            cleaned.get("mpesa_source") == IntegrationConfiguration.SOURCE_DATABASE
            or cleaned.get("etims_source") == IntegrationConfiguration.SOURCE_DATABASE
        )
        if database_source and not settings.INTEGRATION_SETTINGS_KEY:
            raise forms.ValidationError(
                "Set the external INTEGRATION_SETTINGS_KEY before saving integration settings.")
        if database_source and self._decryption_error:
            raise forms.ValidationError(
                "Saved database values cannot be decrypted. Restore the original key or switch both integrations "
                "to environment source before replacing the values.")
        saved_callback_secret = self._existing_mpesa.get("CALLBACK_SECRET", "")
        if cleaned.get("clear_mpesa_callback_secret"):
            saved_callback_secret = ""
        callback_secret = cleaned.get("mpesa_callback_secret") or saved_callback_secret
        callback_url = cleaned.get("mpesa_callback_base_url", "")
        if cleaned.get("mpesa_source") == IntegrationConfiguration.SOURCE_DATABASE:
            if not callback_url or urlsplit(callback_url).scheme != "https":
                self.add_error("mpesa_callback_base_url", "Database-source M-PESA requires an HTTPS callback URL.")
            if len(callback_secret) < 32:
                self.add_error("mpesa_callback_secret", "Use a callback secret of at least 32 characters.")
        relay_url = cleaned.get("mpesa_relay_url", "")
        if relay_url and urlsplit(relay_url).scheme != "https":
            self.add_error("mpesa_relay_url", "The M-PESA relay URL must use HTTPS.")
        saved_comm_key = self._existing_etims.get("COMM_KEY", "")
        if cleaned.get("clear_etims_comm_key"):
            saved_comm_key = ""
        if (cleaned.get("etims_mode") in ("sandbox", "live") and
                cleaned.get("etims_source") == IntegrationConfiguration.SOURCE_DATABASE):
            if not cleaned.get("etims_base_url"):
                self.add_error("etims_base_url", "Set the eTIMS unit URL before enabling it.")
            if (cleaned.get("etims_integration") == "oscu" and
                    not (cleaned.get("etims_comm_key") or saved_comm_key)):
                self.add_error("etims_comm_key", "OSCU requires its communication key.")
        return cleaned

    def _values(self, fields, old_values):
        values = {}
        for key, field in fields.items():
            if field not in SECRET_FIELDS:
                values[key] = self.cleaned_data[field]
            elif self.cleaned_data.get(f"clear_{field}"):
                values[key] = ""
            else:
                values[key] = self.cleaned_data.get(field) or old_values.get(key, "")
        return values

    def save(self, commit=True):
        instance = super().save(commit=False)
        if settings.INTEGRATION_SETTINGS_KEY and not self._decryption_error:
            instance.set_mpesa_values(self._values(MPESA_FIELDS, self._existing_mpesa))
            instance.set_etims_values(self._values(ETIMS_FIELDS, self._existing_etims))
        else:
            instance.mpesa_config_ciphertext = ""
            instance.etims_config_ciphertext = ""
        if commit:
            instance.save()
        return instance
