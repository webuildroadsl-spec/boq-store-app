from django.contrib import admin

from .models import BOQ, Bill, BOQItem


class BOQItemInline(admin.TabularInline):
    model = BOQItem
    extra = 0
    fk_name = "bill"


class BillInline(admin.TabularInline):
    model = Bill
    extra = 0


@admin.register(BOQ)
class BOQAdmin(admin.ModelAdmin):
    # contingency_percent / tax_percent (step 10, rule 7) are set here
    # for now rather than through a dedicated form on the BOQ detail
    # page -- a disclosed simplification; see README.
    list_display = ("project", "version_number", "type", "status", "contingency_percent", "tax_percent")
    list_filter = ("type", "status")
    inlines = [BillInline]


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("boq", "number", "title", "total")
    inlines = [BOQItemInline]


@admin.register(BOQItem)
class BOQItemAdmin(admin.ModelAdmin):
    list_display = ("bill", "item_reference", "description", "item_type", "quantity", "rate", "amount")
    list_filter = ("item_type",)
    search_fields = ("item_reference", "description")
