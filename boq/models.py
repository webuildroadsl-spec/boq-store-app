"""
BOQ module (Section 4): the contract quantities and rates per project,
grouped into bills, with every revision kept.

Steps 3-4 built BOQ, Bill, BOQItem, the amount / bill-total calculation
(rule 1), and Excel import/export. Step 5 adds versions, approval and
variation orders (rules 4, 5, 8 and Section 4.2's "Revise BOQ" /
"Variation orders"): `BOQ.create_revision()` and `BOQ.approve()`, and
the `VariationOrder` model. Material allowances (the BOQ-Store link)
are step 9.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max, Sum
from django.utils import timezone

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
    # Rule 7: "Grand total = sum of bill totals + contingency + tax,
    # each shown separately." These are the "structured tax fields"
    # Company.tax_settings' own help text names this reporting step as
    # needing -- kept per BOQ version (not per project) since a
    # revision can renegotiate either rate without touching an earlier,
    # Approved or Superseded version's own stated total.
    contingency_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

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
        """
        Sum of every bill's total -- unchanged since step 3, and left
        alone deliberately: `VariationOrder.value_impact` and every
        existing test already read this as "sum of bills only," and
        rule 7's contingency/tax belong on top of that sum, not folded
        into it. See `contingency_amount` / `tax_amount` /
        `final_total` below for the rule 7 figure the BOQ summary
        report (step 10) actually shows.
        """
        return sum((bill.total for bill in self.bills.all()), Decimal("0.00"))

    @property
    def contingency_amount(self):
        return (self.grand_total * self.contingency_percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def tax_amount(self):
        """VAT/GST applied to the subtotal plus contingency -- the
        common convention, and a disclosed choice since the spec
        doesn't say which base the tax rate applies to."""
        return ((self.grand_total + self.contingency_amount) * self.tax_percent / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def final_total(self):
        """Rule 7's actual "Grand total": sum of bills + contingency + tax."""
        return self.grand_total + self.contingency_amount + self.tax_amount

    def create_revision(self, revision_type=TYPE_REVISION):
        """
        Section 4.2's "Revise BOQ": "copying an approved BOQ creates a
        new Draft version." Deep-copies every bill and item into a
        brand-new BOQ row (its own version number, one higher than any
        version this project has), so the source version is completely
        untouched — editing the copy can never retroactively change an
        Approved or Superseded version.

        `revision_type` is `TYPE_VARIATION` when this call is backing a
        Variation Order rather than a plain revision; either way the
        copy starts life as Draft, exactly like a hand-built BOQ.
        """
        next_version = (
            self.project.boqs.aggregate(highest=Max("version_number"))["highest"] or 0
        ) + 1
        with transaction.atomic():
            new_boq = BOQ.objects.create(
                project=self.project,
                version_number=next_version,
                type=revision_type,
                status=BOQ.STATUS_DRAFT,
            )
            old_pk_to_new_item = {}
            for bill in self.bills.all():
                new_bill = Bill.objects.create(
                    boq=new_boq, number=bill.number, title=bill.title, sort_order=bill.sort_order
                )
                for item in bill.items.all():
                    new_item = BOQItem.objects.create(
                        bill=new_bill,
                        item_reference=item.item_reference,
                        description=item.description,
                        item_type=item.item_type,
                        unit=item.unit,
                        quantity=item.quantity,
                        rate=item.rate,
                        section=item.section,
                        sort_order=item.sort_order,
                        # parent_item is fixed up in a second pass below,
                        # once every item in this version has a pk of
                        # its own to point to.
                    )
                    old_pk_to_new_item[item.pk] = new_item
            for old_pk, new_item in old_pk_to_new_item.items():
                old_parent_pk = self._original_parent_pk(old_pk)
                if old_parent_pk is not None:
                    new_item.parent_item = old_pk_to_new_item.get(old_parent_pk)
                    new_item.save()
        return new_boq

    def _original_parent_pk(self, old_item_pk):
        return BOQItem.objects.filter(pk=old_item_pk).values_list(
            "parent_item_id", flat=True
        ).first()

    def approve(self, user):
        """
        Rules 4/5/8: approving this (Draft) version marks it Approved,
        records who and when, and supersedes whichever version was
        previously Approved on this project — "only one version per
        project can be Approved at a time." If this version was created
        for a Variation Order, approving it also marks that VO Approved
        (Section 2's "Approve BOQ revision or variation" is one action
        either way).
        """
        if self.status != BOQ.STATUS_DRAFT:
            raise ValidationError("Only a Draft version can be approved.")
        with transaction.atomic():
            BOQ.objects.filter(
                project=self.project, status=BOQ.STATUS_APPROVED
            ).exclude(pk=self.pk).update(status=BOQ.STATUS_SUPERSEDED)
            self.status = BOQ.STATUS_APPROVED
            self.approved_by = user
            self.approved_date = timezone.localdate()
            self.save()
            variation_order = getattr(self, "variation_order", None)
            if variation_order is not None:
                variation_order.status = VariationOrder.STATUS_APPROVED
                variation_order.save()


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
    # Precision follows real contract BOQs, not round numbers: quantities
    # come from take-off formulas (2.1504 ha, 1.03125 t, 26675.706215 kg)
    # and rates are commonly priced to 3 decimals (26.325 per m3). Storing
    # less than that changes the amounts, so the BOQ total would no longer
    # match the signed contract to the cent. Amounts stay at 2 decimals
    # (Rule 1); quantities are still shown to 3 decimals on screen.
    quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    rate = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
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


class VariationOrder(models.Model):
    """
    Section 4.2's "Variation orders": add, omit or change items; each VO
    produces a new BOQ version and shows its value impact. `linked_boq`
    is that new (Draft, then Approved) version, built as a copy of
    `base_boq` via `BOQ.create_revision()` — the QS then edits items on
    it like any other Draft, through the same manual-entry grid.

    `base_boq` is kept as its own field (not just "the previous version
    number") so the value impact stays well-defined even after later
    versions exist: it's always "this VO's version compared with the
    Approved version it was built from," not "whatever the highest
    version number happened to be."
    """

    STATUS_DRAFT = "draft"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="variation_orders")
    number = models.PositiveIntegerField()
    date = models.DateField()
    description = models.CharField(max_length=255)
    reason = models.TextField(blank=True)
    instructed_by = models.CharField(
        max_length=255, blank=True, help_text="e.g. the client's engineer — not necessarily a system user."
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    base_boq = models.ForeignKey(
        BOQ, on_delete=models.PROTECT, related_name="variation_orders_based_on"
    )
    linked_boq = models.OneToOneField(BOQ, on_delete=models.PROTECT, related_name="variation_order")

    class Meta:
        ordering = ["project", "number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_vo_number_per_project")
        ]

    def __str__(self):
        return f"VO {self.number} — {self.description}"

    @property
    def value_impact(self):
        """linked_boq's grand total minus the Approved baseline it was built from."""
        return self.linked_boq.grand_total - self.base_boq.grand_total
