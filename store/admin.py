from django.contrib import admin

from .models import (
    GRN,
    GRNAttachment,
    GRNLine,
    DocumentReversal,
    Issue,
    IssueLine,
    ItemCategory,
    RequisitionLine,
    ReturnLine,
    ReturnToStore,
    StockCount,
    StockCountLine,
    Store,
    StockMovement,
    StoreItem,
    StoreRequisition,
    Supplier,
    Transfer,
    TransferLine,
)


@admin.register(ItemCategory)
class ItemCategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("project", "code", "name", "type", "storekeeper")
    list_filter = ("type", "project")
    search_fields = ("code", "name")


@admin.register(StoreItem)
class StoreItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "unit", "is_fuel", "active")
    list_filter = ("category", "is_fuel", "active")
    search_fields = ("code", "name")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone", "email")
    search_fields = ("name",)


class GRNLineInline(admin.TabularInline):
    model = GRNLine
    extra = 0


class GRNAttachmentInline(admin.TabularInline):
    model = GRNAttachment
    extra = 0


@admin.register(GRN)
class GRNAdmin(admin.ModelAdmin):
    list_display = ("store", "number", "date", "supplier", "status", "total_value")
    list_filter = ("status", "store")
    inlines = [GRNLineInline, GRNAttachmentInline]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("store", "item", "quantity", "unit_cost", "total_cost", "document_type", "document_id", "created_at")
    list_filter = ("store", "document_type")
    search_fields = ("item__code", "item__name")


class RequisitionLineInline(admin.TabularInline):
    model = RequisitionLine
    extra = 0


@admin.register(StoreRequisition)
class StoreRequisitionAdmin(admin.ModelAdmin):
    list_display = ("project", "number", "date", "section", "requested_by", "status")
    list_filter = ("status", "project")
    inlines = [RequisitionLineInline]


class IssueLineInline(admin.TabularInline):
    model = IssueLine
    extra = 0


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("store", "number", "date", "requisition", "issued_to", "status")
    list_filter = ("status", "store")
    inlines = [IssueLineInline]


class ReturnLineInline(admin.TabularInline):
    model = ReturnLine
    extra = 0


@admin.register(ReturnToStore)
class ReturnToStoreAdmin(admin.ModelAdmin):
    list_display = ("store", "number", "date", "linked_issue", "returned_by", "status")
    list_filter = ("status", "store")
    inlines = [ReturnLineInline]


class TransferLineInline(admin.TabularInline):
    model = TransferLine
    extra = 0


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("project", "number", "date", "from_store", "to_store", "status")
    list_filter = ("status", "project")
    inlines = [TransferLineInline]


class StockCountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 0


@admin.register(StockCount)
class StockCountAdmin(admin.ModelAdmin):
    list_display = ("store", "number", "date", "counted_by", "approved_by", "status")
    list_filter = ("status", "store")
    inlines = [StockCountLineInline]


@admin.register(DocumentReversal)
class DocumentReversalAdmin(admin.ModelAdmin):
    list_display = ("document_type", "document_id", "reversed_by", "created_at")
    list_filter = ("document_type",)
