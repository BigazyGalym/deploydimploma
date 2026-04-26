from datetime import date

from django.db import OperationalError, ProgrammingError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIAssistantMessage, Habit, Task, Tracker
from .tracker_ai import (
    build_ai_chat_reply,
    build_default_ai_messages,
    build_quick_prompts,
    ensure_ai_messages_seeded,
    generate_recommendations,
)
from .tracker_serializers import (
    AIAssistantMessageSerializer,
    HabitSerializer,
    TaskSerializer,
    TrackerSerializer,
)


def _get_request_language(request):
    raw_language = (
        request.headers.get("X-App-Language")
        or request.headers.get("Accept-Language")
        or "en"
    )
    language = str(raw_language).split(",")[0].strip().lower().split("-")[0]
    if language == "kk":
        language = "kz"
    return language if language in {"en", "ru", "kz"} else "en"


def _message_empty_error(language):
    return {
        "en": "Message cannot be empty.",
        "ru": "Сообщение не может быть пустым.",
        "kz": "Хабарлама бос болмауы керек.",
    }.get(language, "Message cannot be empty.")


def _filtered_queryset(request, model):
    queryset = model.objects.filter(user=request.user)
    user_id = str(request.query_params.get("user_id") or "").strip()
    if user_id:
        if request.user.is_staff:
            queryset = model.objects.filter(user_id=user_id)
        elif str(request.user.id) != user_id:
            queryset = model.objects.none()
    return queryset


def _build_serialized_ai_payload(ai_payload, messages, proactive_messages, language):
    return {
        "generated_at": ai_payload["generated_at"],
        "summary": ai_payload["summary"],
        "recommendations": ai_payload["recommendations"][:4],
        "tracker_trends": ai_payload["tracker_trends"][:5],
        "finance_snapshot": ai_payload.get("finance_snapshot") or {},
        "quick_prompts": build_quick_prompts(ai_payload, language=language),
        "messages": messages,
        "proactive_messages": proactive_messages,
    }


def _build_ai_chat_payload(user, language, extra_messages=None):
    ai_payload = generate_recommendations(user, language=language)
    default_messages = build_default_ai_messages(ai_payload, language=language)
    extra_messages = list(extra_messages or [])

    try:
        ensure_ai_messages_seeded(user, ai_payload, language=language)
        messages = AIAssistantMessage.objects.filter(user=user).order_by("created_at")
        proactive_messages = list(
            messages.filter(kind__in=["system", "proactive"]).order_by("-created_at")[:3]
        )[::-1]
        serialized_messages = AIAssistantMessageSerializer(messages, many=True).data
        serialized_proactive = AIAssistantMessageSerializer(proactive_messages, many=True).data
    except (OperationalError, ProgrammingError):
        serialized_messages = [*default_messages, *extra_messages]
        serialized_proactive = default_messages[:3]

    if extra_messages and serialized_messages[: len(default_messages)] != default_messages:
        serialized_messages = [*serialized_messages, *extra_messages]

    return _build_serialized_ai_payload(
        ai_payload,
        messages=serialized_messages,
        proactive_messages=serialized_proactive,
        language=language,
    )


class HabitListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = _filtered_queryset(request, Habit).order_by("name", "created_at")
        frequency = request.query_params.get("frequency")
        if frequency in {"daily", "weekly"}:
            queryset = queryset.filter(frequency=frequency)
        return Response(HabitSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = HabitSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class HabitDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        return get_object_or_404(Habit, pk=pk, user=request.user)

    def get(self, request, pk):
        habit = self.get_object(request, pk)
        return Response(HabitSerializer(habit).data)

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        habit = self.get_object(request, pk)
        serializer = HabitSerializer(habit, data=request.data, partial=partial)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        habit = self.get_object(request, pk)
        habit.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HabitCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        habit = get_object_or_404(Habit, pk=pk, user=request.user)
        completed_on_raw = str(request.data.get("completed_on") or "").strip()
        completed_on = timezone.localdate()
        if completed_on_raw:
            try:
                completed_on = date.fromisoformat(completed_on_raw)
            except ValueError as exc:
                raise ValidationError({"completed_on": "Use YYYY-MM-DD format."}) from exc

        changed = habit.mark_completed(completed_on=completed_on)
        payload = HabitSerializer(habit).data
        payload["completion_status"] = "completed" if changed else "already_completed"
        return Response(payload)


class TaskListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = _filtered_queryset(request, Task).order_by("completed", "due_date", "-created_at")
        category = request.query_params.get("category")
        priority = request.query_params.get("priority")
        completed = request.query_params.get("completed")

        if category in {"finance", "personal", "work"}:
            queryset = queryset.filter(category=category)
        if priority in {"low", "medium", "high"}:
            queryset = queryset.filter(priority=priority)
        if completed in {"true", "false"}:
            queryset = queryset.filter(completed=completed == "true")

        return Response(TaskSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = TaskSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        return get_object_or_404(Task, pk=pk, user=request.user)

    def get(self, request, pk):
        task = self.get_object(request, pk)
        return Response(TaskSerializer(task).data)

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        task = self.get_object(request, pk)
        serializer = TaskSerializer(task, data=request.data, partial=partial)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        task = self.get_object(request, pk)
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrackerListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = _filtered_queryset(request, Tracker).order_by("-date", "name")
        tracker_type = request.query_params.get("type")
        tracker_name = str(request.query_params.get("name") or "").strip()

        if tracker_type in {"daily", "weekly", "custom"}:
            queryset = queryset.filter(tracker_type=tracker_type)
        if tracker_name:
            queryset = queryset.filter(name__icontains=tracker_name)
        return Response(TrackerSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = TrackerSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TrackerDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        return get_object_or_404(Tracker, pk=pk, user=request.user)

    def get(self, request, pk):
        tracker = self.get_object(request, pk)
        return Response(TrackerSerializer(tracker).data)

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        tracker = self.get_object(request, pk)
        serializer = TrackerSerializer(tracker, data=request.data, partial=partial)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        tracker = self.get_object(request, pk)
        tracker.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrackerDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        language = _get_request_language(request)
        habits = Habit.objects.filter(user=request.user).order_by("name", "created_at")
        tasks = Task.objects.filter(user=request.user).order_by("completed", "due_date", "-created_at")
        trackers = Tracker.objects.filter(user=request.user).order_by("-date", "name")[:50]
        ai_payload = generate_recommendations(request.user, language=language)

        return Response(
            {
                "habits": HabitSerializer(habits, many=True).data,
                "tasks": TaskSerializer(tasks, many=True).data,
                "trackers": TrackerSerializer(trackers, many=True).data,
                "summary": ai_payload["summary"],
                "recommendations": ai_payload["recommendations"],
                "tracker_trends": ai_payload["tracker_trends"],
                "generated_at": ai_payload["generated_at"],
            }
        )


class AIRecommendationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(generate_recommendations(request.user, language=_get_request_language(request)))


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_build_ai_chat_payload(request.user, language=_get_request_language(request)))

    def post(self, request):
        language = _get_request_language(request)
        message = str(request.data.get("message") or "").strip()
        if not message:
            raise ValidationError({"message": _message_empty_error(language)})

        ai_payload = generate_recommendations(request.user, language=language)
        history = []
        try:
            ensure_ai_messages_seeded(request.user, ai_payload, language=language)
            history = list(
                AIAssistantMessage.objects.filter(user=request.user, kind="chat")
                .order_by("created_at")
                .values("role", "message")
            )[-8:]
        except (OperationalError, ProgrammingError):
            history = []

        reply = build_ai_chat_reply(
            request.user,
            message,
            ai_payload=ai_payload,
            history=history,
            language=language,
        )
        now = timezone.now()
        extra_messages = [
            {
                "id": f"temp-user-{int(now.timestamp())}",
                "role": "user",
                "kind": "chat",
                "message": message,
                "created_at": now.isoformat(),
            },
            {
                "id": f"temp-assistant-{int(now.timestamp())}",
                "role": "assistant",
                "kind": "chat",
                "message": reply,
                "created_at": now.isoformat(),
            },
        ]

        try:
            AIAssistantMessage.objects.create(
                user=request.user,
                role="user",
                kind="chat",
                message=message,
            )
            AIAssistantMessage.objects.create(
                user=request.user,
                role="assistant",
                kind="chat",
                message=reply,
            )
            return Response(_build_ai_chat_payload(request.user, language=language))
        except (OperationalError, ProgrammingError):
            return Response(_build_ai_chat_payload(request.user, language=language, extra_messages=extra_messages))
