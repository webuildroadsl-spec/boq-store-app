from django import forms
from django.core.exceptions import ValidationError

from boq.models import BOQItem

from .models import (
    GRN,
    GRNAttachment,
    GRNLine,
    Issue,
    IssueLine,
    MaterialAllowance,
    RequisitionLine,
    ReturnLine,
    ReturnToStore,
    StockCount,
    StockCountLine,
    StoreRequisition,
    Transfer,
    TransferLine,
)

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # Section 3: "max 10 MB each."


def _boq_items_for_project(project):
    """Every non-heading BOQItem on any BOQ version of this project —
    headings carry no quantity to consume against, so they're never a
    valid "BOQ item" for a requisition/issue/return line."""
    return BOQItem.objects.filter(bill__boq__project=project).exclude(item_type=BOQItem.TYPE_HEADING)


class GRNForm(forms.ModelForm):
    class Meta:
        model = GRN
        fields = ["date", "supplier", "delivery_note_number", "vehicle_number"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class GRNLineForm(forms.ModelForm):
    class Meta:
        model = GRNLine
        fields = ["item", "quantity", "unit_cost"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "0.001"}),
            "unit_cost": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)


class GRNAttachmentForm(forms.ModelForm):
    class Meta:
        model = GRNAttachment
        fields = ["file"]

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_ATTACHMENT_SIZE:
            raise ValidationError("Attachments are limited to 10 MB.")
        return file


class RequisitionForm(forms.ModelForm):
    class Meta:
        model = StoreRequisition
        fields = ["date", "section"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["section"].queryset = project.sections.all()


class RequisitionLineForm(forms.ModelForm):
    class Meta:
        model = RequisitionLine
        fields = ["item", "quantity", "boq_item"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.001"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)
        if project is not None:
            self.fields["boq_item"].queryset = _boq_items_for_project(project)


class IssueForm(forms.ModelForm):
    class Meta:
        model = Issue
        fields = ["date", "requisition", "issued_to"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requisition"].required = False
        if project is not None:
            self.fields["requisition"].queryset = project.requisitions.filter(
                status__in=[StoreRequisition.STATUS_PENDING, StoreRequisition.STATUS_PARTLY_ISSUED]
            )


class IssueLineForm(forms.ModelForm):
    """
    `boq_item` has no blank choice added and is a plain required
    ModelChoiceField — the step 7 acceptance test's "issue without a
    BOQ item is blocked" is enforced by this field simply being
    required, the same way Django blocks saving without any other
    required field.
    """

    class Meta:
        model = IssueLine
        fields = ["item", "quantity", "boq_item", "section"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.001"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)
        self.fields["section"].required = False
        if project is not None:
            self.fields["boq_item"].queryset = _boq_items_for_project(project)
            self.fields["section"].queryset = project.sections.all()


class ReturnForm(forms.ModelForm):
    class Meta:
        model = ReturnToStore
        fields = ["date", "linked_issue"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["linked_issue"].required = False
        if store is not None:
            self.fields["linked_issue"].queryset = store.issues.filter(status=Issue.STATUS_POSTED)


class ReturnLineForm(forms.ModelForm):
    class Meta:
        model = ReturnLine
        fields = ["item", "quantity", "condition", "boq_item"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.001"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)
        self.fields["boq_item"].required = False
        if project is not None:
            self.fields["boq_item"].queryset = _boq_items_for_project(project)


class TransferForm(forms.ModelForm):
    class Meta:
        model = Transfer
        fields = ["date", "from_store", "to_store"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["from_store"].queryset = project.stores.all()
            self.fields["to_store"].queryset = project.stores.all()

    def clean(self):
        cleaned_data = super().clean()
        from_store = cleaned_data.get("from_store")
        to_store = cleaned_data.get("to_store")
        if from_store and to_store and from_store == to_store:
            raise ValidationError("A transfer must be between two different stores.")
        return cleaned_data


class TransferLineForm(forms.ModelForm):
    class Meta:
        model = TransferLine
        fields = ["item", "quantity"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.001"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)


class StockCountForm(forms.ModelForm):
    class Meta:
        model = StockCount
        fields = ["date"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class StockCountLineForm(forms.ModelForm):
    """
    `system_quantity` isn't a form field — it's snapshotted from
    `StockMovement.current_balance()` by the view when the line is
    saved, the same way `IssueLine.section` or `RequisitionLine`'s
    parent FK are set after `form.save(commit=False)` rather than
    exposed for the user to type in.
    """

    class Meta:
        model = StockCountLine
        fields = ["item", "counted_quantity", "reason"]
        widgets = {"counted_quantity": forms.NumberInput(attrs={"step": "0.001"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = self.fields["item"].queryset.filter(active=True)
        self.fields["reason"].required = False


class ReversalForm(forms.Form):
    """A one-field form for the reason behind reversing a Posted GRN,
    issue or return — see `DocumentReversal` (Section 2's "mistakes
    are corrected with a reversing document")."""

    reason = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"size": 60}))


class MaterialAllowanceForm(forms.ModelForm):
    class Meta:
        model = MaterialAllowance
        fields = ["boq_item", "store_item", "quantity_per_unit", "wastage_percent"]
        widgets = {
            "quantity_per_unit": forms.NumberInput(attrs={"step": "0.0001"}),
            "wastage_percent": forms.NumberInput(attrs={"step": "0.01"}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["store_item"].queryset = self.fields["store_item"].queryset.filter(active=True)
        if project is not None:
            self.fields["boq_item"].queryset = _boq_items_for_project(project)
