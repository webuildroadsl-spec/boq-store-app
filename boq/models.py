"""
BOQ module (Section 4): the contract quantities and rates per project,
grouped into bills, with every revision kept.

Only what step 3 needs is built here: BOQ, Bill, BOQItem, and the
amount / bill-total calculation (rule 1). Versions, approval, and
variation orders (rules 4, 5, 8 and Section 4.2's "Revise BOQ" /
"Variation orders") are step 5's work — the `type` and `status` fields
exist now because they're part of the BOQ table itself, but nothing yet
enforces "only one Approved version per project" or supersedes an old
version. Material allowances (the BOQ–Store link) are step 9.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from core.models import Project, Section, UnitOfMeasure


class BOQ(models.Model):
    TYPE_ORIGINAL = "original"
    TYPE_REVISION = "revision"
    TYPE_VARIATION = "variation"
    TYPE_CHOICES = [
        (TYPE_ORIGINAL, "Original"),
        (TYPE_REVISION, "Revision"),
        (TYPE_VARIATION, "Variation"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_SUPERSEDED, "Superseded"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="boqs")
    version_number = models.PositiveIntegerField()
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_ORIGINAL)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["project", "version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "version_number"], name="unique_boq_version_per_project"
            )
        ]

    def __str__(self):
        return f"{self.project.code} v{self.version_number} ({self.get_type_display()})"

    @property
    def is_editable(self):
        """Rule 4: only Draft versions can be edited."""
        return self.status == self.STATUS_DRAFT

    @property
    def grand_total(self):
        """Sum of every bill's total. Contingency and tax (rule 7) are
        added in the Section 7 reporting step, once those rates have a
        place to live."""
        return sum((bill.total for bill in self.bills.all()), Decimal("0.00"))


class Bill(models.Model):
    boq = models.ForeignKey(BOQ, on_delete=models.CASCADE, related_name="bills")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["boq", "sort_order", "number"]
        constraints = [
            models.UniqueConstraint(fields=["boq", "number"], name="unique_bill_number_per_boq")
        ]

    def __str__(self):
        return f"Bill {self.number}: {self.title}"

    @property
    def total(self):
        """Rule 1's amounts, summed. Headings contribute nothing (they
        have no amount)."""
        result = self.items.exclude(item_type=BOQItem.TYPE_HEADING).aggregate(
            total=Sum("amount")
        )
        return result["total"] or Decimal("0.00")


class BOQItem(models.Model):
    TYPE_MEASURED = "measured"
    TYPE_PROVISIONAL_SUM = "provisional_sum"
    TYPE_PRIME_COST = "prime_cost"
    TYPE_LUMP_SUM = "lump_sum"
    TYPE_DAYWORK = "daywork"
    TYPE_HEADING = "heading"

    TYPE_CHOICES = [
        (TYPE_MEASURED, "Measured"),
        (TYPE_PROVISIONAL_SUM, "Provisional Sum"),
        (TYPE_PRIME_COST, "Prime Cost"),
        (TYPE_LUMP_SUM, "Lump Sum"),
        (TYPE_DAYWORK, "Daywork"),
        (TYPE_HEADING, "Heading"),
    ]

    # Rule 2: these two item types are entered as a single amount
    # (quantity forced to 1, unit forced to "sum") rather than a
    # quantity/rate pair.
    LUMP_SUM_TYPES = {TYPE_LUMP_SUM, TYPE_PROVISIONAL_SUM}

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    item_reference = models.CharField(max_length=20, help_text="e.g. 4.02")
    description = models.CharField(max_length=500)
    # No default: the manual-entry grid gives every row a blank "type"
    # cell that the user must actively choose, rather than silently
    # defaulting to Measured (and this also keeps a genuinely untouched
    # extra grid row detected as "unchanged" by Django's formsets — a
    # default value here would make blank rows look edited).
    item_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    unit = models.ForeignKey(
        UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True, editable=False
    )
    section = models.ForeignKey(
        Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="boq_items"
    )
    # Rule: "parent item (for headings)" — a leaf item can sit under a
    # heading row for display/grouping.
    parent_item = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["bill", "sort_order", "pk"]

    def __str__(self):
        return f"{self.item_reference} — {self.description}"

    def clean(self):
        super().clean()
        if self.item_type == self.TYPE_HEADING:
            if self.quantity or self.rate:
                raise ValidationError("A heading cannot have a quantity or rate.")

    def save(self, *args, **kwargs):
        # Rule 1: headings have no quantity, rate or amount.
        if self.item_type == self.TYPE_HEADING:
            self.quantity = None
            self.rate = None
            self.amount = None
        else:
            # Rule 2: lump sum / provisional sum items are one amount,
            # entered as rate with quantity forced to 1 and unit "sum".
            if self.item_type in self.LUMP_SUM_TYPES:
                self.quantity = Decimal("1")
                sum_unit = UnitOfMeasure.objects.filter(code="sum").first()
                if sum_unit is not None:
                    self.unit = sum_unit
            # Rule 1: amount = quantity x rate, rounded to 2 decimals.
            if self.quantity is not None and self.rate is not None:
                self.amount = (self.quantity * self.rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                self.amount = None
        super().save(*args, **kwargs)
