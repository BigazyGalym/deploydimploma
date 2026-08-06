from django.utils import timezone
from django.db import OperationalError, ProgrammingError
from rest_framework_simplejwt.tokens import AccessToken
from .models import User, UserLoginActivity


class UserActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._touch_session_user(request)
        self._touch_jwt_user(request)
        return self.get_response(request)

    def _touch_session_user(self, request):
        try:
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                return
            self._touch_or_open(user, source="web_admin" if user.is_staff else "web_user")
        except Exception:
            return

    def _touch_jwt_user(self, request):
        auth_header = str(request.META.get("HTTP_AUTHORIZATION") or "").strip()
        if not auth_header.startswith("Bearer "):
            return
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            return
        try:
            payload = AccessToken(token)
            user_id = payload.get("user_id")
            if not user_id:
                return
            user = User.objects.filter(pk=user_id).first()
            if not user:
                return
            self._touch_or_open(user, source="api")
        except Exception:
            return

    def _touch_or_open(self, user, source):
        now = timezone.now()
        activity = (
            UserLoginActivity.objects
            .filter(user=user, logout_at__isnull=True)
            .order_by("-login_at")
            .first()
        )
        if activity:
            activity.last_seen = now
            activity.save(update_fields=["last_seen"])
        else:
            UserLoginActivity.objects.create(
                user=user,
                source=source,
                last_seen=now,
            )
