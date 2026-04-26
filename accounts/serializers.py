from rest_framework import serializers
from django.db.models import Sum, Q
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import (
    Wallet,
    Transaction,
    Budget,
    Debt,
    Category,
    SupportTicket,
    SupportChatMessage,
    is_premium_limit_category_name,
)
from dj_rest_auth.registration.serializers import RegisterSerializer
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone

User = get_user_model()

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
                category_obj, _ = Category.objects.get_or_create(user=user, name=data)
                return category_obj
            else:
                raise serializers.ValidationError({"category": "Invalid category type"})

# ---------------- USER ----------------
class UserSerializer(serializers.ModelSerializer):
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
            "limit_subscription_cancelled_at",
        ]
        extra_kwargs = {"password": {"write_only": True}}
        read_only_fields = [
            "id",
            "email",
            "is_limit_subscription_active",
            "limit_subscription_started_at",
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

    def get_over_limit(self, obj):
        if obj.type != "expense":
            return False
        budget = Budget.objects.filter(
            user=obj.user,
            start_date__lte=obj.date,
            end_date__gte=obj.date
        ).filter(
            Q(category=obj.category) | Q(category__isnull=True)
        ).first()
        if not budget:
            return False
        spent = Transaction.objects.filter(
            user=obj.user,
            type="expense",
            date__range=(budget.start_date, budget.end_date)
        ).filter(
            Q(category=budget.category) | Q(category__isnull=True)
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
        return spent > budget.limit

    def get_percent_used(self, obj):
        if obj.type != "expense":
            return 0
        budget = Budget.objects.filter(
            user=obj.user,
            start_date__lte=obj.date,
            end_date__gte=obj.date
        ).filter(
            Q(category=obj.category) | Q(category__isnull=True)
        ).first()
        if not budget:
            return 0
        spent = Transaction.objects.filter(
            user=obj.user,
            type="expense",
            date__range=(budget.start_date, budget.end_date)
        ).filter(
            Q(category=budget.category) | Q(category__isnull=True)
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
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
    period_type = serializers.CharField(required=False)
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

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        category = attrs.get("category") or getattr(self.instance, "category", None)

        if (
            user
            and not getattr(user, "is_anonymous", True)
            and category
            and is_premium_limit_category_name(getattr(category, "name", ""))
            and not user.is_limit_subscription_active
        ):
            raise serializers.ValidationError(
                {"category": "Limit subscription is required for this category."}
            )

        return attrs

    def get_spent(self, obj):
        spent = Transaction.objects.filter(
            user=obj.user,
            type="expense",
            date__range=(obj.start_date, obj.end_date)
        ).filter(
            Q(category=obj.category) | Q(category__isnull=True)
        ).aggregate(total=Sum("amount"))["total"] or Decimal(0)
        return spent

    def get_remaining(self, obj):
        spent = self.get_spent(obj)
        remaining = obj.limit - spent
        return float(remaining)

    def get_percent_used(self, obj):
        spent = self.get_spent(obj)
        if not obj.limit or obj.limit == 0:
            return 0
        percent = (spent / obj.limit) * Decimal(100)
        return round(float(percent), 1)

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or user.is_anonymous:
            raise serializers.ValidationError("Authentication required")
        # Set defaults if not provided
        if 'period_type' not in validated_data:
            validated_data['period_type'] = 'monthly'  # Assuming 'monthly' is a valid choice
        if 'start_date' not in validated_data or 'end_date' not in validated_data:
            today = date.today()
            start_date = date(today.year, today.month, 1)
            end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
            validated_data['start_date'] = validated_data.get('start_date', start_date)
            validated_data['end_date'] = validated_data.get('end_date', end_date)
        return Budget.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)

# ---------------- DEBT ----------------
class DebtSerializer(serializers.ModelSerializer):
    class Meta:
        model = Debt
        fields = "__all__"
        read_only_fields = ["user", "returned"]

# ---------------- CATEGORY ----------------
class CategorySerializer(serializers.ModelSerializer):
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
