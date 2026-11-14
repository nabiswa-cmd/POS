import uuid
from decimal import Decimal

from django import forms
from django.db.models import Q

from catalog import services as catalog_services
from catalog.models import Packaging, Product, Supplier

from . import movement_rules as mr
from .models import Receiving


class AdjustForm(forms.Form):
    movement_type = forms.ChoiceField(choices=[(t, mr.LABELS[t]) for t in mr.MANUAL_TYPES], label="What happened")
    quantity = forms.IntegerField(
        label="Quantity (in single units)",
        help_text="Damaged, expired and supplier returns remove stock. Opening stock adds. "
                  "For a correction use a plus or minus number (-3 removes three).")
    reason = forms.CharField(max_length=250, label="Reason")
    reference = forms.CharField(max_length=60, required=False, label="Reference (optional)")
    approver_username = forms.CharField(max_length=150, required=False, label="Approver username")
    approver_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False),
                                      label="Approver password or PIN")

    def clean(self):
        data = super().clean()
        kind, qty = data.get("movement_type"), data.get("quantity")
        if kind and qty is not None:
            if qty == 0:
                self.add_error("quantity", "Quantity cannot be zero.")
            elif kind in mr.NEGATIVE_ONLY:
                data["quantity"] = -abs(qty)
            elif kind in mr.POSITIVE_ONLY:
                data["quantity"] = abs(qty)
        return data


class _VolumeOperationForm(forms.Form):
    """Shared by the volume wastage and bottle adjustment screens.

    `operation_key` is generated when the form is first shown. Submitting the same form twice (double click,
    browser refresh) therefore reaches the service with the same key and is recorded only once.
    """

    reason = forms.CharField(max_length=250, label="Reason")
    reference = forms.CharField(max_length=60, required=False, label="Reference (optional)")
    operation_key = forms.CharField(max_length=80, widget=forms.HiddenInput, initial=lambda: uuid.uuid4().hex)
    approver_username = forms.CharField(max_length=150, required=False, label="Approver username")
    approver_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False),
                                      label="Approver password or PIN")


class WastageForm(_VolumeOperationForm):
    volume_ml = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                   label="Liquid lost (ml)",
                                   help_text="Taken from opened bottles, oldest first. Sealed bottles are never touched.")
    field_order = ["volume_ml", "reason", "reference"]


class BottleAdjustForm(_VolumeOperationForm):
    volume_ml = forms.DecimalField(max_digits=10, decimal_places=2, label="Change in remaining liquid (ml)",
                                   help_text="Plus adds liquid, minus removes it (-25 removes 25 ml). "
                                             "The result must stay between empty and the bottle's capacity.")
    field_order = ["volume_ml", "reason", "reference"]

    def clean_volume_ml(self):
        value = self.cleaned_data["volume_ml"]
        if value == 0:
            raise forms.ValidationError("The change cannot be zero.")
        return value


class ReceivingForm(forms.ModelForm):
    class Meta:
        model = Receiving
        fields = ["supplier", "delivery_note", "invoice_ref", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(active=True)
        self.fields["supplier"].required = False
        self.fields["supplier"].help_text = "Optional."


class ReceivingItemForm(forms.Form):
    """One delivery line. `code` takes a scanned barcode/QR/SKU, or enough of the name to find one product."""

    code = forms.CharField(max_length=120, label="Scan code, or type product name")
    pack = forms.CharField(max_length=40, required=False, label="Pack size (blank = single units)",
                           help_text="Type the pack name, e.g. Carton. A scanned carton barcode fills this in.")
    qty_good = forms.IntegerField(min_value=0, initial=0, label="Received OK")
    qty_damaged = forms.IntegerField(min_value=0, initial=0, required=False, label="Damaged")
    qty_missing = forms.IntegerField(min_value=0, initial=0, required=False, label="Missing")
    cost_per_pack = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2, required=False,
                                       label="Cost per pack (from invoice)",
                                       help_text="Leave blank to use the product's expected cost.")

    def clean(self):
        data = super().clean()
        text = (data.get("code") or "").strip()
        if not text:
            return data
        hit = catalog_services.find_by_code(text)
        product = packaging = None
        if hit:
            product, packaging = hit.product, hit.packaging
        else:
            matches = list(Product.objects.filter(active=True).filter(
                Q(name__icontains=text) | Q(brand__name__icontains=text)).select_related("brand")[:6])
            if len(matches) == 1:
                product = matches[0]
            elif not matches:
                self.add_error("code", "No active product matches that code or name.")
            else:
                names = "; ".join(f"{p.name} ({p.sku})" for p in matches[:5])
                self.add_error("code", f"More than one product matches. Scan the barcode or type the SKU. Matches: {names}")
        if product is not None:
            pack_text = (data.get("pack") or "").strip()
            if packaging is None and pack_text:
                packaging = Packaging.objects.filter(product=product, active=True, name__iexact=pack_text).first()
                if packaging is None:
                    self.add_error("pack", f"{product.name} has no active pack size called '{pack_text}'.")
            data["product"], data["packaging"] = product, packaging
        for key in ("qty_damaged", "qty_missing"):
            if data.get(key) is None:
                data[key] = 0
        return data
