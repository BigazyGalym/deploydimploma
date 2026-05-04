import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import OperationalError, ProgrammingError
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from dj_rest_auth.registration.serializers import RegisterSerializer

from .models import (
    Wallet,
    Transaction,
    Budget,
    Debt,
    Category,
    SupportTicket,
    SupportChatMessage,
    exclude_debt_related_transactions,
    FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT,
    get_budget_date_range,
    get_limit_subscription_expires_at,
    has_active_limit_subscription,
    is_default_expense_category_name,
    is_premium_limit_category_name,
    normalize_limit_category_name,
    sync_limit_subscription_state,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def _find_existing_user_category(user, category_name, exclude_category_id=None):
    normalized_name = normalize_limit_category_name(category_name)
    if not normalized_name:
        return None

    queryset = Category.objects.filter(user=user).only("id", "name")
    if exclude_category_id is not None:
        queryset = queryset.exclude(pk=exclude_category_id)

    for category in queryset:
        if normalize_limit_category_name(category.name) == normalized_name:
            return category
    return None


def _count_custom_expense_categories(user, exclude_category_id=None):
    queryset = Category.objects.filter(user=user)
    if exclude_category_id is not None:
        queryset = queryset.exclude(pk=exclude_category_id)

    return sum(
        1
        for name in queryset.values_list("name", flat=True)
        if name and not is_default_expense_category_name(name)
    )


def _enforce_custom_expense_category_limit(
    user,
    category_name,
    *,
    field_name,
    exclude_category_id=None,
):
    if not user or getattr(user, "is_anonymous", True):
        return

    normalized_name = normalize_limit_category_name(category_name)
    if (
        not normalized_name
        or has_active_limit_subscription(user)
        or is_default_expense_category_name(category_name)
    ):
        return

    if _count_custom_expense_categories(user, exclude_category_id=exclude_category_id) >= FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT:
        raise serializers.ValidationError(
            {
                field_name: (
                    f"Free plan allows up to {FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT} custom expense "
                    "categories. Activate the limit subscription to add more."
                )
            }
        )
# Custom Field for Flexible Category Handling
class FlexibleCategoryField(serializers.Field):
    def to_representation(self, value):
        return value.name if value else None

    def to_internal_value(self, data):
        if data is None:
            return None
        
        request = self.context.get('request')
        if not request or not request.user or request.user.is_anonymous:
            raise serializers.ValidationError("Authentication required")
        
        user = request.user
        
        # Handle ID (int or str like '123')
        try:
            category_id = int(data)
            try:
                return Category.objects.get(id=category_id, user=user)
            except Category.DoesNotExist:
                raise serializers.ValidationError({"category": "Invalid category ID"})
        except ValueError:
            # Not a valid int, treat as name
            if isinstance(data, str):
                category_name = data.strip()
                if not category_name:
                    raise serializers.ValidationError({"category": "Category name is required."})

                existing_category = _find_existing_user_category(user, category_name)
                if existing_category:
                    return existing_category

                _enforce_custom_expense_category_limit(
                    user,
                    category_name,
                    field_name="category",
                )

                category_obj, _ = Category.objects.get_or_create(user=user, name=category_name)
                return category_obj
            else:
                raise serializers.ValidationError({"category": "Invalid category type"})

# ---------------- USER ----------------
class UserSerializer(serializers.ModelSerializer):
    is_limit_subscription_active = serializers.SerializerMethodField()
    limit_subscription_expires_at = serializers.SerializerMethodField()

    def get_is_limit_subscription_active(self, obj):
        return has_active_limit_subscription(obj)

    def get_limit_subscription_expires_at(self, obj):
        return get_limit_subscription_expires_at(obj)

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "phone",
            "email",
            "profile_photo",
            "password",
            "is_limit_subscription_active",
            "limit_subscription_started_at",
            "limit_subscription_expires_at",
            "limit_subscription_cancelled_at",
        ]
        extra_kwargs = {"password": {"write_only": True}}
        read_only_fields = [
            "id",
            "email",
            "is_limit_subscription_active",
            "limit_subscription_started_at",
            "limit_subscription_expires_at",
            "limit_subscription_cancelled_at",
        ]

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

class LoginSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data["email"] = self.user.email
        return data

# ---------------- CUSTOM REGISTRATION ----------------
class CustomRegisterSerializer(RegisterSerializer):
    username = None
    email = serializers.EmailField(required=True)

    def get_cleaned_data(self):
        return {
            "email": self.validated_data.get("email", ""),
            "password1": self.validated_data.get("password1", ""),
        }

# ---------------- WALLET ----------------
class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["id", "name", "balance"]

# ---------------- TRANSACTION ----------------
class TransactionSerializer(serializers.ModelSerializer):
    category = FlexibleCategoryField(required=False, allow_null=True)
    over_limit = serializers.SerializerMethodField()
    percent_used = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            "id", "wallet", "type", "category", "amount",
            "date", "time", "comment", "over_limit", "percent_used"
        ]

    def _budget_expense_total(self, user_id, budget):
        base_queryset = Transaction.objects.filter(
            user_id=user_id,
            type="expense",
            date__range=(budget.start_date, budget.end_date),
            category_id=budget.category_id,
        )

        try:
            return (
                exclude_debt_related_transactions(base_queryset)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal(0)
            )
        except (ProgrammingError, OperationalError):
            logger.warning(
                "Debt history links are unavailable while computing transaction budget totals.",
                exc_info=True,
            )
            return base_queryset.aggregate(total=Sum("amount"))["total"] or Decimal(0)

    def _is_debt_history_transaction(self, obj):
        try:
            return obj.issued_debts.exists() or obj.returned_debts.exists()
        except (ProgrammingError, OperationalError):
            logger.warning(
                "Debt history links are unavailable while serializing transaction %s.",
                getattr(obj, "pk", None),
                exc_info=True,
            )
            return False

    def get_over_limit(self, obj):
        if obj.type != "expense":
            return False
        if self._is_debt_history_transaction(obj):
            return False
        budget = Budget.objects.filter(
            user_id=obj.user_id,
            start_date__lte=obj.date,
            end_date__gte=obj.date,
            category_id=obj.category_id,
        ).first()
        if not budget:
            return False
        spent = self._budget_expense_total(obj.user_id, budget)
        return spent > budget.limit

    def get_percent_used(self, obj):
        if obj.type != "expense":
            return 0
        if self._is_debt_history_transaction(obj):
            return 0
        budget = Budget.objects.filter(
            user_id=obj.user_id,
            start_date__lte=obj.date,
            end_date__gte=obj.date,
            category_id=obj.category_id,
        ).first()
        if not budget:
            return 0
        spent = self._budget_expense_total(obj.user_id, budget)
        if not budget.limit or budget.limit == 0:
            return 0
        percent = (Decimal(spent) / budget.limit) * Decimal(100)
        return round(float(percent), 1)

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            raise serializers.ValidationError("Authentication required")
        validated_data.setdefault("time", timezone.localtime().time().replace(microsecond=0))
        return Transaction.objects.create(user=user, **validated_data)

# ---------------- BUDGET ----------------
class BudgetSerializer(serializers.ModelSerializer):
    category = FlexibleCategoryField(required=False, allow_null=True)
    period_type = serializers.ChoiceField(
        choices=[choice[0] for choice in Budget.PERIOD_CHOICES],
        required=False,
    )
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    spent = serializers.SerializerMethodField()
    remaining = serializers.SerializerMethodField()
    percent_used = serializers.SerializerMethodField()

    class Meta:
        model = Budget
        fields = [
            "id", "category", "limit", "period_type",
            "start_date", "end_date", "spent", "remaining", "percent_used"
        ]

    def _premium_slot_requested(self):
        initial_data = getattr(self, "initial_data", {}) or {}
        raw_value = initial_data.get("premium_slot")
        return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _category_requires_limit_subscription(self, category):
        if not category:
            return False
        return (
            is_premium_limit_category_name(getattr(category, "name", ""))
            or bool(getattr(category, "is_limit_subscription_premium", False))
        )

    def _mark_category_as_premium(self, category):
        if not category:
            return
        should_mark = self._premium_slot_requested() or is_premium_limit_category_name(
            getattr(category, "name", "")
        )
        if should_mark and not category.is_limit_subscription_premium:
            category.is_limit_subscription_premium = True
            category.save(update_fields=["is_limit_subscription_premium"])

    def _resolve_period_fields(self, attrs):
        instance = getattr(self, "instance", None)
        period_type = attrs.get("period_type") or getattr(instance, "period_type", "month")
        current_start = attrs.get("start_date")
        current_end = attrs.get("end_date")

        if instance is not None:
            current_start = current_start or getattr(instance, "start_date", None)
            current_end = current_end or getattr(instance, "end_date", None)

        should_refresh_range = (
            instance is None
            or "period_type" in attrs
            or current_start is None
            or current_end is None
        )

        if should_refresh_range or period_type == "custom":
            resolved_start, resolved_end = get_budget_date_range(
                period_type,
                start_date=current_start,
                end_date=current_end,
            )
            attrs["start_date"] = resolved_start
            attrs["end_date"] = resolved_end

        return period_type, attrs.get("start_date"), attrs.get("end_date")

    def validate(self, attrs):
        attrs = dict(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        category = attrs.get("category") or getattr(self.instance, "category", None)
        instance = getattr(self, "instance", None)

        if user and not getattr(user, "is_anonymous", True):
            subscription_active = sync_limit_subscription_state(user)
        else:
            subscription_active = False

        period_type, start_date, end_date = self._resolve_period_fields(attrs)

        if (
            user
            and not getattr(user, "is_anonymous", True)
            and (
                self._category_requires_limit_subscription(category)
                or (self._premium_slot_requested() and category is not None)
            )
            and not subscription_active
        ):
            raise serializers.ValidationError(
                {"category": "Limit subscription is required for this category."}
            )

        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be on or after start date."}
            )

        today = timezone.localdate()
        if (
            user
            and not getattr(user, "is_anonymous", True)
            and category is not None
            and start_date
            and end_date
            and start_date <= today <= end_date
        ):
            active_conflicts = Budget.objects.filter(
                user=user,
                category=category,
                start_date__lte=today,
                end_date__gte=today,
            )
            if instance is not None:
                active_conflicts = active_conflicts.exclude(pk=instance.pk)
            if active_conflicts.exists():
                raise serializers.ValidationError(
                    {
                        "category": (
                            "An active limit for this category already exists. "
                            "Edit the current limit instead of creating a new one."
                        )
                    }
                )

        return attrs

    def _expense_total(self, obj):
        base_queryset = Transaction.objects.filter(
            user_id=obj.user_id,
            type="expense",
            date__range=(obj.start_date, obj.end_date),
            category_id=obj.category_id,
        )

        try:
            return (
                exclude_debt_related_transactions(base_queryset)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal(0)
            )
        except (ProgrammingError, OperationalError):
            logger.warning(
                "Debt history links are unavailable while computing budget %s totals.",
                getattr(obj, "pk", None),
                exc_info=True,
            )
            return base_queryset.aggregate(total=Sum("amount"))["total"] or Decimal(0)

    def get_spent(self, obj):
        return self._expense_total(obj)

    def get_remaining(self, obj):
        remaining = obj.limit - self._expense_total(obj)
        return float(remaining)

    def get_percent_used(self, obj):
        spent = self._expense_total(obj)
        if not obj.limit or obj.limit == 0:
            return 0
        percent = (spent / obj.limit) * Decimal(100)
        return round(float(percent), 1)

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            raise serializers.ValidationError("Authentication required")
        validated_data.setdefault("period_type", "month")
        budget = Budget.objects.create(user=user, **validated_data)
        self._mark_category_as_premium(budget.category)
        return budget

    def update(self, instance, validated_data):
        budget = super().update(instance, validated_data)
        self._mark_category_as_premium(budget.category)
        return budget

# ---------------- DEBT ----------------
class DebtSerializer(serializers.ModelSerializer):
    wallet_name = serializers.CharField(source="wallet.name", read_only=True)

    class Meta:
        model = Debt
        fields = [
            "id",
            "user",
            "wallet",
            "wallet_name",
            "type",
            "counterparty",
            "amount",
            "issued_date",
            "issued_time",
            "due_date",
            "due_time",
            "returned",
            "issued_transaction",
            "returned_transaction",
        ]
        read_only_fields = ["user", "wallet_name", "issued_transaction", "returned_transaction"]

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        instance = getattr(self, "instance", None)

        wallet = attrs.get("wallet")
        if wallet is None and instance is not None:
            wallet = instance.wallet

        if wallet and user and wallet.user_id != user.id:
            raise serializers.ValidationError({"wallet": "Invalid wallet."})

        if instance is None and wallet is None:
            raise serializers.ValidationError({"wallet": "Wallet is required."})

        issued_date = attrs.get("issued_date") or getattr(instance, "issued_date", None)
        issued_time = attrs.get("issued_time") or getattr(instance, "issued_time", None) or datetime.min.time()
        due_date = attrs.get("due_date") or getattr(instance, "due_date", None)
        due_time = attrs.get("due_time") or getattr(instance, "due_time", None) or datetime.min.time()

        if issued_date and due_date:
            issued_at = datetime.combine(issued_date, issued_time)
            due_at = datetime.combine(due_date, due_time)
            if due_at < issued_at:
                raise serializers.ValidationError(
                    {"due_date": "Due date/time must be later than given date/time."}
                )

        return attrs

# ---------------- CATEGORY ----------------
class CategorySerializer(serializers.ModelSerializer):
    def validate_name(self, value):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        instance = getattr(self, "instance", None)
        cleaned_value = str(value or "").strip()

        if not cleaned_value:
            raise serializers.ValidationError("Category name is required.")

        current_name = normalize_limit_category_name(getattr(instance, "name", ""))
        next_name = normalize_limit_category_name(cleaned_value)
        if instance and current_name == next_name:
            return cleaned_value

        _enforce_custom_expense_category_limit(
            user,
            cleaned_value,
            field_name="name",
            exclude_category_id=getattr(instance, "id", None),
        )
        return cleaned_value

    class Meta:
        model = Category
        fields = ["id", "name"]


class SupportTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "subject",
            "message",
            "status",
            "admin_reply",
            "created_at",
            "updated_at",
            "answered_at",
        ]
        read_only_fields = ["status", "admin_reply", "created_at", "updated_at", "answered_at"]


class SupportChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportChatMessage
        fields = ["id", "sender", "message", "created_at"]
        read_only_fields = ["sender", "created_at"]
