from django import forms

from . import codes
from .models import Brand, Category, Packaging, Product, ProductCode, Shelf, Supplier
from .services import can_see_cost


class ProductForm(forms.ModelForm):
    """Fields a person may not change are removed from the form, not just disabled."""

    class Meta:
        model = Product
        fields = ["name", "brand", "category", "description", "sku", "base_unit", "buying_cost", "selling_price",
                  "wholesale_price", "min_selling_price", "tax_class", "etims_item_class_code", "reorder_level", "supplier", "shelf", "image", "active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if "tax_class" in self.fields:
            self.fields["tax_class"].required = False      # blank keeps the current class (new products: B)
        self.fields["sku"].required = False
        self.fields["sku"].help_text = "Leave blank to generate one. This code can be scanned at the till."
        for name in ("brand", "category", "supplier", "shelf"):
            self.fields[name].queryset = self.fields[name].queryset.filter(active=True)
            self.fields[name].required = False
        if self.instance.pk:
            self.fields["sku"].disabled = True  # the SKU is also a scannable code; it is never renamed
        if not user.can("products.change_price"):
            for name in ("selling_price", "wholesale_price", "min_selling_price"):
                self.fields.pop(name, None)
        if not user.can("products.change_price"):
            for name in ("tax_class", "etims_item_class_code"):    # tax treatment is a pricing decision
                self.fields.pop(name, None)
        if not user.can("products.change_cost"):
            self.fields.pop("buying_cost", None)
        if not user.can("products.manage"):
            self.fields.clear()

    def clean_tax_class(self):
        return self.cleaned_data.get("tax_class") or (self.instance.tax_class if self.instance.pk else "B")

    def clean_sku(self):
        value = self.cleaned_data.get("sku") or ""
        if self.instance.pk or not value:
            return value
        try:
            return codes.validate_text(value)
        except codes.CodeError as exc:
            raise forms.ValidationError(str(exc))

    def clean(self):
        data = super().clean()
        selling, minimum, wholesale = data.get("selling_price"), data.get("min_selling_price"), data.get("wholesale_price")
        if selling is not None and minimum is not None and minimum > selling:
            self.add_error("min_selling_price", "The minimum price cannot be above the selling price.")
        if selling is not None and wholesale is not None and wholesale > selling:
            self.add_error("wholesale_price", "The wholesale price cannot be above the selling price.")
        return data


class PackagingForm(forms.ModelForm):
    class Meta:
        model = Packaging
        fields = ["name", "units_per_pack", "selling_price"]

    def __init__(self, *args, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.instance.product = product

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Packaging.objects.filter(product=self.product, name__iexact=name).exists():
            raise forms.ValidationError("This product already has a pack size with that name.")
        return name


class CodeForm(forms.Form):
    code = forms.CharField(max_length=40, label="Barcode or code", help_text="Scan the barcode here, or type a code.")
    kind = forms.ChoiceField(choices=[(ProductCode.KIND_BARCODE, "Manufacturer barcode"),
                                      (ProductCode.KIND_MANUAL, "Manual code")], initial=ProductCode.KIND_BARCODE)
    packaging = forms.ModelChoiceField(queryset=Packaging.objects.none(), required=False, label="Applies to pack",
                                       help_text="Leave blank if this code is for a single unit.")

    def __init__(self, *args, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["packaging"].queryset = product.packagings.filter(active=True)


class NamedForm(forms.ModelForm):
    class Meta:
        fields = ["name"]


class CategoryForm(NamedForm):
    class Meta(NamedForm.Meta):
        model = Category


class BrandForm(NamedForm):
    class Meta(NamedForm.Meta):
        model = Brand


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "kra_pin", "notes", "active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class ShelfForm(forms.ModelForm):
    class Meta:
        model = Shelf
        fields = ["code", "name", "notes", "active"]

    def clean_code(self):
        code = codes.normalize(self.cleaned_data["code"])
        clash = Shelf.objects.filter(code=code).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A shelf with this code already exists.")
        return code
