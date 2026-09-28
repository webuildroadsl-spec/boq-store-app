"""
Store module (Section 5): every material movement goes through a
posted document, and the stock balance is always calculated from those
movements — never typed in or stored as a running total anywhere.

Step 6 builds the master data (Item category, Store, Store item,
Supplier) and the first posted document, GRN (goods received).
Requisition, Issue, Transfer, Return and Stock count (Section 5.2's
other document types) are steps 7-8. Material allowances (the BOQ-
Store link) are step 9.

Item category, Store item and Supplier are managed through /admin/,
the same way Company/UnitOfMeasure/ProjectMembership already are (see
the BOQ module's README section) — they're master/setup data, not
something the spec asks for a bespoke screen to create.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from core.models import Project, Section, UnitOfMeasure


class ItemCategory(models.Model):
    """Section 5.1: e.g. Aggregates, Cement and Binders, Bitumen, Steel,
    Pipes and Culverts, Fuel and Lubricants, Road Furniture, Consumables."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "item categories"

    def __str__(self):
        return self.name


class Store(models.Model):
    TYPE_MAIN_YARD = "main_yard"
    TYPE_SITE_STORE = "site_store"
    TYPE_FUEL_DEPOT = "fuel_depot"
    TYPE_CHOICES = [
        (TYPE_MAIN_YARD, "Main yard"),
        (TYPE_SITE_STORE, "Site store"),
        (TYPE_FUEL_DEPOT, "Fuel depot"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="stores")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    # Section 5.1 lists a single "storekeeper" per store. Section 2's
    # per-project Storekeeper *role* still gates who can be assigned
    # here at all (see store.permissions.can_manage_grn) -- this field
    # is "which one, of possibly several, runs this particular store."
    storekeeper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stores_managed"
    )
    type = models.CharField(max_length=15, choices=TYPE_CHOICES, default=TYPE_SITE_STORE)

    class Meta:
        ordering = ["project", "code"]
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="unique_store_code_per_project")
        ]

    def __str__(self):
        return f"{self.project.code} / {self.code} — {self.name}"


class StoreItem(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    minimum_stock_level = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    reorder_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    is_fuel = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StockMovement(models.Model):
    """
    The single source of truth for stock on hand: no model anywhere
    stores a running balance. `quantity` is signed (+ in / - out), and
    `unit_cost` / `total_cost` are always *this movement's own* cost —
    which is what makes a weighted-average balance just
    sum(total_cost) / sum(quantity) over every movement for a given
    store+item (see `current_balance()`). An issue's unit_cost will be
    the average cost *at the moment it's posted* (computed then, fixed
    afterward) once step 7 adds issues, so that sum keeps working even
    after stock leaves as well as arrives.

    `document_type` + `document_id` name the posted document that
    created this movement, spelled out as plain fields (not a
    GenericForeignKey) because that's literally how Section 5.1 lists
    them ("document type, document ID") — the BOQ item/section links
    are Section 5.2's "issued against a requisition (or directly, with
    a BOQ item)" and step 9's material-allowance reconciliation.
    """

    DOCUMENT_GRN = "grn"
    DOCUMENT_CHOICES = [
        (DOCUMENT_GRN, "GRN"),
        # issue / transfer / return / adjustment join this list in steps 7-8.
    ]

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="movements")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="movements")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)
    total_cost = models.DecimalField(max_digits=16, decimal_places=2, editable=False)
    document_type = models.CharField(max_length=15, choices=DOCUMENT_CHOICES)
    document_id = models.PositiveIntegerField()
    boq_item = models.ForeignKey(
        "boq.BOQItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    section = models.ForeignKey(
        Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["store", "item", "created_at", "pk"]

    def __str__(self):
        return f"{self.store.code}/{self.item.code}: {self.quantity:+} @ {self.unit_cost}"

    def save(self, *args, **kwargs):
        self.total_cost = (self.quantity * self.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)

    @staticmethod
    def current_balance(store, item):
        """
        (quantity, value, average_unit_cost) for this store+item, from
        every movement posted so far. The step 6 acceptance test (a
        200-bag GRN at 150.00 showing stock 200, value 30,000.00) and
        the weighted-average test in Section 8 (100@150.00 then
        100@170.00 -> average 160.00; issuing 50 leaves value
        24,000.00) are both exactly this formula.
        """
        result = StockMovement.objects.filter(store=store, item=item).aggregate(
            qty=models.Sum("quantity"), value=models.Sum("total_cost")
        )
        quantity = result["qty"] or Decimal("0.000")
        value = result["value"] or Decimal("0.00")
        average_unit_cost = (
            (value / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if quantity else Decimal("0.00")
        )
        return quantity, value, average_unit_cost


class GRN(models.Model):
    """
    Section 5.2 point 2: "the Storekeeper records a delivery. Posting
    adds stock at the supplier's unit cost." Recording (this model plus
    its lines) and posting (`post()`, which is what actually creates
    the StockMovement rows) are deliberately two steps — a GRN can be
    built up as a Draft first and corrected freely, but once Posted it
    follows Section 2's rule: "Posted documents ... cannot be edited or
    deleted; mistakes are corrected with a reversing document." (There
    is no reversing-document type yet -- issues, transfers and
    adjustments don't exist until steps 7-8 -- so for now a posting
    mistake has no in-app fix; this is disclosed in the README.)
    """

    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_POSTED, "Posted")]

    number = models.PositiveIntegerField()
    date = models.DateField()
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="grns")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="grns")
    delivery_note_number = models.CharField(max_length=100, blank=True)
    vehicle_number = models.CharField(max_length=50, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        ordering = ["store", "number"]
        constraints = [
            models.UniqueConstraint(fields=["store", "number"], name="unique_grn_number_per_store")
        ]

    def __str__(self):
        return f"GRN {self.number} — {self.store.code}"

    @property
    def is_editable(self):
        return self.status == self.STATUS_DRAFT

    @property
    def total_value(self):
        return sum((line.total_cost for line in self.lines.all()), Decimal("0.00"))

    def post(self, user):
        """Creates one StockMovement per line, at that line's own unit
        cost, and locks the GRN. All-or-nothing, same reasoning as the
        BOQ Excel import: either every line posts or none does."""
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a Draft GRN can be posted.")
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError("A GRN needs at least one line before it can be posted.")
        with transaction.atomic():
            for line in lines:
                StockMovement.objects.create(
                    store=self.store,
                    item=line.item,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    document_type=StockMovement.DOCUMENT_GRN,
                    document_id=self.pk,
                    created_by=user,
                )
            self.status = self.STATUS_POSTED
            self.save()


class GRNLine(models.Model):
    grn = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code} x{self.quantity} @ {self.unit_cost}"

    @property
    def total_cost(self):
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class GRNAttachment(models.Model):
    """Section 3's "any record can hold files ... max 10 MB each,
    stored off the database" -- enforced in the form, not here, since
    Django's FileField itself has no size cap."""

    grn = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="grn_attachments/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.file.name
