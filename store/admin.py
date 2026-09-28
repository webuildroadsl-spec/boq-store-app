from django.contrib import admin

from .models import GRN, GRNAttachment, GRNLine, ItemCategory, Store, StockMovement, StoreItem, Supplier


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
