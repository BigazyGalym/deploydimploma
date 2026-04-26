# accounts/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.conf import settings
from django.utils import timezone
from datetime import date
import datetime
import uuid

# ---------------- USER ----------------
class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)

class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15, null=True, blank=True)
    profile_photo = models.ImageField(upload_to="profiles/", null=True, blank=True)
    is_limit_subscription_active = models.BooleanField(default=False)
    limit_subscription_started_at = models.DateTimeField(null=True, blank=True)
    limit_subscription_cancelled_at = models.DateTimeField(null=True, blank=True)
    limit_subscription_challenge = models.CharField(max_length=64, blank=True)
    limit_subscription_answer = models.IntegerField(null=True, blank=True)
    limit_subscription_challenge_expires_at = models.DateTimeField(null=True, blank=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def __str__(self):
        return self.email


PREMIUM_LIMIT_CATEGORY_NAMES = {
    "другие",
    "другое",
    "басқа",
    "other",
    "здоровье",
    "денсаулық",
    "health",
    "путешествия",
    "саяхат",
    "travel",
}


def normalize_limit_category_name(value):
    return str(value or "").strip().casefold()


def is_premium_limit_category_name(value):
    return normalize_limit_category_name(value) in PREMIUM_LIMIT_CATEGORY_NAMES

# ---------------- CATEGORY ----------------
class Category(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

# ---------------- WALLET ----------------
class Wallet(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.name} — {self.balance}"

# ---------------- TRANSACTION ----------------
class Transaction(models.Model):
    TYPE_CHOICES = (
        ("income", "Income"),
        ("expense", "Expense"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(auto_now_add=True)
    time = models.TimeField(default=datetime.time(0, 0))
    comment = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.type} | {self.category} | {self.amount}"

# ---------------- BUDGET ----------------
class Budget(models.Model):
    PERIOD_CHOICES = (
        ("month", "Month"),
        ("week", "Week"),
        ("custom", "Custom"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    limit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    period_type = models.CharField(max_length=10, choices=PERIOD_CHOICES, default="month")
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(default=date.today)

    def __str__(self):
        return f"{self.category.name if self.category else 'TOTAL'} — {self.limit}"

# ---------------- DEBT ----------------
class Debt(models.Model):
    TYPE_CHOICES = (
        ("lent", "Lent"),
        ("borrowed", "Borrowed"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    counterparty = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    issued_date = models.DateField(default=date.today)
    issued_time = models.TimeField(default=datetime.time(9, 0))
    due_date = models.DateField()
    due_time = models.TimeField(default=datetime.time(18, 0))
    returned = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.type} — {self.counterparty} — {self.amount} ({self.issued_date} {self.issued_time})"


class EmailOTP(models.Model):
    PURPOSE_CHOICES = (
        ("verify", "Verify Email"),
        ("reset", "Reset Password"),
    )
    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=16, choices=PURPOSE_CHOICES)
    expires_at = models.DateTimeField()
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} ({self.purpose})"


class SupportTicket(models.Model):
    STATUS_CHOICES = (
        ("open", "Open"),
        ("in_progress", "In progress"),
        ("answered", "Answered"),
        ("closed", "Closed"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=120, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    admin_reply = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Ticket #{self.pk} - {self.user.email} - {self.status}"


class SupportChatMessage(models.Model):
    SENDER_CHOICES = (
        ("user", "User"),
        ("admin", "Admin"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="support_messages")
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender} -> {self.user.email}: {self.message[:40]}"


class UserLoginActivity(models.Model):
    SOURCE_CHOICES = (
        ("api", "API"),
        ("web_user", "Web User"),
        ("web_admin", "Web Admin"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_activities")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="api")
    login_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)
    logout_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-login_at"]
        indexes = [
            models.Index(fields=["user", "logout_at"]),
            models.Index(fields=["last_seen"]),
            models.Index(fields=["login_at"]),
        ]

    def __str__(self):
        return f"{self.user.email} ({self.source}) {self.login_at}"


class Habit(models.Model):
    FREQUENCY_CHOICES = (
        ("daily", "Daily"),
        ("weekly", "Weekly"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="habits")
    name = models.CharField(max_length=150)
    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default="daily")
    streak_count = models.PositiveIntegerField(default=0)
    last_completed_date = models.DateField(null=True, blank=True)
    points = models.PositiveIntegerField(default=0)
    badge = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "created_at"]
        indexes = [
            models.Index(fields=["user", "frequency"]),
            models.Index(fields=["last_completed_date"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.frequency})"

    def _current_period_start(self, reference_date=None):
        reference_date = reference_date or timezone.localdate()
        if self.frequency == "weekly":
            return reference_date - datetime.timedelta(days=reference_date.weekday())
        return reference_date

    def completed_this_period(self, reference_date=None):
        if not self.last_completed_date:
            return False
        return self._current_period_start(self.last_completed_date) == self._current_period_start(reference_date)

    def missed_periods(self, reference_date=None):
        reference_date = reference_date or timezone.localdate()
        if not self.last_completed_date:
            return 0
        current_start = self._current_period_start(reference_date)
        last_start = self._current_period_start(self.last_completed_date)
        if self.frequency == "weekly":
            weeks_between = (current_start - last_start).days // 7
            return max(weeks_between - 1, 0)
        return max((reference_date - self.last_completed_date).days - 1, 0)

    def is_due(self, reference_date=None):
        return not self.completed_this_period(reference_date)

    def refresh_badge(self):
        if self.streak_count >= 30:
            self.badge = "legend"
        elif self.streak_count >= 14:
            self.badge = "gold"
        elif self.streak_count >= 7:
            self.badge = "silver"
        elif self.streak_count >= 3:
            self.badge = "bronze"
        else:
            self.badge = ""
        return self.badge

    def mark_completed(self, completed_on=None):
        completed_on = completed_on or timezone.localdate()
        if self.completed_this_period(completed_on):
            return False

        step = datetime.timedelta(days=7 if self.frequency == "weekly" else 1)
        expected_previous_period = self._current_period_start(completed_on - step)
        last_period = self._current_period_start(self.last_completed_date) if self.last_completed_date else None
        self.streak_count = self.streak_count + 1 if last_period == expected_previous_period else 1
        self.last_completed_date = completed_on
        self.points += 25 if self.frequency == "weekly" else 10
        self.refresh_badge()
        self.save(
            update_fields=[
                "streak_count",
                "last_completed_date",
                "points",
                "badge",
                "updated_at",
            ]
        )
        return True


class Task(models.Model):
    CATEGORY_CHOICES = (
        ("finance", "Finance"),
        ("personal", "Personal"),
        ("work", "Work"),
    )
    PRIORITY_CHOICES = (
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tasks")
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="finance")
    due_date = models.DateTimeField()
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["completed", "due_date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "completed"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["priority"]),
        ]

    def __str__(self):
        return f"{self.name} [{self.priority}]"

    def is_overdue(self, reference_time=None):
        if self.completed:
            return False
        reference_time = reference_time or timezone.now()
        return self.due_date < reference_time


class Tracker(models.Model):
    TRACKER_TYPE_CHOICES = (
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("custom", "Custom"),
    )
    GOAL_DIRECTION_CHOICES = (
        ("at_least", "At least"),
        ("at_most", "At most"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trackers")
    name = models.CharField(max_length=150)
    tracker_type = models.CharField(max_length=10, choices=TRACKER_TYPE_CHOICES, default="daily")
    value = models.JSONField()
    date = models.DateTimeField(default=timezone.now)
    target_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    goal_direction = models.CharField(max_length=10, choices=GOAL_DIRECTION_CHOICES, default="at_least")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "name"]
        indexes = [
            models.Index(fields=["user", "name"]),
            models.Index(fields=["user", "date"]),
        ]

    def __str__(self):
        return f"{self.name} @ {self.date:%Y-%m-%d}"


class AIAssistantMessage(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
    )
    KIND_CHOICES = (
        ("chat", "Chat"),
        ("proactive", "Proactive"),
        ("system", "System"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ai_messages")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="assistant")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default="chat")
    message = models.TextField()
    source_key = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "kind"]),
            models.Index(fields=["user", "source_key"]),
        ]

    def __str__(self):
        return f"{self.role}::{self.kind}::{self.user.email}"
