from django import forms

from .models import BOQItem, Bill


class BillForm(forms.ModelForm):
    class Meta:
        model = Bill
        fields = ["number", "title", "sort_order"]


class BOQItemForm(forms.ModelForm):
    """
    Validates one row of the manual-entry grid (rules 1-3).

    Used from `boq.views.bill_items_save`, once per row in the JSON
    payload the JS grid posts. `boq` is passed in explicitly (rather
    than read off `self.instance.bill.boq`) because a brand-new row has
    no `bill` set on its instance until after validation — so
    item-reference uniqueness (rule 3) is checked against the BOQ the
    view already knows it's working on.
    """

    class Meta:
        model = BOQItem
        fields = [
            "item_reference",
            "description",
            "item_type",
            "unit",
            "quantity",
            "rate",
            "section",
            "parent_item",
            "sort_order",
        ]
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "0.001"}),
            "rate": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, boq=None, **kwargs):
        self.boq = boq
        super().__init__(*args, **kwargs)
        # A blank first option so a wholly-untouched row (item_type="")
        # doesn't fail validation as "this field is required" before
        # the view's own has_changed() check gets a chance to skip it.
        self.fields["item_type"].choices = [("", "---------")] + list(BOQItem.TYPE_CHOICES)
        if boq is not None:
            self.fields["parent_item"].queryset = BOQItem.objects.filter(
                bill__boq=boq, item_type=BOQItem.TYPE_HEADING
            )
            self.fields["section"].queryset = boq.project.sections.all()

    def clean(self):
        cleaned_data = super().clean()
        item_reference = cleaned_data.get("item_reference")
        item_type = cleaned_data.get("item_type")
        quantity = cleaned_data.get("quantity")
        rate = cleaned_data.get("rate")

        if item_reference and self.boq is not None:
            duplicate = (
                BOQItem.objects.filter(bill__boq=self.boq, item_reference=item_reference)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if duplicate:
                self.add_error(
                    "item_reference",
                    "This item reference is already used elsewhere in this BOQ version.",
                )

        if item_type == BOQItem.TYPE_HEADING:
            if quantity or rate:
                self.add_error(None, "A heading cannot have a quantity or rate.")
        elif item_type not in BOQItem.LUMP_SUM_TYPES:
            if quantity in (None, "") or rate in (None, ""):
                self.add_error(None, "Quantity and rate are required for this item type.")

        return cleaned_data
