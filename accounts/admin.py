from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from .models import (
    User,
    SupportTicket,
    SupportChatMessage,
    UserLoginActivity,
    Habit,
    Task,
    Tracker,
)


class UserCreationAdminForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Password confirmation", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "phone")

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords don't match.")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserChangeAdminForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        label="Password",
        help_text="Raw passwords are not stored, so there is no way to see this user's password.",
    )

    class Meta:
        model = User
        fields = "__all__"

    def clean_password(self):
        return self.initial["password"]


@admin.action(description="Grant support desk access")
def grant_support_desk_access(modeladmin, request, queryset):
    updated = queryset.update(is_support_agent=True, is_active=True)
    modeladmin.message_user(
        request,
        f"{updated} user(s) can now sign in through support login.",
        level=messages.SUCCESS,
    )


@admin.action(description="Remove support desk access")
def revoke_support_desk_access(modeladmin, request, queryset):
    updated = queryset.update(is_support_agent=False)
    modeladmin.message_user(
        request,
        f"Support desk access removed for {updated} user(s).",
        level=messages.SUCCESS,
    )


@admin.action(description="Grant full admin dashboard access")
def grant_admin_dashboard_access(modeladmin, request, queryset):
    updated = queryset.update(is_staff=True, is_active=True)
    modeladmin.message_user(
        request,
        f"{updated} user(s) can now sign in as full admin.",
        level=messages.SUCCESS,
    )


@admin.action(description="Remove full admin dashboard access")
def revoke_admin_dashboard_access(modeladmin, request, queryset):
    updated = queryset.update(is_staff=False)
    modeladmin.message_user(
        request,
        f"Full admin access removed for {updated} user(s).",
        level=messages.SUCCESS,
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    model = User
    actions = (
        grant_support_desk_access,
        revoke_support_desk_access,
        grant_admin_dashboard_access,
        revoke_admin_dashboard_access,
    )
    list_display = (
        "email",
        "first_name",
        "last_name",
        "phone",
        "is_support_agent",
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
    )
    list_filter = ("is_support_agent", "is_staff", "is_superuser", "is_active", "date_joined")
    ordering = ("-date_joined",)
    search_fields = ("email", "first_name", "last_name", "phone")
    readonly_fields = (
        "last_login",
        "date_joined",
        "limit_subscription_started_at",
        "limit_subscription_cancelled_at",
        "limit_subscription_challenge_expires_at",
    )
    filter_horizontal = ("groups", "user_permissions")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "profile_photo")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_support_agent",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Limit subscription",
            {
                "fields": (
                    "is_limit_subscription_active",
                    "limit_subscription_started_at",
                    "limit_subscription_cancelled_at",
                    "limit_subscription_challenge",
                    "limit_subscription_answer",
                    "limit_subscription_challenge_expires_at",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "phone",
                    "is_active",
                    "is_support_agent",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
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
