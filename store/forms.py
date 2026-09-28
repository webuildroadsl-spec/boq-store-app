from django import forms
from django.core.exceptions import ValidationError

from .models import GRN, GRNAttachment, GRNLine

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # Section 3: "max 10 MB each."


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
