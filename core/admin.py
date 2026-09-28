from django.contrib import admin

from .models import Company, Project, ProjectMembership, Section, UnitOfMeasure


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 1


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "default_currency")


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type")
    list_filter = ("type",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "status", "currency")
    list_filter = ("status", "company")
    search_fields = ("code", "name")
    inlines = [ProjectMembershipInline, SectionInline]


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "role")
    list_filter = ("role", "project")
