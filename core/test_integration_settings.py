from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, TestCase, override_settings

from core import integration_settings
from core.forms import IntegrationConfigurationForm
from core.models import AuditLog, IntegrationConfiguration


@override_settings(INTEGRATION_SETTINGS_KEY="test-only-external-encryption-key")
class IntegrationSettingsTests(TestCase):
    @staticmethod
    def form_data(form):
        return {
            name: form[name].value()
            for name in form.fields
            if not name.startswith("clear_") and form[name].value() is not None
        }

    def database_configuration(self):
        mpesa = integration_settings.environment_mpesa()
        mpesa.update({
            "CALLBACK_BASE_URL": "https://shop.example.test",
            "CALLBACK_SECRET": "db-callback-secret-" + "x" * 32,
            "CONSUMER_SECRET": "db-consumer-secret",
        })
        etims = integration_settings.environment_etims()
        etims.update({"MODE": "sandbox", "BASE_URL": "http://127.0.0.1:8088", "COMM_KEY": "db-etims-key"})
        config = IntegrationConfiguration(
            mpesa_source=IntegrationConfiguration.SOURCE_DATABASE,
            etims_source=IntegrationConfiguration.SOURCE_DATABASE,
        )
        config.set_mpesa_values(mpesa)
        config.set_etims_values(etims)
        config.save()
        return config, mpesa, etims

    def test_database_source_uses_encrypted_values_without_environment_fallback(self):
        config, mpesa, etims = self.database_configuration()

        self.assertEqual(integration_settings.mpesa(), mpesa)
        self.assertEqual(integration_settings.etims(), etims)
        self.assertNotIn("db-consumer-secret", config.mpesa_config_ciphertext)
        self.assertNotIn("db-etims-key", config.etims_config_ciphertext)

    def test_wrong_external_key_fails_closed(self):
        self.database_configuration()
        with override_settings(INTEGRATION_SETTINGS_KEY="different-external-key"):
            with self.assertRaisesMessage(ImproperlyConfigured, "Check the external INTEGRATION_SETTINGS_KEY"):
                integration_settings.mpesa()

    def test_manager_can_recover_to_environment_source_after_key_mismatch(self):
        config, _mpesa, _etims = self.database_configuration()
        with override_settings(INTEGRATION_SETTINGS_KEY="different-external-key"):
            form = IntegrationConfigurationForm(instance=config)
            data = self.form_data(form)
            data.update({
                "mpesa_source": IntegrationConfiguration.SOURCE_ENV,
                "etims_source": IntegrationConfiguration.SOURCE_ENV,
            })
            recovery = IntegrationConfigurationForm(data=data, instance=config)
            self.assertTrue(recovery.is_valid(), recovery.errors)
            recovered = recovery.save()

        self.assertEqual(recovered.mpesa_config_ciphertext, "")
        self.assertEqual(recovered.etims_config_ciphertext, "")
        self.assertEqual(integration_settings.mpesa(), integration_settings.environment_mpesa())

    def test_masked_secret_fields_preserve_saved_values_when_blank(self):
        config, mpesa, _etims = self.database_configuration()
        form = IntegrationConfigurationForm(instance=config)
        self.assertNotIn("db-consumer-secret", str(form))
        data = self.form_data(form)
        data["mpesa_source"] = IntegrationConfiguration.SOURCE_DATABASE

        updated = IntegrationConfigurationForm(data=data, instance=config)
        self.assertTrue(updated.is_valid(), updated.errors)
        updated.save()

        self.assertEqual(integration_settings.mpesa()["CONSUMER_SECRET"], mpesa["CONSUMER_SECRET"])

    def test_database_source_requires_callback_configuration(self):
        form = IntegrationConfigurationForm()
        data = self.form_data(form)
        data.update({
            "mpesa_source": IntegrationConfiguration.SOURCE_DATABASE,
            "mpesa_callback_base_url": "",
            "mpesa_callback_secret": "",
        })
        invalid = IntegrationConfigurationForm(data=data)
        self.assertFalse(invalid.is_valid())
        self.assertIn("mpesa_callback_base_url", invalid.errors)

    def test_manager_can_switch_source_and_audit_never_contains_secrets(self):
        config, _mpesa, _etims = self.database_configuration()
        config.mpesa_source = IntegrationConfiguration.SOURCE_ENV
        config.save()
        manager = get_user_model().objects.create_user(username="settings-manager", password="Strong-test-pass-1")
        manager.is_manager = True
        manager.save()
        client = Client()
        client.force_login(manager)
        url = "/admin/core/integrationconfiguration/1/change/"

        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "db-consumer-secret")
        form = IntegrationConfigurationForm(instance=config)
        data = self.form_data(form)
        data["mpesa_source"] = IntegrationConfiguration.SOURCE_DATABASE
        response = client.post(url, data)

        self.assertEqual(response.status_code, 302)
        audit = AuditLog.objects.get(action="integration.settings_changed")
        self.assertEqual(audit.new_value["mpesa_source"], IntegrationConfiguration.SOURCE_DATABASE)
        self.assertNotIn("db-consumer-secret", str(audit.new_value))

    def test_non_manager_cannot_open_admin_settings(self):
        cashier = get_user_model().objects.create_user(username="settings-cashier", password="Strong-test-pass-2")
        client = Client()
        client.force_login(cashier)
        response = client.get("/admin/core/integrationconfiguration/")
        self.assertEqual(response.status_code, 302)
