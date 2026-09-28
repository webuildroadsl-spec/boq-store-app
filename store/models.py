"""
Store module (Section 5): every material movement goes through a
posted document, and the stock balance is always calculated from those
movements — never typed in or stored as a running total anywhere.

Step 6 built the master data (Item category, Store, Store item,
Supplier) and the first posted document, GRN (goods received). Step 7
added Requisition, Issue and Return (Section 5.2 points 1, 3 and 5).
Step 8 adds Transfer and Stock count (points 4 and 6), plus a generic
`DocumentReversal` for Section 2's rule "Posted documents ... cannot be
edited or deleted; mistakes are corrected with a reversing document."
Material allowances (the BOQ-Store link) are step 9.

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
    store+item (see `current_balance()`). An issue's unit_cost is the
    average cost *at the moment it's posted* (computed then, fixed
    afterward -- see `Issue.post()`), which is what keeps this sum
    correct even once stock leaves as well as arrives.

    `document_type` + `document_id` name the posted document that
    created this movement, spelled out as plain fields (not a
    GenericForeignKey) because that's literally how Section 5.1 lists
    them ("document type, document ID") — the BOQ item/section links
    are Section 5.2's "issued against a requisition (or directly, with
    a BOQ item)" and step 9's material-allowance reconciliation.
    """

    DOCUMENT_GRN = "grn"
    DOCUMENT_ISSUE = "issue"
    DOCUMENT_RETURN = "return"
    DOCUMENT_TRANSFER = "transfer"
    DOCUMENT_ADJUSTMENT = "adjustment"
    DOCUMENT_REVERSAL = "reversal"
    DOCUMENT_CHOICES = [
        (DOCUMENT_GRN, "GRN"),
        (DOCUMENT_ISSUE, "Issue"),
        (DOCUMENT_RETURN, "Return to store"),
        (DOCUMENT_TRANSFER, "Transfer"),
        (DOCUMENT_ADJUSTMENT, "Stock count adjustment"),
        (DOCUMENT_REVERSAL, "Reversal"),
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
    deleted; mistakes are corrected with a reversing document" — see
    `reverse()`, added in step 8 (`DocumentReversal`, defined further
    down this file).
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

    @property
    def is_reversed(self):
        return DocumentReversal.objects.filter(
            document_type=StockMovement.DOCUMENT_GRN, document_id=self.pk
        ).exists()

    def reverse(self, user, reason):
        """Step 8's answer to "mistakes are corrected with a reversing
        document": negates every StockMovement this GRN posted. Only a
        Posted, not-already-reversed GRN can be reversed -- the GRN
        row itself is untouched (still Posted, still locked), the
        correction is a separate document, per Section 2's rule."""
        if self.status != self.STATUS_POSTED:
            raise ValidationError("Only a Posted GRN can be reversed.")
        return DocumentReversal.create_for(StockMovement.DOCUMENT_GRN, self.pk, user, reason)


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


class StoreRequisition(models.Model):
    """
    Section 5.2 point 1: "a Site Engineer requests materials for a BOQ
    item and section." Unlike a GRN or Issue, a requisition names a
    *project*, not a store -- the requester doesn't pick which store
    fulfils it, the Storekeeper does, at issue time (`Issue.requisition`).

    Numbers are scoped per project (there's no store yet to scope them
    to). Status starts Pending and is recomputed by `update_status()`
    every time an Issue that references this requisition is posted --
    there's no separate "submit" step the way a GRN has Draft-then-Post,
    since a requisition is usable for issuing from the moment it's
    created.
    """

    STATUS_PENDING = "pending"
    STATUS_PARTLY_ISSUED = "partly_issued"
    STATUS_ISSUED = "issued"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PARTLY_ISSUED, "Partly issued"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_REJECTED, "Rejected"),
    ]

    number = models.PositiveIntegerField()
    date = models.DateField()
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="requisitions")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="requisitions")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)

    class Meta:
        ordering = ["project", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"], name="unique_requisition_number_per_project"
            )
        ]

    def __str__(self):
        return f"Requisition {self.number} — {self.project.code}"

    @property
    def is_editable(self):
        return self.status == self.STATUS_PENDING

    def issued_quantity(self, item):
        """How much of `item` has been issued so far against this
        requisition (summed across every Issue that references it,
        posted or not -- a Draft issue already "claims" the quantity
        on it, same as the storekeeper would treat a request they're
        part-way through fulfilling)."""
        return self.issues.filter(lines__item=item).aggregate(
            total=models.Sum("lines__quantity", filter=models.Q(lines__item=item))
        )["total"] or Decimal("0.000")

    def update_status(self):
        """Recomputes Pending/Partly issued/Issued from how much of
        each requested line has actually been issued. Never overrides
        Rejected -- that's a separate, manual decision."""
        if self.status == self.STATUS_REJECTED:
            return
        lines = list(self.lines.all())
        if not lines:
            return
        issued_amounts = [self.issued_quantity(line.item) for line in lines]
        if all(issued >= line.quantity for issued, line in zip(issued_amounts, lines)):
            self.status = self.STATUS_ISSUED
        elif any(issued > 0 for issued in issued_amounts):
            self.status = self.STATUS_PARTLY_ISSUED
        else:
            self.status = self.STATUS_PENDING
        self.save()

    def reject(self):
        if self.status not in (self.STATUS_PENDING, self.STATUS_PARTLY_ISSUED):
            raise ValidationError("Only a Pending or Partly issued requisition can be rejected.")
        self.status = self.STATUS_REJECTED
        self.save()


class RequisitionLine(models.Model):
    requisition = models.ForeignKey(StoreRequisition, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    # "for a BOQ item" -- required, same as an Issue line's, so a
    # requisition can't itself be the loophole around that rule.
    boq_item = models.ForeignKey("boq.BOQItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code} x{self.quantity}"


class Issue(models.Model):
    """
    Section 5.2 point 3: "the Storekeeper issues materials against a
    requisition (or directly, with a BOQ item). Posting removes stock."
    `requisition` is optional (a direct issue skips it entirely), but
    every line still needs its own `boq_item` regardless -- that's the
    step 7 acceptance test's second half: "issue without a BOQ item is
    blocked."
    """

    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_POSTED, "Posted")]

    number = models.PositiveIntegerField()
    date = models.DateField()
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="issues")
    requisition = models.ForeignKey(
        StoreRequisition, on_delete=models.PROTECT, null=True, blank=True, related_name="issues"
    )
    issued_to = models.CharField(max_length=255, help_text="A person or a plant/vehicle.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        ordering = ["store", "number"]
        constraints = [
            models.UniqueConstraint(fields=["store", "number"], name="unique_issue_number_per_store")
        ]

    def __str__(self):
        return f"Issue {self.number} — {self.store.code}"

    @property
    def is_editable(self):
        return self.status == self.STATUS_DRAFT

    def post(self, user):
        """
        Rule (step 7 acceptance test): issuing more than is in stock is
        blocked, and every line must carry a BOQ item. Both are
        checked for *every* line before anything is written -- one bad
        line blocks the whole issue, same all-or-nothing reasoning as
        the BOQ Excel import and GRN posting.

        The unit cost recorded on each movement is the store's current
        weighted-average cost for that item, taken once at the start of
        posting (not per line), so two lines for the same item in one
        issue don't see a different cost mid-way through.
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a Draft issue can be posted.")
        lines = list(self.lines.select_related("item"))
        if not lines:
            raise ValidationError("An issue needs at least one line before it can be posted.")

        errors = []
        cost_by_item = {}
        requested_by_item = {}
        for line in lines:
            if line.boq_item_id is None:
                errors.append(f"{line.item.code}: an issue line must have a BOQ item.")
            requested_by_item[line.item_id] = requested_by_item.get(line.item_id, Decimal("0")) + line.quantity

        for item_id, requested in requested_by_item.items():
            item = next(l.item for l in lines if l.item_id == item_id)
            available, _, average_cost = StockMovement.current_balance(self.store, item)
            if requested > available:
                errors.append(
                    f"{item.code}: issuing {requested} {item.unit.code} would exceed the "
                    f"{available} {item.unit.code} in stock at {self.store.code}."
                )
            cost_by_item[item_id] = average_cost

        if errors:
            raise ValidationError(errors)

        with transaction.atomic():
            for line in lines:
                StockMovement.objects.create(
                    store=self.store,
                    item=line.item,
                    quantity=-line.quantity,
                    unit_cost=cost_by_item[line.item_id],
                    document_type=StockMovement.DOCUMENT_ISSUE,
                    document_id=self.pk,
                    boq_item_id=line.boq_item_id,
                    section_id=line.section_id,
                    created_by=user,
                )
            self.status = self.STATUS_POSTED
            self.save()
            if self.requisition_id:
                self.requisition.update_status()

    @property
    def is_reversed(self):
        return DocumentReversal.objects.filter(
            document_type=StockMovement.DOCUMENT_ISSUE, document_id=self.pk
        ).exists()

    def reverse(self, user, reason):
        """Same reversing-document mechanism as GRN.reverse() -- see
        DocumentReversal. Reversing an issue restores the stock it
        removed (each movement's quantity was negative, so its
        negation is positive), so it can never itself be blocked by
        the "no negative stock" rule; it's included there for
        symmetry with GRN/transfer reversal, which can be blocked."""
        if self.status != self.STATUS_POSTED:
            raise ValidationError("Only a Posted issue can be reversed.")
        return DocumentReversal.create_for(StockMovement.DOCUMENT_ISSUE, self.pk, user, reason)


class IssueLine(models.Model):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    # Required (no null=True): "issue without a BOQ item is blocked" is
    # enforced here, at the field, not just in post() -- a form can't
    # even save a line without one.
    boq_item = models.ForeignKey("boq.BOQItem", on_delete=models.PROTECT, related_name="+")
    section = models.ForeignKey(
        Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code} x{self.quantity}"


class ReturnToStore(models.Model):
    """
    Section 5.2 point 5: "unused materials come back and are credited
    to the original BOQ item." `linked_issue` is optional (a return
    doesn't have to trace back to one specific issue), and each line's
    `boq_item` defaults to the credit target when the return is
    created from a linked issue, but can be set directly -- unlike
    Issue, this isn't hard-required, since the spec's step 7
    acceptance test only names issuing, not returning.
    """

    STATUS_DRAFT = "draft"
    STATUS_POSTED = "posted"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_POSTED, "Posted")]

    CONDITION_GOOD = "good"
    CONDITION_DAMAGED = "damaged"
    CONDITION_CHOICES = [(CONDITION_GOOD, "Good"), (CONDITION_DAMAGED, "Damaged")]

    number = models.PositiveIntegerField()
    date = models.DateField()
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="returns")
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    linked_issue = models.ForeignKey(
        Issue, on_delete=models.PROTECT, null=True, blank=True, related_name="returns"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        ordering = ["store", "number"]
        constraints = [
            models.UniqueConstraint(fields=["store", "number"], name="unique_return_number_per_store")
        ]

    def __str__(self):
        return f"Return {self.number} — {self.store.code}"

    @property
    def is_editable(self):
        return self.status == self.STATUS_DRAFT

    def post(self, user):
        """
        Adds stock back, at the average cost the store is currently
        carrying for that item (there's no "cost it was issued at"
        recorded anywhere useful to recover, since Section 5.1 doesn't
        track that on the Issue itself) -- a disclosed simplification,
        same spirit as everywhere else the average-cost method is used.
        Damaged-condition lines still restore quantity (the physical
        item is back in the store); a real system might value those at
        zero or write them off separately, which is future work.
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a Draft return can be posted.")
        lines = list(self.lines.select_related("item"))
        if not lines:
            raise ValidationError("A return needs at least one line before it can be posted.")
        with transaction.atomic():
            for line in lines:
                _, _, average_cost = StockMovement.current_balance(self.store, line.item)
                StockMovement.objects.create(
                    store=self.store,
                    item=line.item,
                    quantity=line.quantity,
                    unit_cost=average_cost,
                    document_type=StockMovement.DOCUMENT_RETURN,
                    document_id=self.pk,
                    boq_item_id=line.boq_item_id,
                    created_by=user,
                )
            self.status = self.STATUS_POSTED
            self.save()

    @property
    def is_reversed(self):
        return DocumentReversal.objects.filter(
            document_type=StockMovement.DOCUMENT_RETURN, document_id=self.pk
        ).exists()

    def reverse(self, user, reason):
        """Same mechanism as GRN.reverse()/Issue.reverse() -- reversing
        a return removes the stock it restored, so (unlike reversing
        an issue) it *can* be blocked if that stock has since moved on
        elsewhere -- DocumentReversal.create_for() checks this."""
        if self.status != self.STATUS_POSTED:
            raise ValidationError("Only a Posted return can be reversed.")
        return DocumentReversal.create_for(StockMovement.DOCUMENT_RETURN, self.pk, user, reason)


class ReturnLine(models.Model):
    ret = models.ForeignKey(ReturnToStore, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    condition = models.CharField(
        max_length=10, choices=ReturnToStore.CONDITION_CHOICES, default=ReturnToStore.CONDITION_GOOD
    )
    boq_item = models.ForeignKey(
        "boq.BOQItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code} x{self.quantity} ({self.condition})"


class Transfer(models.Model):
    """
    Section 5.2 point 4: "stock leaves one store on dispatch and
    arrives at the other only when received." Three states, not the
    spec's literal two ("In transit, Received") -- a Draft stage is
    added first, same as every other document here, so lines can be
    built up and corrected before anything actually leaves a store;
    the spec's two named states are exactly what Draft leads into.

    Section 5.3 rule 7's "transfers in transit are shown separately
    and count in neither store's stock" falls out of the two-movement
    design for free: `dispatch()` removes stock from `from_store`
    immediately (so it's already gone from there), and `receive()` is
    the *only* thing that adds it to `to_store` (so it isn't there
    yet either) -- nothing extra needs to track "in transit" for the
    balance to be correct; `current_balance()` already excludes it
    from both stores by construction. "Shown separately" is simply
    this model's own `status` column on the transfer list.

    Numbered per *project*, not per store like GRN/Issue/Return --
    a transfer inherently touches two stores, so there's no single
    store to scope the sequence to; this is a disclosed deviation from
    Section 5.3 rule 5's literal "per store."
    """

    STATUS_DRAFT = "draft"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_RECEIVED = "received"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_IN_TRANSIT, "In transit"),
        (STATUS_RECEIVED, "Received"),
    ]

    number = models.PositiveIntegerField()
    date = models.DateField()
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="transfers")
    from_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="transfers_out")
    to_store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="transfers_in")
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        ordering = ["project", "number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_transfer_number_per_project")
        ]

    def __str__(self):
        return f"Transfer {self.number} — {self.from_store.code} to {self.to_store.code}"

    def clean(self):
        if self.from_store_id and self.to_store_id and self.from_store_id == self.to_store_id:
            raise ValidationError("A transfer must be between two different stores.")

    @property
    def is_editable(self):
        return self.status == self.STATUS_DRAFT

    def dispatch(self, user):
        """
        Rule 2's "cannot exceed stock on hand" applies here exactly as
        it does to Issue.post(): every line's quantity is summed per
        item across the whole transfer before checking it against
        `from_store`'s balance, and the average cost is captured once
        per item and frozen onto each line (`dispatch_unit_cost`) so
        `receive()` can post the arrival at the same cost without
        re-deriving it later, once time has passed and the store's
        average may have moved on.
        """
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a Draft transfer can be dispatched.")
        lines = list(self.lines.select_related("item"))
        if not lines:
            raise ValidationError("A transfer needs at least one line before it can be dispatched.")

        errors = []
        cost_by_item = {}
        requested_by_item = {}
        for line in lines:
            requested_by_item[line.item_id] = requested_by_item.get(line.item_id, Decimal("0")) + line.quantity
        for item_id, requested in requested_by_item.items():
            item = next(l.item for l in lines if l.item_id == item_id)
            available, _, average_cost = StockMovement.current_balance(self.from_store, item)
            if requested > available:
                errors.append(
                    f"{item.code}: transferring {requested} {item.unit.code} would exceed the "
                    f"{available} {item.unit.code} in stock at {self.from_store.code}."
                )
            cost_by_item[item_id] = average_cost
        if errors:
            raise ValidationError(errors)

        with transaction.atomic():
            for line in lines:
                line.dispatch_unit_cost = cost_by_item[line.item_id]
                line.save(update_fields=["dispatch_unit_cost"])
                StockMovement.objects.create(
                    store=self.from_store,
                    item=line.item,
                    quantity=-line.quantity,
                    unit_cost=cost_by_item[line.item_id],
                    document_type=StockMovement.DOCUMENT_TRANSFER,
                    document_id=self.pk,
                    created_by=user,
                )
            self.status = self.STATUS_IN_TRANSIT
            self.dispatched_by = user
            self.save()

    def receive(self, user):
        """Posts the arrival at `to_store`, at the cost frozen onto
        each line when it was dispatched -- a transfer doesn't
        re-value stock, it just moves it."""
        if self.status != self.STATUS_IN_TRANSIT:
            raise ValidationError("Only an In transit transfer can be received.")
        lines = list(self.lines.all())
        with transaction.atomic():
            for line in lines:
                StockMovement.objects.create(
                    store=self.to_store,
                    item=line.item,
                    quantity=line.quantity,
                    unit_cost=line.dispatch_unit_cost,
                    document_type=StockMovement.DOCUMENT_TRANSFER,
                    document_id=self.pk,
                    created_by=user,
                )
            self.status = self.STATUS_RECEIVED
            self.received_by = user
            self.save()


class TransferLine(models.Model):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    # Frozen at dispatch time (see Transfer.dispatch()); blank until then.
    dispatch_unit_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code} x{self.quantity}"


class StockCount(models.Model):
    """
    Section 5.2 point 6: "physical count; differences post as
    adjustments after Project Manager approval." Section 2's
    permissions table gives the Storekeeper "Yes" (they do the count)
    and the Project Manager "Approve" -- the same shape as GRN/Issue,
    except the posting action (`approve()`) belongs to a different
    role than the one who built the document, and Section 2's own
    rule "No user can approve their own adjustment" is enforced there.
    """

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
    ]

    number = models.PositiveIntegerField()
    date = models.DateField()
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="stock_counts")
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    class Meta:
        ordering = ["store", "number"]
        constraints = [
            models.UniqueConstraint(fields=["store", "number"], name="unique_stock_count_number_per_store")
        ]

    def __str__(self):
        return f"Stock count {self.number} — {self.store.code}"

    @property
    def is_editable(self):
        return self.status == self.STATUS_DRAFT

    def submit(self):
        """Draft -> Submitted: hands the count to a Project Manager
        for approval. Nothing is posted yet -- submitting only closes
        the counting stage, the same way a requisition's "Pending"
        just means it's waiting on someone else's action."""
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a Draft stock count can be submitted.")
        if not self.lines.exists():
            raise ValidationError("A stock count needs at least one line before it can be submitted.")
        self.status = self.STATUS_SUBMITTED
        self.save()

    def approve(self, user):
        """
        Posts one StockMovement per line whose counted quantity
        differs from its (frozen-at-add-time) system quantity, at the
        store's current average cost -- the adjustment, per Section
        5.2's own wording. "No user can approve their own adjustment"
        (Section 2) is checked here: the approver can never be the
        person who counted.
        """
        if self.status != self.STATUS_SUBMITTED:
            raise ValidationError("Only a Submitted stock count can be approved.")
        if user.id == self.counted_by_id:
            raise ValidationError("The person who counted cannot approve their own stock count.")
        lines = list(self.lines.select_related("item"))
        with transaction.atomic():
            for line in lines:
                difference = line.difference
                if difference == 0:
                    continue
                _, _, average_cost = StockMovement.current_balance(self.store, line.item)
                StockMovement.objects.create(
                    store=self.store,
                    item=line.item,
                    quantity=difference,
                    unit_cost=average_cost,
                    document_type=StockMovement.DOCUMENT_ADJUSTMENT,
                    document_id=self.pk,
                    created_by=user,
                )
            self.status = self.STATUS_APPROVED
            self.approved_by = user
            self.save()


class StockCountLine(models.Model):
    stock_count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(StoreItem, on_delete=models.PROTECT, related_name="+")
    # Snapshotted from current_balance() when the line is added (see
    # store/forms.py), not user-editable -- a stock count compares a
    # physical count against the system figure *at the moment of
    # counting*, same as any real stock take.
    system_quantity = models.DecimalField(max_digits=14, decimal_places=3, editable=False)
    counted_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.item.code}: system {self.system_quantity}, counted {self.counted_quantity}"

    @property
    def difference(self):
        return self.counted_quantity - self.system_quantity


class DocumentReversal(models.Model):
    """
    Section 2's rule: "Posted documents (GRN, issue, transfer,
    adjustment) cannot be edited or deleted; mistakes are corrected
    with a reversing document." One generic model handles all of
    them uniformly, by working from `StockMovement.document_type` +
    `document_id` rather than each document's own line structure:
    reversing a document means finding every `StockMovement` it
    posted and creating an equal-and-opposite one for each, so this
    works the same way whether the original was a GRN, an Issue or a
    Return without any type-specific code.

    A Transfer or a Stock count adjustment isn't reversible through
    this model yet -- disclosed simplification. A Transfer already
    has its own two-sided correction path (the two stores can transfer
    the material back), and a Stock count's approved adjustment *is*
    itself the correction for whatever the physical count found, so
    neither had the same obvious need as GRN/Issue/Return, which is
    what step 8's acceptance-test-adjacent rule ("posted GRN cannot be
    edited") is really about.

    Exactly one reversal per document (see the unique constraint) --
    to correct further, post a brand-new document rather than
    reversing a reversal.
    """

    document_type = models.CharField(max_length=15, choices=StockMovement.DOCUMENT_CHOICES)
    document_id = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "document_id"], name="one_reversal_per_document"
            )
        ]

    def __str__(self):
        return f"Reversal of {self.document_type} #{self.document_id}"

    @staticmethod
    def create_for(document_type, document_id, user, reason):
        original_movements = list(
            StockMovement.objects.filter(document_type=document_type, document_id=document_id)
        )
        if not original_movements:
            raise ValidationError("Nothing was posted for this document — there is nothing to reverse.")
        if DocumentReversal.objects.filter(document_type=document_type, document_id=document_id).exists():
            raise ValidationError("This document has already been reversed.")

        # Group the would-be reversal quantities per store+item so a
        # reversal that would take stock negative (e.g. a GRN whose
        # delivery has since partly been issued away) is blocked
        # before anything is written -- same all-or-nothing check as
        # Issue.post().
        totals = {}
        for movement in original_movements:
            key = (movement.store_id, movement.item_id)
            totals[key] = totals.get(key, Decimal("0")) - movement.quantity
        errors = []
        for (store_id, item_id), delta in totals.items():
            if delta < 0:
                store = Store.objects.get(pk=store_id)
                item = StoreItem.objects.get(pk=item_id)
                available, _, _ = StockMovement.current_balance(store, item)
                if available + delta < 0:
                    errors.append(
                        f"{item.code}: reversing this document would take stock at {store.code} negative."
                    )
        if errors:
            raise ValidationError(errors)

        with transaction.atomic():
            reversal = DocumentReversal.objects.create(
                document_type=document_type, document_id=document_id, reason=reason, reversed_by=user
            )
            for movement in original_movements:
                StockMovement.objects.create(
                    store=movement.store,
                    item=movement.item,
                    quantity=-movement.quantity,
                    unit_cost=movement.unit_cost,
                    document_type=StockMovement.DOCUMENT_REVERSAL,
                    document_id=reversal.pk,
                    boq_item_id=movement.boq_item_id,
                    section_id=movement.section_id,
                    created_by=user,
                )
        return reversal
