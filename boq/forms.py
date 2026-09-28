from django import forms
from django.forms import inlineformset_factory

from .models import BOQItem, Bill


class BillForm(forms.ModelForm):
    class Meta:
        model = Bill
        fields = ["number", "title", "sort_order"]


class BOQItemForm(forms.ModelForm):
    """
    One row of the manual-entry grid.

    `boq` is passed in explicitly (rather than read off
    `self.instance.bill.boq`) because for a brand-new row in the
    formset, the parent `bill` foreign key isn't set on the instance
    until after validation — so item-reference uniqueness (rule 3) has
    to be checked against the BOQ the view already knows it's working
    on, not against the not-yet-saved instance.
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
        # A blank first option so a genuinely untouched extra grid row
        # renders with nothing selected, matching its blank initial
        # value — without this, browsers default to the first real
        # choice, which would make an empty row look "changed" and
        # trigger required-field errors on rows nobody filled in.
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


BOQItemFormSet = inlineformset_factory(
    Bill,
    BOQItem,
    form=BOQItemForm,
    fk_name="bill",
    extra=3,
    can_delete=True,
)
