from django.utils import timezone
from rest_framework import serializers

from .models import AIAssistantMessage, Habit, Task, Tracker


class HabitSerializer(serializers.ModelSerializer):
    is_due = serializers.SerializerMethodField()
    missed_periods = serializers.SerializerMethodField()
    completed_this_period = serializers.SerializerMethodField()

    class Meta:
        model = Habit
        fields = [
            "id",
            "name",
            "frequency",
            "streak_count",
            "last_completed_date",
            "points",
            "badge",
            "is_due",
            "missed_periods",
            "completed_this_period",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "streak_count",
            "last_completed_date",
            "points",
            "badge",
            "is_due",
            "missed_periods",
            "completed_this_period",
            "created_at",
            "updated_at",
        ]

    def get_is_due(self, obj):
        return obj.is_due()

    def get_missed_periods(self, obj):
        return obj.missed_periods()

    def get_completed_this_period(self, obj):
        return obj.completed_this_period()


class TaskSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()
    due_today = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "name",
            "category",
            "due_date",
            "completed",
            "completed_at",
            "priority",
            "is_overdue",
            "due_today",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["completed_at", "is_overdue", "due_today", "created_at", "updated_at"]

    def get_is_overdue(self, obj):
        return obj.is_overdue()

    def get_due_today(self, obj):
        return timezone.localtime(obj.due_date).date() == timezone.localdate()

    def create(self, validated_data):
        if validated_data.get("completed") and not validated_data.get("completed_at"):
            validated_data["completed_at"] = timezone.now()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        completed = validated_data.get("completed")
        if completed is not None and completed != instance.completed:
            validated_data["completed_at"] = timezone.now() if completed else None
        return super().update(instance, validated_data)


class TrackerSerializer(serializers.ModelSerializer):
    type = serializers.ChoiceField(source="tracker_type", choices=Tracker.TRACKER_TYPE_CHOICES)

    class Meta:
        model = Tracker
        fields = [
            "id",
            "name",
            "type",
            "value",
            "date",
            "target_value",
            "goal_direction",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_value(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "false"}:
                return normalized == "true"
            try:
                return float(normalized)
            except ValueError as exc:
                raise serializers.ValidationError("Value must be numeric or boolean.") from exc
        raise serializers.ValidationError("Value must be numeric or boolean.")


class AIAssistantMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAssistantMessage
        fields = [
            "id",
            "role",
            "kind",
            "message",
            "created_at",
        ]
        read_only_fields = fields
