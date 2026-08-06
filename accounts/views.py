# accounts/views.py
from datetime import date
from datetime import timedelta
from decimal import Decimal
import random
import csv
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import OperationalError, ProgrammingError
from django.db.models import Sum, Q, F, Count
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler
import logging

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        import traceback
        logger.error("Unhandled API exception: %s\n%s", exc, traceback.format_exc())
        return Response(
            {"detail": f"Internal server error: {exc}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    return response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from .models import (
    User,
    Wallet,
    Transaction,
    Budget,
    Debt,
    Category,
    EmailOTP,
    SupportTicket,
    SupportChatMessage,
    UserLoginActivity,
    exclude_debt_related_transactions,
    is_premium_limit_category_name,
)
from .serializers import (
    UserSerializer,
    WalletSerializer,
    TransactionSerializer,
    BudgetSerializer,
    DebtSerializer,
    CategorySerializer,
    SupportTicketSerializer,
    SupportChatMessageSerializer,
)
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView


def generate_otp_code():
    return f"{random.randint(100000, 999999)}"


def send_otp_email(email, code, purpose):
    subject = "Email verification code" if purpose == "verify" else "Password reset code"
    if purpose == "verify":
        body = f"Your verification code is: {code}\nThis code expires in 10 minutes."
    else:
        body = f"Your password reset code is: {code}\nThis code expires in 10 minutes."
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=True,
        )
    except Exception as exc:
        logger.warning("Failed sending OTP email to %s: %s", email, exc)


def create_and_send_otp(email, purpose, user=None):
    EmailOTP.objects.filter(email=email, purpose=purpose).delete()
    code = generate_otp_code()
    otp = EmailOTP.objects.create(
        email=email,
        code=code,
        purpose=purpose,
        user=user,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    send_otp_email(email, code, purpose)
    return otp


def _get_client_ip(request):
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR") or "").strip() or None


def _get_user_agent(request):
    return str(request.META.get("HTTP_USER_AGENT") or "").strip()[:255]


def _open_login_activity(user, request, source):
    try:
        UserLoginActivity.objects.create(
            user=user,
            source=source,
            ip_address=_get_client_ip(request),
            user_agent=_get_user_agent(request),
            last_seen=timezone.now(),
        )
    except (ProgrammingError, OperationalError):
        return None


def _touch_login_activity(user):
    try:
        activity = (
            UserLoginActivity.objects
            .filter(user=user, logout_at__isnull=True)
            .order_by("-login_at")
            .first()
        )
        if activity:
            activity.last_seen = timezone.now()
            activity.save(update_fields=["last_seen"])
    except (ProgrammingError, OperationalError):
        return None


def _close_login_activity(user):
    try:
        activity = (
            UserLoginActivity.objects
            .filter(user=user, logout_at__isnull=True)
            .order_by("-login_at")
            .first()
        )
        if activity:
            now = timezone.now()
            activity.logout_at = now
            activity.last_seen = now
            activity.save(update_fields=["logout_at", "last_seen"])
    except (ProgrammingError, OperationalError):
        return None


def _is_staff(user):
    return bool(user and user.is_authenticated and user.is_staff)


def _is_support_agent(user):
    return bool(user and user.is_authenticated and getattr(user, "is_support_agent", False))


def _can_access_support_desk(user):
    return bool(user and user.is_authenticated and (user.is_staff or getattr(user, "is_support_agent", False)))


class IsSupportAgentOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return _can_access_support_desk(getattr(request, "user", None))


class IsFullAdminUser(BasePermission):
    def has_permission(self, request, view):
        return _is_staff(getattr(request, "user", None))


def _clear_limit_subscription_challenge(user):
    user.limit_subscription_challenge = ""
    user.limit_subscription_answer = None
    user.limit_subscription_challenge_expires_at = None


def _limit_subscription_payload(user):
    return {
        "is_limit_subscription_active": bool(user.is_limit_subscription_active),
        "limit_subscription_started_at": user.limit_subscription_started_at,
        "limit_subscription_cancelled_at": user.limit_subscription_cancelled_at,
    }


def _build_limit_subscription_challenge():
    operator = random.choice(["+", "-", "*"])
    left = random.randint(7, 24)
    right = random.randint(2, 9)

    if operator == "-":
        left, right = max(left, right) + 4, min(left, right)
        answer = left - right
    elif operator == "*":
        answer = left * right
    else:
        answer = left + right

    return f"{left} {operator} {right}", answer


def _category_requires_limit_subscription(category):
    if not category:
        return False
    return (
        is_premium_limit_category_name(getattr(category, "name", ""))
        or bool(getattr(category, "is_limit_subscription_premium", False))
    )


def _get_visible_budgets(user, queryset):
    budgets = list(queryset)
    if user.is_limit_subscription_active:
        return budgets
    return [
        budget
        for budget in budgets
        if not _category_requires_limit_subscription(getattr(budget, "category", None))
    ]


def _safe_expense_queryset(queryset):
    try:
        return exclude_debt_related_transactions(queryset)
    except (ProgrammingError, OperationalError):
        return queryset


def _serialize_budget_summary(request, budgets):
    try:
        serialized = BudgetSerializer(
            budgets,
            many=True,
            context={"request": request},
        ).data
    except (ProgrammingError, OperationalError):
        return [], Decimal("0"), Decimal("0")

    total_limit = sum((budget.limit or Decimal("0") for budget in budgets), Decimal("0"))
    total_spent = sum(
        (Decimal(str(budget.get("spent") or 0)) for budget in serialized),
        Decimal("0"),
    )
    return serialized, total_limit, total_spent


def _build_top_category(expense_queryset, total_spent):
    leader = (
        expense_queryset
        .filter(category__isnull=False)
        .values("category__id", "category__name")
        .annotate(amount=Sum("amount"))
        .order_by("-amount", "category__name")
        .first()
    )
    if not leader:
        return None

    share_percent = 0.0
    if total_spent:
        share_percent = round(float((leader["amount"] / total_spent) * Decimal("100")), 1)

    return {
        "id": leader["category__id"],
        "name": leader["category__name"],
        "amount": leader["amount"] or Decimal("0"),
        "share_percent": share_percent,
    }


@require_http_methods(["GET"])
def root_entry_view(request):
    if _is_staff(request.user):
        return redirect("admin_dashboard")
    if _is_support_agent(request.user):
        return redirect("admin_tickets")
    return redirect("admin_login")

class HomeView(APIView):
    def get(self, request):
        return Response({"message": "Welcome to your dashboard!"})



# =========================
# Register
# =========================
@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password")
        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=400)

        existing = User.objects.filter(email=email).first()
        if existing and existing.is_active:
            return Response({"detail": "User already exists."}, status=400)

        if existing and not existing.is_active:
            existing.set_password(password)
            existing.save(update_fields=["password"])
            create_and_send_otp(email, "verify", existing)
            return Response(
                {"detail": "Verification code sent to email.", "verification_required": True},
                status=200,
            )

        user = User.objects.create_user(email=email, password=password, is_active=False)
        create_and_send_otp(email, "verify", user)
        return Response(
            {"detail": "Verification code sent to email.", "verification_required": True},
            status=201,
        )

# =========================
# Login
# =========================
@method_decorator(csrf_exempt, name='dispatch')
class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            email = (request.data.get("email") or "").strip().lower()
            password = request.data.get("password")
            if not email or not password:
                return Response({"detail": "Email and password are required."}, status=400)

            user = User.objects.filter(email=email).first()
            if not user or not user.check_password(password):
                return Response({"detail": "Invalid credentials"}, status=401)

            if not user.is_active:
                return Response(
                    {"detail": "Email is not verified.", "verification_required": True},
                    status=403,
                )

            refresh = RefreshToken.for_user(user)
            _open_login_activity(user, request, source="api")
            return Response({
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return Response({"detail": f"Internal server error: {exc}"}, status=500)


class VerifyEmailCodeView(APIView):
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        code = (request.data.get("code") or "").strip()
        if not email or not code:
            return Response({"detail": "Email and code are required."}, status=400)

        otp = (
            EmailOTP.objects.filter(email=email, purpose="verify")
            .order_by("-created_at")
            .first()
        )
        if not otp:
            return Response({"detail": "Verification code not found."}, status=400)
        if otp.expires_at < timezone.now():
            return Response({"detail": "Verification code expired."}, status=400)
        if otp.code != code:
            return Response({"detail": "Invalid verification code."}, status=400)

        user = User.objects.filter(email=email).first()
        if not user:
            return Response({"detail": "User not found."}, status=404)

        user.is_active = True
        user.save(update_fields=["is_active"])
        Wallet.objects.get_or_create(user=user, name="Cash", defaults={"balance": 0})
        Wallet.objects.get_or_create(user=user, name="Card", defaults={"balance": 0})
        EmailOTP.objects.filter(email=email, purpose="verify").delete()

        refresh = RefreshToken.for_user(user)
        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)},
            status=200,
        )


class ResendVerificationCodeView(APIView):
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "Email is required."}, status=400)
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({"detail": "User not found."}, status=404)
        if user.is_active:
            return Response({"detail": "Email already verified."}, status=400)
        create_and_send_otp(email, "verify", user)
        return Response({"detail": "Verification code resent."}, status=200)


class ForgotPasswordRequestView(APIView):
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "Email is required."}, status=400)
        user = User.objects.filter(email=email).first()
        if user:
            create_and_send_otp(email, "reset", user)
        # Do not leak whether user exists
        return Response({"detail": "If account exists, reset code has been sent."}, status=200)


class ForgotPasswordConfirmView(APIView):
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        code = (request.data.get("code") or "").strip()
        new_password = request.data.get("new_password")
        if not email or not code or not new_password:
            return Response({"detail": "Email, code and new password are required."}, status=400)

        otp = (
            EmailOTP.objects.filter(email=email, purpose="reset")
            .order_by("-created_at")
            .first()
        )
        if not otp:
            return Response({"detail": "Reset code not found."}, status=400)
        if otp.expires_at < timezone.now():
            return Response({"detail": "Reset code expired."}, status=400)
        if otp.code != code:
            return Response({"detail": "Invalid reset code."}, status=400)

        user = User.objects.filter(email=email).first()
        if not user:
            return Response({"detail": "User not found."}, status=404)
        user.set_password(new_password)
        user.save(update_fields=["password"])
        EmailOTP.objects.filter(email=email, purpose="reset").delete()
        return Response({"detail": "Password updated successfully."}, status=200)

# =========================
# Finance Dashboard

class FinanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        today = timezone.localdate()

        transactions = _safe_expense_queryset(Transaction.objects.filter(
            user=user,
            date__year=today.year,
            date__month=today.month
        ))

        
        income = (
            transactions.filter(type="income")
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        
        expenses = (
            transactions.filter(type="expense")
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        
        category_qs = (
            transactions.filter(type="expense", category__isnull=False)
            .values("category__id", "category__name")
            .annotate(amount=Sum("amount"))
        )

        categories = [
            {
                "id": c["category__id"],
                "name": c["category__name"],
                "amount": c["amount"] or 0
            }
            for c in category_qs
        ]

        # ---------------- WALLETS ----------------
        wallets = WalletSerializer(
            Wallet.objects.filter(user=user),
            many=True
        ).data

        # ---------------- Limits ----------------
        budgets_qs = Budget.objects.filter(
            user=user,
            start_date__lte=today,
            end_date__gte=today,
        ).select_related("category")
        visible_budgets = _get_visible_budgets(user, budgets_qs)
        budgets, total_limit, total_spent = _serialize_budget_summary(
            request,
            visible_budgets,
        )

        # ---------------- DEBTS ----------------
        debts = DebtSerializer(
            Debt.objects.filter(user=user),
            many=True
        ).data

        return Response({
            "income": income,
            "expenses": expenses,
            "wallets": wallets,
            "categories": categories,
            "budgets": budgets,
            "total_limit": total_limit,
            "total_spent": total_spent,
            "debts": debts
        })


# =========================
# Wallet CRUD 
# =========================
class WalletCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WalletSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class WalletDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return Wallet.objects.filter(pk=pk, user=self.request.user).first()

    def get(self, request, pk):
        wallet = self.get_object(pk)
        if not wallet:
            return Response({"detail": "Not found"}, status=404)
        return Response(WalletSerializer(wallet).data)

    def patch(self, request, pk):
        wallet = self.get_object(pk)
        if not wallet:
            return Response({"detail": "Not found"}, status=404)
        serializer = WalletSerializer(wallet, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        wallet = self.get_object(pk)
        if not wallet:
            return Response({"detail": "Not found"}, status=404)
        wallet.delete()
        return Response(status=204)

# =========================
# Transaction CRUD
# =========================
class TransactionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Transaction.objects.filter(user=request.user)
        # Optional filtering by wallet and category for detail screens
        wallet_id = request.query_params.get("wallet")
        if wallet_id:
            qs = qs.filter(wallet_id=wallet_id)
        category_id = request.query_params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        qs = qs.order_by("-date", "-time")
        return Response(TransactionSerializer(qs, many=True).data)

    def post(self, request):
        serializer = TransactionSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            transaction = serializer.save()
            wallet = transaction.wallet
            if transaction.type == "income":
                wallet.balance += transaction.amount
            else:
                wallet.balance -= transaction.amount
            wallet.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class TransactionDetailView(APIView):
    """
    Detail view for single transaction.
    Used by the mobile client to delete a transaction.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        return Transaction.objects.filter(pk=pk, user=user).first()

    def delete(self, request, pk):
        transaction = self.get_object(pk, request.user)
        if not transaction:
            return Response({"detail": "Not found"}, status=404)
        # Roll back wallet balance according to transaction type
        wallet = transaction.wallet
        if transaction.type == "income":
            wallet.balance -= transaction.amount
        else:
            wallet.balance += transaction.amount
        wallet.save()
        transaction.delete()
        return Response(status=204)

# =========================
# Budget CRUD
# =========================
class BudgetCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BudgetSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class BudgetDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        budget = Budget.objects.filter(pk=pk, user=request.user).first()
        if not budget:
            return Response({"detail": "Not found"}, status=404)
        serializer = BudgetSerializer(budget, data=request.data, partial=True, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        budget = Budget.objects.filter(pk=pk, user=request.user).first()
        if not budget:
            return Response({"detail": "Not found"}, status=404)
        budget.delete()
        return Response(status=204)


class BudgetHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period_type = str(request.query_params.get("period_type") or "month").strip().casefold()
        valid_period_types = {choice[0] for choice in Budget.PERIOD_CHOICES}
        if period_type not in valid_period_types:
            return Response({"detail": "Unsupported period type."}, status=400)

        raw_limit = str(request.query_params.get("limit") or "6").strip()
        try:
            history_limit = int(raw_limit)
        except ValueError:
            return Response({"detail": "Limit must be a whole number."}, status=400)

        if history_limit < 1:
            return Response({"detail": "Limit must be at least 1."}, status=400)

        budgets_qs = Budget.objects.filter(
            user=request.user,
            period_type=period_type,
        ).select_related("category")
        visible_budgets = _get_visible_budgets(request.user, budgets_qs)

        period_totals = {}
        for budget in visible_budgets:
            key = (budget.start_date, budget.end_date)
            period_totals[key] = period_totals.get(key, Decimal("0")) + (budget.limit or Decimal("0"))

        history = []
        for start_date, end_date in sorted(period_totals.keys(), reverse=True)[:history_limit]:
            expense_queryset = _safe_expense_queryset(
                Transaction.objects.filter(
                    user=request.user,
                    type="expense",
                    date__range=(start_date, end_date),
                )
            )
            total_spent = expense_queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            history.append(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "total_limit": period_totals[(start_date, end_date)],
                    "total_spent": total_spent,
                    "top_category": _build_top_category(expense_queryset, total_spent),
                }
            )

        return Response(
            {
                "period_type": period_type,
                "history": history,
            }
        )


class LimitSubscriptionChallengeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()

        if user.is_limit_subscription_active:
            return Response(
                {
                    **_limit_subscription_payload(user),
                    "detail": "Limit subscription is already active.",
                }
            )

        has_active_challenge = (
            user.limit_subscription_challenge
            and user.limit_subscription_answer is not None
            and user.limit_subscription_challenge_expires_at
            and user.limit_subscription_challenge_expires_at > now
        )

        if not has_active_challenge:
            question, answer = _build_limit_subscription_challenge()
            user.limit_subscription_challenge = question
            user.limit_subscription_answer = answer
            user.limit_subscription_challenge_expires_at = now + timedelta(minutes=10)
            user.save(
                update_fields=[
                    "limit_subscription_challenge",
                    "limit_subscription_answer",
                    "limit_subscription_challenge_expires_at",
                ]
            )

        return Response(
            {
                **_limit_subscription_payload(user),
                "question": user.limit_subscription_challenge,
                "expires_at": user.limit_subscription_challenge_expires_at,
            }
        )


class LimitSubscriptionActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.is_limit_subscription_active:
            return Response(
                {
                    **_limit_subscription_payload(user),
                    "detail": "Limit subscription is already active.",
                }
            )

        raw_answer = str(request.data.get("answer") or "").strip()
        if not raw_answer:
            return Response({"detail": "Answer is required."}, status=400)

        if (
            not user.limit_subscription_challenge
            or user.limit_subscription_answer is None
            or not user.limit_subscription_challenge_expires_at
        ):
            return Response(
                {"detail": "Challenge not found. Request a new one."},
                status=400,
            )

        if user.limit_subscription_challenge_expires_at <= timezone.now():
            _clear_limit_subscription_challenge(user)
            user.save(
                update_fields=[
                    "limit_subscription_challenge",
                    "limit_subscription_answer",
                    "limit_subscription_challenge_expires_at",
                ]
            )
            return Response(
                {"detail": "Challenge expired. Request a new one."},
                status=400,
            )

        try:
            answer = int(raw_answer)
        except ValueError:
            return Response({"detail": "Answer must be a whole number."}, status=400)

        if answer != user.limit_subscription_answer:
            _clear_limit_subscription_challenge(user)
            user.save(
                update_fields=[
                    "limit_subscription_challenge",
                    "limit_subscription_answer",
                    "limit_subscription_challenge_expires_at",
                ]
            )
            return Response(
                {"detail": "Incorrect answer. Subscription was not activated."},
                status=400,
            )

        now = timezone.now()
        user.is_limit_subscription_active = True
        user.limit_subscription_started_at = now
        user.limit_subscription_cancelled_at = None
        _clear_limit_subscription_challenge(user)
        user.save(
            update_fields=[
                "is_limit_subscription_active",
                "limit_subscription_started_at",
                "limit_subscription_cancelled_at",
                "limit_subscription_challenge",
                "limit_subscription_answer",
                "limit_subscription_challenge_expires_at",
            ]
        )
        return Response(
            {
                **_limit_subscription_payload(user),
                "detail": "Limit subscription activated.",
            }
        )


class LimitSubscriptionCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.is_limit_subscription_active:
            user.is_limit_subscription_active = False
            user.limit_subscription_cancelled_at = timezone.now()

        _clear_limit_subscription_challenge(user)
        user.save(
            update_fields=[
                "is_limit_subscription_active",
                "limit_subscription_cancelled_at",
                "limit_subscription_challenge",
                "limit_subscription_answer",
                "limit_subscription_challenge_expires_at",
            ]
        )

        return Response(
            {
                **_limit_subscription_payload(user),
                "detail": "Limit subscription cancelled.",
            }
        )

# =========================
# Debt CRUD
# =========================
class DebtCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        debts = Debt.objects.filter(user=request.user).order_by("returned", "due_date")
        return Response(DebtSerializer(debts, many=True).data)

    def post(self, request):
        serializer = DebtSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class DebtDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        return Debt.objects.filter(pk=pk, user=user).first()

    def patch(self, request, pk):
        debt = self.get_object(pk, request.user)
        if not debt:
            return Response({"detail": "Not found"}, status=404)
        serializer = DebtSerializer(debt, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        debt = self.get_object(pk, request.user)
        if not debt:
            return Response({"detail": "Not found"}, status=404)
        debt.delete()
        return Response(status=204)

# =========================
# Category CRUD
# =========================
class CategoryCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = Category.objects.filter(user=request.user)
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CategorySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class CategoryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        category = Category.objects.filter(pk=pk, user=request.user).first()
        if not category:
            return Response({"detail": "Not found"}, status=404)
        serializer = CategorySerializer(category)
        return Response(serializer.data)

    def patch(self, request, pk):
        category = Category.objects.filter(pk=pk, user=request.user).first()
        if not category:
            return Response({"detail": "Not found"}, status=404)
        serializer = CategorySerializer(
            category, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        category = Category.objects.filter(pk=pk, user=request.user).first()
        if not category:
            return Response({"detail": "Not found"}, status=404)
        category.delete()
        return Response(status=204)


class SupportTicketView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            tickets = SupportTicket.objects.filter(user=request.user).order_by("-created_at")
            return Response(SupportTicketSerializer(tickets, many=True).data)
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "Support service is not initialized. Run migrations."},
                status=503,
            )

    def post(self, request):
        serializer = SupportTicketSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        message = serializer.validated_data.get("message", "").strip()
        subject = serializer.validated_data.get("subject", "").strip()
        if not message:
            return Response({"message": ["This field may not be blank."]}, status=400)

        try:
            ticket = SupportTicket.objects.create(
                user=request.user,
                subject=subject,
                message=message,
            )
            # Mirror ticket creation into chat timeline for unified support flow.
            SupportChatMessage.objects.create(
                user=request.user,
                sender="user",
                message=message,
            )
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "Support service is not initialized. Run migrations."},
                status=503,
            )

        return Response(SupportTicketSerializer(ticket).data, status=201)


class SupportTicketDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        try:
            ticket = SupportTicket.objects.filter(pk=pk, user=request.user).first()
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "Support service is not initialized. Run migrations."},
                status=503,
            )
        if not ticket:
            return Response({"detail": "Not found"}, status=404)

        if request.data.get("action") == "close":
            ticket.status = "closed"
            ticket.save(update_fields=["status", "updated_at"])
            return Response(SupportTicketSerializer(ticket).data)

        return Response({"detail": "Invalid action"}, status=400)


class SupportChatMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            messages = SupportChatMessage.objects.filter(user=request.user).order_by("created_at")
            return Response(SupportChatMessageSerializer(messages, many=True).data)
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "Support service is not initialized. Run migrations."},
                status=503,
            )

    def post(self, request):
        text = str(request.data.get("message") or "").strip()
        if not text:
            return Response({"message": ["This field may not be blank."]}, status=400)
        try:
            latest_ticket = (
                SupportTicket.objects
                .filter(user=request.user)
                .order_by("-updated_at", "-created_at")
                .first()
            )
            if not latest_ticket or latest_ticket.status in {"closed", "answered"}:
                SupportTicket.objects.create(
                    user=request.user,
                    subject="Chat",
                    message=text,
                )
            msg = SupportChatMessage.objects.create(
                user=request.user,
                sender="user",
                message=text,
            )
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "Support service is not initialized. Run migrations."},
                status=503,
            )

        return Response(SupportChatMessageSerializer(msg).data, status=201)


class AdminSupportTicketView(APIView):
    permission_classes = [IsSupportAgentOrAdmin]

    def get(self, request):
        status_filter = str(request.query_params.get("status") or "").strip()
        user_id_filter = str(request.query_params.get("user_id") or "").strip()
        tickets = SupportTicket.objects.select_related("user").order_by("-updated_at", "-created_at")
        if status_filter:
            tickets = tickets.filter(status=status_filter)
        if user_id_filter.isdigit():
            tickets = tickets.filter(user_id=int(user_id_filter))

        data = SupportTicketSerializer(tickets, many=True).data
        for idx, ticket in enumerate(tickets):
            data[idx]["user_id"] = ticket.user_id
            data[idx]["user_email"] = ticket.user.email
        return Response(data)


class AdminSupportTicketDetailView(APIView):
    permission_classes = [IsSupportAgentOrAdmin]

    def patch(self, request, pk):
        ticket = SupportTicket.objects.select_related("user").filter(pk=pk).first()
        if not ticket:
            return Response({"detail": "Not found"}, status=404)

        status_value = str(request.data.get("status") or "").strip()
        reply_value = str(request.data.get("admin_reply") or "").strip()
        changed_fields = []

        if status_value and status_value in {"open", "in_progress", "answered", "closed"}:
            ticket.status = status_value
            changed_fields.append("status")

        if reply_value:
            ticket.admin_reply = reply_value
            if ticket.status != "answered":
                ticket.status = "answered"
                if "status" not in changed_fields:
                    changed_fields.append("status")
            ticket.answered_at = timezone.now()
            changed_fields.extend(["admin_reply", "answered_at"])

        if not changed_fields:
            return Response({"detail": "No valid fields to update"}, status=400)

        ticket.save(update_fields=list(dict.fromkeys(changed_fields + ["updated_at"])))
        payload = SupportTicketSerializer(ticket).data
        payload["user_id"] = ticket.user_id
        payload["user_email"] = ticket.user.email
        return Response(payload)


class AdminSupportChatView(APIView):
    permission_classes = [IsSupportAgentOrAdmin]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found"}, status=404)
        messages = SupportChatMessage.objects.filter(user_id=user_id).order_by("created_at")
        data = SupportChatMessageSerializer(messages, many=True).data
        return Response(
            {
                "user_id": user.id,
                "user_email": user.email,
                "messages": data,
            }
        )

    def post(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found"}, status=404)
        text = str(request.data.get("message") or "").strip()
        if not text:
            return Response({"message": ["This field may not be blank."]}, status=400)
        msg = SupportChatMessage.objects.create(
            user=user,
            sender="admin",
            message=text,
        )
        latest_ticket = (
            SupportTicket.objects
            .filter(user=user)
            .order_by("-updated_at", "-created_at")
            .first()
        )
        if latest_ticket and latest_ticket.status == "open":
            latest_ticket.status = "in_progress"
            latest_ticket.save(update_fields=["status", "updated_at"])
        return Response(SupportChatMessageSerializer(msg).data, status=201)


class AdminUserActivityView(APIView):
    permission_classes = [IsFullAdminUser]

    def get(self, request):
        try:
            now = timezone.now()
            online_threshold = now - timedelta(minutes=5)

            online_qs = (
                UserLoginActivity.objects
                .select_related("user")
                .filter(logout_at__isnull=True, last_seen__gte=online_threshold)
                .order_by("-last_seen")[:100]
            )
            online_users = [
                {
                    "user_id": x.user_id,
                    "email": x.user.email,
                    "source": x.source,
                    "login_at": x.login_at,
                    "last_seen": x.last_seen,
                    "ip_address": x.ip_address,
                }
                for x in online_qs
            ]

            recent_logins_qs = (
                UserLoginActivity.objects
                .select_related("user")
                .order_by("-login_at")[:200]
            )
            recent_logins = [
                {
                    "user_id": x.user_id,
                    "email": x.user.email,
                    "source": x.source,
                    "login_at": x.login_at,
                    "last_seen": x.last_seen,
                    "logout_at": x.logout_at,
                    "ip_address": x.ip_address,
                }
                for x in recent_logins_qs
            ]
        except (ProgrammingError, OperationalError):
            return Response(
                {"detail": "User activity tracking is not initialized. Run migrations."},
                status=503,
            )

        return Response(
            {
                "online_count": len(online_users),
                "online_users": online_users,
                "recent_logins": recent_logins,
            }
        )


class AdminSupportTicketExportView(APIView):
    permission_classes = [IsFullAdminUser]

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename=\"support_tickets.csv\"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "id",
                "user_id",
                "user_email",
                "subject",
                "status",
                "message",
                "admin_reply",
                "created_at",
                "updated_at",
                "answered_at",
            ]
        )

        tickets = SupportTicket.objects.select_related("user").order_by("-created_at")
        for ticket in tickets:
            writer.writerow(
                [
                    ticket.id,
                    ticket.user_id,
                    ticket.user.email,
                    ticket.subject,
                    ticket.status,
                    ticket.message,
                    ticket.admin_reply,
                    ticket.created_at.isoformat() if ticket.created_at else "",
                    ticket.updated_at.isoformat() if ticket.updated_at else "",
                    ticket.answered_at.isoformat() if ticket.answered_at else "",
                ]
            )
        return response


class AdminUsersView(APIView):
    permission_classes = [IsFullAdminUser]

    def get(self, request):
        q = str(request.query_params.get("q") or "").strip().lower()
        users = User.objects.all().order_by("-date_joined")
        if q:
            users = users.filter(
                Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(phone__icontains=q)
            )

        users = users.annotate(
            tickets_count=Count("support_tickets", distinct=True),
            messages_count=Count("support_messages", distinct=True),
        )[:500]

        data = []
        for u in users:
            data.append(
                {
                    "id": u.id,
                    "email": u.email,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "phone": u.phone,
                    "profile_photo": u.profile_photo.url if u.profile_photo else None,
                    "is_support_agent": u.is_support_agent,
                    "is_staff": u.is_staff,
                    "is_active": u.is_active,
                    "date_joined": u.date_joined,
                    "last_login": u.last_login,
                    "tickets_count": getattr(u, "tickets_count", 0),
                    "messages_count": getattr(u, "messages_count", 0),
                }
            )
        return Response(data)


class AdminUserDetailView(APIView):
    permission_classes = [IsFullAdminUser]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found"}, status=404)

        recent_tickets = (
            SupportTicket.objects.filter(user=user)
            .order_by("-created_at")[:20]
        )
        recent_messages = (
            SupportChatMessage.objects.filter(user=user)
            .order_by("-created_at")[:30]
        )

        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "phone": user.phone,
                    "profile_photo": user.profile_photo.url if user.profile_photo else None,
                    "is_support_agent": user.is_support_agent,
                    "is_staff": user.is_staff,
                    "is_active": user.is_active,
                    "date_joined": user.date_joined,
                    "last_login": user.last_login,
                },
                "recent_tickets": SupportTicketSerializer(recent_tickets, many=True).data,
                "recent_messages": SupportChatMessageSerializer(recent_messages, many=True).data,
            }
        )


@require_http_methods(["GET", "POST"])
def admin_login_view(request):
    if _is_staff(request.user):
        return redirect("admin_dashboard")
    if _is_support_agent(request.user):
        return redirect("admin_tickets")

    error = ""
    if request.method == "POST":
        email = str(request.POST.get("email") or "").strip().lower()
        password = str(request.POST.get("password") or "")
        user = authenticate(request, email=email, password=password)
        if user and user.is_staff:
            auth_login(request, user)
            _open_login_activity(user, request, source="web_admin")
            return redirect("admin_dashboard")
        error = "Неверные данные или нет доступа администратора."

    return render(request, "admin_login.html", {"error": error})


@login_required(login_url="/admin-login/")
@user_passes_test(_is_staff, login_url="/admin-login/")
def admin_dashboard_view(request):
    return render(request, "admin_overview.html")


@login_required(login_url="/support/login/")
@user_passes_test(_can_access_support_desk, login_url="/support/login/")
def admin_tickets_view(request):
    return render(
        request,
        "admin_tickets.html",
        {"support_only": not request.user.is_staff},
    )


@login_required(login_url="/admin-login/")
@user_passes_test(_is_staff, login_url="/admin-login/")
def admin_activity_view(request):
    return render(request, "admin_activity.html")


@login_required(login_url="/admin-login/")
@user_passes_test(_is_staff, login_url="/admin-login/")
def admin_users_view(request):
    return render(request, "admin_users.html")


@require_http_methods(["POST", "GET"])
def admin_logout_view(request):
    if request.user.is_authenticated:
        _close_login_activity(request.user)
    auth_logout(request)
    return redirect("admin_login")


@require_http_methods(["GET", "POST"])
def support_login_view(request):
    if request.user.is_authenticated:
        if _can_access_support_desk(request.user):
            return redirect("admin_tickets")
        return redirect("support_portal")

    error = ""
    if request.method == "POST":
        email = str(request.POST.get("email") or "").strip().lower()
        password = str(request.POST.get("password") or "")
        user_by_email = User.objects.filter(email=email).first()

        if user_by_email and not user_by_email.is_active:
            error = "Email әлі расталмаған. Алдымен аккаунтты verify етіп алыңыз."
            return render(request, "support_login.html", {"error": error})

        user = authenticate(request, email=email, password=password)
        if user and _can_access_support_desk(user):
            auth_login(request, user)
            _open_login_activity(user, request, source="web_admin")
            return redirect("admin_tickets")
        if user:
            error = "Бұл логин тек техподдержка қызметкерлеріне арналған."
        else:
            error = "Неверные учетные данные пользователя."

    return render(request, "support_login.html", {"error": error})


@login_required(login_url="/support/login/")
def support_portal_view(request):
    if _can_access_support_desk(request.user):
        return redirect("admin_tickets")

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()
        if action == "create_ticket":
            subject = str(request.POST.get("subject") or "").strip()
            message = str(request.POST.get("message") or "").strip()
            if message:
                SupportTicket.objects.create(user=request.user, subject=subject, message=message)
        elif action == "send_chat":
            text = str(request.POST.get("chat_message") or "").strip()
            if text:
                SupportChatMessage.objects.create(user=request.user, sender="user", message=text)
        elif action == "close_ticket":
            ticket_id = str(request.POST.get("ticket_id") or "").strip()
            if ticket_id.isdigit():
                ticket = SupportTicket.objects.filter(pk=int(ticket_id), user=request.user).first()
                if ticket:
                    ticket.status = "closed"
                    ticket.save(update_fields=["status", "updated_at"])
        return redirect("support_portal")

    tickets = SupportTicket.objects.filter(user=request.user).order_by("-created_at")
    messages = SupportChatMessage.objects.filter(user=request.user).order_by("created_at")
    _touch_login_activity(request.user)
    return render(
        request,
        "support_portal.html",
        {
            "tickets": tickets,
            "messages": messages,
        },
    )


@require_http_methods(["POST", "GET"])
def support_logout_view(request):
    if request.user.is_authenticated:
        _close_login_activity(request.user)
    auth_logout(request)
    return redirect("support_login")

# =========================
# Logout
# =========================
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("refresh")
        if not token:
            return Response({"detail": "Refresh token required"}, status=400)
        try:
            refresh_token = RefreshToken(token)
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=400)

        blacklist = getattr(refresh_token, "blacklist", None)
        if callable(blacklist):
            try:
                blacklist()
            except (ProgrammingError, OperationalError):
                return Response(
                    {"detail": "Token blacklist is not initialized. Run migrations."},
                    status=503,
                )

        _close_login_activity(request.user)
        return Response({"detail": "Logged out"})

# =========================
# User Profile
# =========================
@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def user_profile(request):
    if request.method == "PATCH":
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)
    serializer = UserSerializer(request.user)
    return Response(serializer.data)
