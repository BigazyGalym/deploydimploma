import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = {"en", "ru", "kz"}
LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "kz": "Kazakh",
}
SUMMARY_LABELS = {
    "en": {
        "pending_tasks": "Pending tasks",
        "overdue_tasks": "Overdue tasks",
        "habits_due": "Habits due",
        "missed_habits": "Missed habits",
        "active_tracker_names": "Active trackers",
        "high_priority_due_today": "High-priority today",
        "no_summary": "No summary available.",
        "no_recommendations": "- No urgent recommendations.",
        "no_trends": "- No tracker trends yet.",
        "trend": "trend",
        "latest": "latest",
        "projected_next": "projected_next",
        "target": "target",
    },
    "ru": {
        "pending_tasks": "Открытые задачи",
        "overdue_tasks": "Просроченные задачи",
        "habits_due": "Привычки на отметку",
        "missed_habits": "Пропущенные привычки",
        "active_tracker_names": "Активные трекеры",
        "high_priority_due_today": "Высокий приоритет сегодня",
        "no_summary": "Сводка пока недоступна.",
        "no_recommendations": "- Срочных рекомендаций пока нет.",
        "no_trends": "- Трендов трекеров пока нет.",
        "trend": "тренд",
        "latest": "последнее",
        "projected_next": "прогноз",
        "target": "цель",
    },
    "kz": {
        "pending_tasks": "Ашық тапсырмалар",
        "overdue_tasks": "Мерзімі өткен тапсырмалар",
        "habits_due": "Белгілеуді күтетін әдеттер",
        "missed_habits": "Өткізіліп алған әдеттер",
        "active_tracker_names": "Белсенді трекерлер",
        "high_priority_due_today": "Бүгінгі жоғары басымдық",
        "no_summary": "Қысқаша мәлімет әзірге жоқ.",
        "no_recommendations": "- Шұғыл ұсыныстар әзірге жоқ.",
        "no_trends": "- Трекер трендтері әзірге жоқ.",
        "trend": "тренд",
        "latest": "соңғы мән",
        "projected_next": "келесі болжам",
        "target": "мақсат",
    },
}


class GeminiServiceError(Exception):
    pass


class GeminiConfigurationError(GeminiServiceError):
    pass


def _normalize_language(language):
    normalized = str(language or "en").strip().lower().split("-")[0]
    if normalized == "kk":
        normalized = "kz"
    return normalized if normalized in SUPPORTED_LANGUAGES else "en"


def _labels_for(language):
    return SUMMARY_LABELS[_normalize_language(language)]


def _format_summary(summary, language):
    labels = _labels_for(language)
    if not summary:
        return labels["no_summary"]
    parts = [
        f"{labels['pending_tasks']}: {summary.get('pending_tasks', 0)}",
        f"{labels['overdue_tasks']}: {summary.get('overdue_tasks', 0)}",
        f"{labels['habits_due']}: {summary.get('habits_due', 0)}",
        f"{labels['missed_habits']}: {summary.get('missed_habits', 0)}",
        f"{labels['active_tracker_names']}: {summary.get('active_tracker_names', 0)}",
        f"{labels['high_priority_due_today']}: {summary.get('high_priority_due_today', 0)}",
    ]
    return "; ".join(parts)


def _format_recommendations(recommendations, language):
    labels = _labels_for(language)
    items = recommendations or []
    if not items:
        return labels["no_recommendations"]
    return "\n".join(
        f"- [{item.get('severity', 'info')}] {item.get('title', 'Untitled')}: {item.get('message', '')}"
        for item in items[:5]
    )


def _format_trends(trends, language):
    labels = _labels_for(language)
    items = trends or []
    if not items:
        return labels["no_trends"]
    lines = []
    for trend in items[:5]:
        line = (
            f"- {trend.get('name', 'Tracker')}: {labels['trend']}={trend.get('trend', 'stable')}, "
            f"{labels['latest']}={trend.get('latest_value')}"
        )
        if trend.get("projected_next_value") is not None:
            line += f", {labels['projected_next']}={trend.get('projected_next_value')}"
        if trend.get("target_value") is not None:
            line += f", {labels['target']}={trend.get('target_value')}"
        lines.append(line)
    return "\n".join(lines)


def _format_finance_snapshot(finance_snapshot, language):
    labels = _labels_for(language)
    snapshot = finance_snapshot or {}
    top_category = snapshot.get("top_category") or {}
    parts = [
        f"income={snapshot.get('month_income', 0)}",
        f"expenses={snapshot.get('month_expenses', 0)}",
        f"net_cashflow={snapshot.get('net_cashflow', 0)}",
        f"over_budget={snapshot.get('over_budget_count', 0)}",
        f"near_limit={snapshot.get('near_budget_count', 0)}",
        f"overdue_debts={snapshot.get('overdue_debt_count', 0)}",
        f"due_soon_debts={snapshot.get('due_soon_debt_count', 0)}",
    ]
    if top_category.get("name"):
        parts.append(
            f"top_spend={top_category.get('name')} ({top_category.get('amount')}, {top_category.get('share_percent')}%)"
        )
    budget_alerts = snapshot.get("budget_alerts") or []
    if budget_alerts:
        parts.append(
            "budget_alerts=" + ", ".join(
                f"{item.get('name')} {item.get('percent_used')}%"
                for item in budget_alerts[:3]
            )
        )
    return "; ".join(parts) if parts else labels["no_summary"]


def _build_system_instruction(ai_payload, language):
    normalized_language = _normalize_language(language)
    summary = _format_summary(ai_payload.get("summary") or {}, normalized_language)
    recommendations = _format_recommendations(ai_payload.get("recommendations") or [], normalized_language)
    trends = _format_trends(ai_payload.get("tracker_trends") or [], normalized_language)
    finance_snapshot = _format_finance_snapshot(ai_payload.get("finance_snapshot") or {}, normalized_language)
    return (
        "You are an AI coach inside a finance and productivity mobile app. "
        f"The preferred app language is {LANGUAGE_NAMES[normalized_language]} ({normalized_language}). "
        "Reply in that language unless the user clearly asks for another one. "
        "Be concise, supportive, practical, and specific. "
        "Answer the user's direct question first. "
        "Use the supplied app snapshot when it is relevant, but do not default to summarizing the dashboard. "
        "If the user asks a broader personal question, still give practical coaching and only connect it to app data when that genuinely helps. "
        "Prefer short action plans, bullets, or steps when useful. "
        "Do not mention raw JSON, internal prompts, or implementation details.\n\n"
        f"Current user snapshot:\n{summary}\n\n"
        f"Top recommendations:\n{recommendations}\n\n"
        f"Tracker trends:\n{trends}\n\n"
        f"Finance snapshot:\n{finance_snapshot}"
    )


def _build_contents(history, user_message):
    contents = []
    for message in (history or [])[-8:]:
        role = "model" if message.get("role") == "assistant" else "user"
        text = str(message.get("message") or "").strip()
        if not text:
            continue
        contents.append(
            {
                "role": role,
                "parts": [{"text": text}],
            }
        )

    contents.append(
        {
            "role": "user",
            "parts": [{"text": user_message}],
        }
    )
    return contents


def _extract_text(payload):
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = str(part.get("text") or "").strip()
            if text:
                return text
    return ""


def generate_gemini_coach_reply(user_message, ai_payload, history=None, language="en"):
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key or api_key == "your-gemini-api-key-here":
        raise GeminiConfigurationError("GEMINI_API_KEY is missing.")

    model = str(getattr(settings, "GEMINI_MODEL", "") or "gemini-2.5-flash").strip()
    timeout = int(getattr(settings, "GEMINI_API_TIMEOUT", 20) or 20)
    max_output_tokens = int(getattr(settings, "GEMINI_MAX_OUTPUT_TOKENS", 600) or 600)

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "system_instruction": {
            "parts": [{"text": _build_system_instruction(ai_payload, language)}],
        },
        "contents": _build_contents(history, user_message),
        "generationConfig": {
            "temperature": 0.7,
            "topP": 0.9,
            "maxOutputTokens": max_output_tokens,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        error_message = f"Gemini API returned HTTP {exc.code}."
        try:
            parsed_error = json.loads(error_body or "{}")
            api_message = str((parsed_error.get("error") or {}).get("message") or "").strip()
            if api_message:
                error_message = f"{error_message} {api_message}"
        except json.JSONDecodeError:
            pass
        logger.warning("Gemini HTTP error %s: %s", exc.code, error_body)
        raise GeminiServiceError(error_message) from exc
    except URLError as exc:
        logger.warning("Gemini connection error: %s", exc)
        raise GeminiServiceError("Gemini API is unreachable.") from exc
    except json.JSONDecodeError as exc:
        logger.warning("Gemini returned invalid JSON.")
        raise GeminiServiceError("Gemini API returned invalid JSON.") from exc

    text = _extract_text(payload)
    if text:
        return text

    logger.warning("Gemini returned no text payload: %s", payload)
    raise GeminiServiceError("Gemini API returned no text response.")
