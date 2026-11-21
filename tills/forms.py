from django import forms

from .local_servers import normalize_local_server_urls


class TillLocalServersForm(forms.Form):
    local_server_urls = forms.CharField(
        required=False,
        label="Local shop server URLs",
        help_text="Enter one private LAN URL per line, for example http://192.168.1.10:8000.",
        widget=forms.Textarea(attrs={"rows": 3, "autocomplete": "off"}),
    )

    def clean_local_server_urls(self):
        return normalize_local_server_urls(self.cleaned_data["local_server_urls"])
