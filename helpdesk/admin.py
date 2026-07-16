from django.contrib import admin
from .models import Ticket, TicketComment, Category, Tag, AuditLog, KnowledgeBase, TicketAttachment, GlossaryTerm, DatabaseYear


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'created_at']
    search_fields = ['name']


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    readonly_fields = ['author', 'created_at']


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    readonly_fields = ['uploaded_by', 'uploaded_at']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['ticket_id', 'title', 'status', 'priority', 'assigned_to', 'created_at']
    list_filter = ['status', 'priority', 'category', 'source']
    search_fields = ['ticket_id', 'title', 'requester_name', 'requester_email']
    readonly_fields = ['ticket_id', 'created_at', 'updated_at', 'resolved_at', 'closed_at', 'resolution_time_minutes']
    inlines = [TicketCommentInline, TicketAttachmentInline]
    date_hierarchy = 'created_at'
    save_on_top = True


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'action', 'model_name', 'object_repr', 'ip_address']
    list_filter = ['action', 'model_name']
    search_fields = ['user__username', 'object_repr', 'ip_address']
    readonly_fields = ['timestamp', 'user', 'action', 'model_name', 'object_id', 'object_repr', 'changes', 'ip_address', 'user_agent']
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'author', 'is_published', 'views', 'created_at']
    list_filter = ['is_published', 'category']
    search_fields = ['title', 'content']
    prepopulated_fields = {'slug': ('title',)}


@admin.register(GlossaryTerm)
class GlossaryTermAdmin(admin.ModelAdmin):
    list_display  = ['term', 'category', 'added_by', 'created_at']
    list_filter   = ['category']
    search_fields = ['term', 'definition']
    readonly_fields = ['added_by', 'created_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.added_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(DatabaseYear)
class DatabaseYearAdmin(admin.ModelAdmin):
    list_display  = ['year', 'db_name', 'is_current', 'is_active', 'created_by', 'created_at']
    list_filter   = ['is_current', 'is_active']
    readonly_fields = ['created_by', 'created_at']

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
