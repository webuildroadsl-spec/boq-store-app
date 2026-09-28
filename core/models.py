"""
Shared foundations (Section 3 of the spec): Company, Project, Section,
Unit of measure, and the project-level roles from Section 2. Both the
BOQ and Store modules (built in later steps) will hang their tables off
Project and Section defined here.
"""

from django.conf import settings
from django.db import models

# Section 3: "Currency ... default Sierra Leone Leone (SLE); USD also
# supported because many road contracts are priced in dollars."
CURRENCY_CHOICES = [
    ("SLE", "Sierra Leone Leone"),
    ("USD", "US Dollar"),
]

# Section 2: the six roles. Stored as plain strings (not a separate
# table) because the list is fixed by the spec, not something an admin
# configures.
ROLE_ADMIN = "admin"
ROLE_PROJECT_MANAGER = "project_manager"
ROLE_QS = "qs"
ROLE_SITE_ENGINEER = "site_engineer"
ROLE_STOREKEEPER = "storekeeper"
ROLE_VIEWER = "viewer"

ROLE_CHOICES = [
    (ROLE_ADMIN, "Admin"),
    (ROLE_PROJECT_MANAGER, "Project Manager"),
    (ROLE_QS, "QS"),
    (ROLE_SITE_ENGINEER, "Site Engineer"),
    (ROLE_STOREKEEPER, "Storekeeper"),
    (ROLE_VIEWER, "Viewer"),
]


class Company(models.Model):
    """
    Section 3: "One company per installation in the MVP; design tables
    with a company_id so the system can later serve many contractors."

    We still create the table with a foreign key from Project, but the
    MVP only ever has one row here (see the `get_default` seed below).
    """

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    logo = models.FileField(upload_to="company_logos/", blank=True, null=True)
    default_currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default="SLE"
    )
    tax_settings = models.TextField(
        blank=True,
        help_text="Free-text notes on VAT/GST rates and rules until the "
        "reporting step (Section 7) needs structured tax fields.",
    )

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class UnitOfMeasure(models.Model):
    """Section 3 master list: m, m², m³, km, t, kg, L, nr, item, sum, day, hr."""

    TYPE_LENGTH = "length"
    TYPE_AREA = "area"
    TYPE_VOLUME = "volume"
    TYPE_WEIGHT = "weight"
    TYPE_COUNT = "count"
    TYPE_TIME = "time"
    TYPE_LUMP_SUM = "lump_sum"

    TYPE_CHOICES = [
        (TYPE_LENGTH, "Length"),
        (TYPE_AREA, "Area"),
        (TYPE_VOLUME, "Volume"),
        (TYPE_WEIGHT, "Weight"),
        (TYPE_COUNT, "Count"),
        (TYPE_TIME, "Time"),
        (TYPE_LUMP_SUM, "Lump sum"),
    ]

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Project(models.Model):
    """Section 3: one project per contract."""

    STATUS_PLANNING = "planning"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_COMPLETED = "completed"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_PLANNING, "Planning"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CLOSED, "Closed"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.PROTECT, related_name="projects"
    )
    code = models.CharField(max_length=20, unique=True, help_text="e.g. BTR-2026")
    name = models.CharField(max_length=255)
    client = models.CharField(max_length=255, blank=True)
    consultant_engineer = models.CharField(max_length=255, blank=True)
    contract_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=255, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    contract_sum = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="SLE")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PLANNING
    )

    # Section 2: who can see/act on this project, and in what role.
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ProjectMembership",
        related_name="projects",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Section(models.Model):
    """
    Section 3: "road projects are split by section or chainage (e.g.
    Ch 0+000 – 2+500)." Chainage is stored in km to 3 decimal places, as
    the spec's fields list specifies.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="sections"
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255, blank=True)
    start_chainage = models.DecimalField(max_digits=8, decimal_places=3)
    end_chainage = models.DecimalField(max_digits=8, decimal_places=3)

    class Meta:
        ordering = ["project", "start_chainage"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "code"], name="unique_section_code_per_project"
            )
        ]

    def __str__(self):
        return f"{self.project.code} / {self.code}"


class ProjectMembership(models.Model):
    """
    Section 2: "Each user is assigned a role and one or more projects,
    and sees only data for those projects" and "A user can hold
    different roles on different projects." This table is exactly that
    join: one row per (user, project), carrying that project's role.

    Everything in the app that lists or opens a project's data is
    expected to filter through this table (see `core.permissions`)
    rather than trust a user-supplied project id — that's what makes a
    Storekeeper on Project A unable to see Project B.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project"], name="unique_membership_per_project"
            )
        ]

    def __str__(self):
        return f"{self.user} — {self.project.code} ({self.get_role_display()})"
