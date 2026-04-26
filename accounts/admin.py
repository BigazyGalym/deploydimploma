from django.contrib import admin
from .models import (
    SupportTicket,
    SupportChatMessage,
    UserLoginActivity,
    Habit,
    Task,
    Tracker,
)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subject", "status", "created_at", "updated_at", "answered_at")
    list_filter = ("status", "created_at", "answered_at")
    search_fields = ("user__email", "subject", "message", "admin_reply")
    ordering = ("-updated_at", "-created_at")


@admin.register(SupportChatMessage)
class SupportChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "sender", "created_at")
    list_filter = ("sender", "created_at")
    search_fields = ("user__email", "message")
    ordering = ("-created_at",)


@admin.register(UserLoginActivity)
class UserLoginActivityAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "source", "login_at", "last_seen", "logout_at", "ip_address")
    list_filter = ("source", "login_at", "logout_at")
    search_fields = ("user__email", "ip_address", "user_agent")
    ordering = ("-login_at",)


@admin.register(Habit)
class HabitAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "frequency", "streak_count", "points", "badge", "last_completed_date")
    list_filter = ("frequency", "badge", "last_completed_date")
    search_fields = ("name", "user__email")
    ordering = ("name",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "category", "priority", "due_date", "completed")
    list_filter = ("category", "priority", "completed")
    search_fields = ("name", "user__email")
    ordering = ("completed", "due_date")


@admin.register(Tracker)
class TrackerAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "tracker_type", "value", "target_value", "goal_direction", "date")
    list_filter = ("tracker_type", "goal_direction")
    search_fields = ("name", "user__email", "notes")
    ordering = ("-date",)
