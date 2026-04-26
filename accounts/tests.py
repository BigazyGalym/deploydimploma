import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AIAssistantMessage, Habit, Task, Tracker, User
from .models import Budget, Category, Debt, Transaction, Wallet


class TrackerApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="user@example.com", password="secret123")
        self.other_user = User.objects.create_user(email="other@example.com", password="secret123")
        self.client.force_authenticate(self.user)

    def test_complete_habit_updates_streak_points_and_badge(self):
        today = timezone.localdate()
        habit = Habit.objects.create(
            user=self.user,
            name="Record daily expense",
            frequency="daily",
            streak_count=2,
            last_completed_date=today - timedelta(days=1),
            points=20,
        )

        response = self.client.post(f"/api/habits/{habit.id}/complete/")

        self.assertEqual(response.status_code, 200)
        habit.refresh_from_db()
        self.assertEqual(habit.streak_count, 3)
        self.assertEqual(habit.points, 30)
        self.assertEqual(habit.badge, "bronze")
        self.assertEqual(habit.last_completed_date, today)
        self.assertEqual(response.data["completion_status"], "completed")
        self.assertTrue(response.data["completed_this_period"])

    def test_task_list_is_scoped_to_current_user(self):
        now = timezone.now()
        Task.objects.create(
            user=self.user,
            name="Pay utility bill",
            category="finance",
            due_date=now + timedelta(hours=6),
            priority="high",
        )
        Task.objects.create(
            user=self.other_user,
            name="Someone else's task",
            category="personal",
            due_date=now + timedelta(days=1),
            priority="low",
        )

        response = self.client.get("/api/tasks/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Pay utility bill")

    def test_ai_recommendations_include_habit_task_and_tracker_signals(self):
        today = timezone.localdate()
        now = timezone.now()

        Habit.objects.create(
            user=self.user,
            name="Record daily expense",
            frequency="daily",
            streak_count=1,
            last_completed_date=today - timedelta(days=3),
        )
        Task.objects.create(
            user=self.user,
            name="Review subscriptions",
            category="finance",
            due_date=now - timedelta(hours=3),
            priority="high",
        )
        Tracker.objects.create(
            user=self.user,
            name="Savings rate",
            tracker_type="weekly",
            value=52,
            date=now - timedelta(days=14),
            target_value=Decimal("60.00"),
            goal_direction="at_least",
        )
        Tracker.objects.create(
            user=self.user,
            name="Savings rate",
            tracker_type="weekly",
            value=48,
            date=now - timedelta(days=7),
            target_value=Decimal("60.00"),
            goal_direction="at_least",
        )
        Tracker.objects.create(
            user=self.user,
            name="Savings rate",
            tracker_type="weekly",
            value=44,
            date=now,
            target_value=Decimal("60.00"),
            goal_direction="at_least",
        )

        response = self.client.get("/api/ai/recommendations/")

        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["recommendations"]]
        self.assertTrue(any("Catch up on Record daily expense" in title for title in titles))
        self.assertIn("Overdue tasks need attention", titles)
        self.assertTrue(any("Savings rate is trending below target" in title for title in titles))
        self.assertEqual(response.data["summary"]["missed_habits"], 1)
        self.assertEqual(response.data["summary"]["overdue_tasks"], 1)

    def test_ai_recommendations_respect_request_language(self):
        Task.objects.create(
            user=self.user,
            name="Review subscriptions",
            category="finance",
            due_date=timezone.now() - timedelta(hours=3),
            priority="high",
        )

        response = self.client.get("/api/ai/recommendations/", HTTP_X_APP_LANGUAGE="ru")

        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["recommendations"]]
        self.assertIn("Просроченные задачи требуют внимания", titles)

    def test_ai_chat_seeds_welcome_and_daily_proactive_messages_once(self):
        Task.objects.create(
            user=self.user,
            name="Pay rent",
            category="finance",
            due_date=timezone.now() - timedelta(hours=2),
            priority="high",
        )

        first_response = self.client.get("/api/ai/chat/")
        second_response = self.client.get("/api/ai/chat/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertGreaterEqual(len(first_response.data["messages"]), 2)
        self.assertEqual(len(first_response.data["messages"]), len(second_response.data["messages"]))
        self.assertEqual(AIAssistantMessage.objects.filter(user=self.user, kind="system").count(), 1)
        self.assertGreaterEqual(
            AIAssistantMessage.objects.filter(user=self.user, kind="proactive").count(),
            1,
        )

    def test_ai_chat_get_localizes_seed_messages_and_prompts(self):
        Task.objects.create(
            user=self.user,
            name="Pay rent",
            category="finance",
            due_date=timezone.now() - timedelta(hours=2),
            priority="high",
        )

        response = self.client.get("/api/ai/chat/", HTTP_X_APP_LANGUAGE="kz")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Мен сіздің ИИ көмекшіңізбін", response.data["messages"][0]["message"])
        self.assertTrue(
            any("Қай мерзімі өткен тапсырманы" in prompt for prompt in response.data["quick_prompts"])
        )

    def test_ai_chat_post_creates_user_and_assistant_messages(self):
        today = timezone.localdate()
        Habit.objects.create(
            user=self.user,
            name="Review spending",
            frequency="daily",
            last_completed_date=today - timedelta(days=2),
        )
        Task.objects.create(
            user=self.user,
            name="Call the bank",
            category="finance",
            due_date=timezone.now() - timedelta(hours=1),
            priority="high",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "What should I focus on today?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["messages"][-2]["role"], "user")
        self.assertEqual(response.data["messages"][-1]["role"], "assistant")
        self.assertIn("Call the bank", response.data["messages"][-1]["message"])
        self.assertIn("quick_prompts", response.data)

    def test_ai_recommendations_include_finance_signals(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        wallet = Wallet.objects.create(user=self.user, name="Cash", balance=1000)
        food = Category.objects.create(user=self.user, name="Food")

        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=food,
            amount=Decimal("450.00"),
            comment="Groceries and delivery",
        )
        Budget.objects.create(
            user=self.user,
            category=food,
            limit=Decimal("300.00"),
            start_date=month_start,
            end_date=month_end,
        )
        Debt.objects.create(
            user=self.user,
            type="borrowed",
            counterparty="Friend",
            amount=Decimal("120.00"),
            due_date=today - timedelta(days=1),
            due_time=timezone.localtime().time().replace(microsecond=0),
        )

        response = self.client.get("/api/ai/recommendations/")

        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["recommendations"]]
        self.assertIn("Food budget is over limit", titles)
        self.assertIn("Overdue debts need action", titles)
        self.assertTrue(any("Food" in title for title in titles))
        self.assertEqual(response.data["summary"]["over_budget_budgets"], 1)
        self.assertEqual(response.data["summary"]["overdue_debts"], 1)

    def test_ai_chat_answers_spending_question_from_finance_data(self):
        today = timezone.localdate()
        wallet = Wallet.objects.create(user=self.user, name="Card", balance=1500)
        food = Category.objects.create(user=self.user, name="Food")

        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="income",
            amount=Decimal("500.00"),
            comment="Salary part",
        )
        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=food,
            amount=Decimal("320.00"),
            comment="Food spend",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Where is most of my money going this month?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("Food", reply)
        self.assertIn("320.0", reply)

    def test_ai_chat_personal_question_uses_general_coaching_reply(self):
        response = self.client.post(
            "/api/ai/chat/",
            {"message": "How do I stop procrastinating and actually start?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("Motivation usually comes after action", reply)
        self.assertNotIn("Here is the clearest advice from your current data.", reply)

    def test_ai_chat_personal_question_localizes_general_coaching_reply(self):
        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Қалай мотивация табамын?"},
            format="json",
            HTTP_X_APP_LANGUAGE="kz",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("Мотивация көбіне әрекеттен кейін келеді", reply)

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-2.5-flash")
    @patch("accounts.gemini_client.urlopen")
    def test_ai_chat_uses_gemini_when_configured(self, mock_urlopen):
        response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Gemini says to finish the most urgent task first."}
                        ]
                    }
                }
            ]
        }
        mock_response = Mock()
        mock_response.read.return_value = json.dumps(response_payload).encode("utf-8")
        mock_context_manager = Mock()
        mock_context_manager.__enter__ = Mock(return_value=mock_response)
        mock_context_manager.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_context_manager

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Give me a plan"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["messages"][-1]["message"],
            "Gemini says to finish the most urgent task first.",
        )
        request = mock_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertIn("system_instruction", request_body)
        self.assertNotIn("store", request_body)
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
        header_map = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(header_map.get("X-goog-api-key".lower()), "test-key")


class LimitSubscriptionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="premium@example.com", password="secret123")
        self.client.force_authenticate(self.user)

    def _solve_question(self, question):
        left, operator, right = question.split()
        left = int(left)
        right = int(right)
        if operator == "+":
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        raise AssertionError(f"Unexpected operator: {operator}")

    def test_limit_subscription_activates_only_for_correct_answer(self):
        challenge_response = self.client.get("/api/limit-subscription/challenge/")

        self.assertEqual(challenge_response.status_code, 200)
        answer = self._solve_question(challenge_response.data["question"])

        activate_response = self.client.post(
            "/api/limit-subscription/activate/",
            {"answer": answer},
            format="json",
        )

        self.assertEqual(activate_response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_limit_subscription_active)
        self.assertIsNotNone(self.user.limit_subscription_started_at)
        self.assertEqual(activate_response.data["is_limit_subscription_active"], True)

    def test_limit_subscription_rejects_incorrect_answer(self):
        self.client.get("/api/limit-subscription/challenge/")

        activate_response = self.client.post(
            "/api/limit-subscription/activate/",
            {"answer": -999},
            format="json",
        )

        self.assertEqual(activate_response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_limit_subscription_active)
        self.assertEqual(self.user.limit_subscription_challenge, "")
        self.assertIsNone(self.user.limit_subscription_answer)

    def test_premium_budget_requires_active_subscription(self):
        blocked_response = self.client.post(
            "/api/budget/",
            {"category": "Другие", "limit": "500"},
            format="json",
        )

        self.assertEqual(blocked_response.status_code, 400)
        self.assertIn("category", blocked_response.data)

        self.user.is_limit_subscription_active = True
        self.user.save(update_fields=["is_limit_subscription_active"])

        allowed_response = self.client.post(
            "/api/budget/",
            {"category": "Другие", "limit": "500"},
            format="json",
        )

        self.assertEqual(allowed_response.status_code, 201)

    def test_finance_hides_premium_budgets_while_subscription_is_inactive(self):
        base_category = Category.objects.create(user=self.user, name="Кафе")
        premium_category = Category.objects.create(user=self.user, name="Другие")

        Budget.objects.create(
            user=self.user,
            category=base_category,
            limit=Decimal("300.00"),
            start_date=timezone.localdate().replace(day=1),
            end_date=timezone.localdate(),
        )
        Budget.objects.create(
            user=self.user,
            category=premium_category,
            limit=Decimal("700.00"),
            start_date=timezone.localdate().replace(day=1),
            end_date=timezone.localdate(),
        )

        inactive_response = self.client.get("/api/finance/")

        self.assertEqual(inactive_response.status_code, 200)
        self.assertEqual(len(inactive_response.data["budgets"]), 1)
        self.assertEqual(Decimal(str(inactive_response.data["total_limit"])), Decimal("300.00"))

        self.user.is_limit_subscription_active = True
        self.user.save(update_fields=["is_limit_subscription_active"])

        active_response = self.client.get("/api/finance/")

        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(len(active_response.data["budgets"]), 2)
        self.assertEqual(Decimal(str(active_response.data["total_limit"])), Decimal("1000.00"))

    def test_limit_subscription_can_be_cancelled(self):
        self.user.is_limit_subscription_active = True
        self.user.limit_subscription_started_at = timezone.now()
        self.user.save(
            update_fields=["is_limit_subscription_active", "limit_subscription_started_at"]
        )

        response = self.client.post("/api/limit-subscription/cancel/")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_limit_subscription_active)
        self.assertIsNotNone(self.user.limit_subscription_cancelled_at)

    def test_finance_total_spent_counts_only_budgeted_categories(self):
        wallet = Wallet.objects.create(user=self.user, name="Card", balance=2000)
        budgeted_category = Category.objects.create(user=self.user, name="Кафе")
        unbudgeted_category = Category.objects.create(user=self.user, name="Без лимита")

        Budget.objects.create(
            user=self.user,
            category=budgeted_category,
            limit=Decimal("10000.00"),
            start_date=timezone.localdate().replace(day=1),
            end_date=timezone.localdate(),
        )
        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=budgeted_category,
            amount=Decimal("8000.00"),
        )
        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=unbudgeted_category,
            amount=Decimal("2500.00"),
        )

        response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["expenses"])), Decimal("10500.00"))
        self.assertEqual(Decimal(str(response.data["total_spent"])), Decimal("8000.00"))
