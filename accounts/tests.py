import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, PropertyMock, patch

from django.db import ProgrammingError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    AIAssistantMessage,
    FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT,
    Habit,
    SupportTicket,
    Task,
    Tracker,
    User,
    get_budget_date_range,
    get_limit_subscription_expires_at,
)
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

    def test_ai_chat_house_purchase_question_uses_finance_context_and_step_plan(self):
        today = timezone.localdate()
        wallet = Wallet.objects.create(user=self.user, name="Kaspi", balance=Decimal("250000.00"))
        housing = Category.objects.create(user=self.user, name="Housing")

        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="income",
            amount=Decimal("600000.00"),
            comment="Salary",
        )
        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=housing,
            amount=Decimal("180000.00"),
            comment="Rent",
        )
        Debt.objects.create(
            user=self.user,
            type="borrowed",
            counterparty="Bank",
            amount=Decimal("90000.00"),
            due_date=today + timedelta(days=15),
            due_time=timezone.localtime().time().replace(microsecond=0),
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Үй сатып алғым келеді, не істеуім керек?"},
            format="json",
            HTTP_X_APP_LANGUAGE="kz",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("Үй сатып алуды", reply)
        self.assertIn("Бастапқы жарнаны", reply)
        self.assertIn("айлық табыс 600000.0", reply)
        self.assertIn("ипотека", reply.lower())

    def test_ai_chat_history_question_summarizes_wallets_and_recent_transactions(self):
        wallet = Wallet.objects.create(user=self.user, name="Freedom", balance=Decimal("120000.00"))
        food = Category.objects.create(user=self.user, name="Food")

        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="income",
            amount=Decimal("400000.00"),
            comment="Salary",
        )
        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=food,
            amount=Decimal("55000.00"),
            comment="Groceries",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Менің расход доход историям мен әмияндарым қалай?"},
            format="json",
            HTTP_X_APP_LANGUAGE="kz",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("Freedom", reply)
        self.assertIn("Food", reply)
        self.assertIn("транзакция тарихы", reply)

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
        system_instruction = request_body["system_instruction"]["parts"][0]["text"]
        self.assertIn("spending history, income, expenses, wallet balances, budgets, debts", system_instruction)
        self.assertIn("Detailed app context", system_instruction)
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
        header_map = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(header_map.get("X-goog-api-key".lower()), "test-key")


class DebtWalletFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="debts@example.com", password="secret123")
        self.client.force_authenticate(self.user)
        self.wallet = Wallet.objects.create(user=self.user, name="Cash", balance=Decimal("500.00"))

    def test_lent_debt_reduces_wallet_and_creates_history_transaction(self):
        today = timezone.localdate()

        response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "lent",
                "counterparty": "Friend",
                "amount": "120.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=5)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("380.00"))

        debt = Debt.objects.get(user=self.user, counterparty="Friend")
        self.assertEqual(debt.wallet, self.wallet)
        self.assertIsNotNone(debt.issued_transaction_id)
        self.assertEqual(response.data["wallet"], self.wallet.id)
        self.assertEqual(response.data["wallet_name"], "Cash")

        history_item = debt.issued_transaction
        self.assertEqual(history_item.type, "expense")
        self.assertEqual(history_item.amount, Decimal("120.00"))
        self.assertIn("Debt given to Friend", history_item.comment)

    def test_returning_lent_debt_restores_wallet_and_writes_income_history(self):
        today = timezone.localdate()
        create_response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "lent",
                "counterparty": "Colleague",
                "amount": "80.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        debt_id = create_response.data["id"]

        response = self.client.patch(
            f"/api/debt/{debt_id}/",
            {"returned": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        debt = Debt.objects.get(pk=debt_id, user=self.user)
        self.assertTrue(debt.returned)
        self.assertIsNotNone(debt.returned_transaction_id)
        self.assertEqual(debt.returned_transaction.type, "income")
        self.assertIn("Debt returned by Colleague", debt.returned_transaction.comment)

    def test_debt_history_transaction_cannot_be_deleted_directly(self):
        today = timezone.localdate()
        create_response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "borrowed",
                "counterparty": "Sibling",
                "amount": "60.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=4)).isoformat(),
            },
            format="json",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("560.00"))
        debt = Debt.objects.get(pk=create_response.data["id"], user=self.user)

        delete_response = self.client.delete(f"/api/transaction/{debt.issued_transaction_id}/")

        self.assertEqual(delete_response.status_code, 400)
        self.assertIn("linked to a debt", delete_response.data["detail"])

    def test_ai_chat_knows_about_open_debt_positions(self):
        today = timezone.localdate()
        self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "borrowed",
                "counterparty": "Parent",
                "amount": "150.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=10)).isoformat(),
            },
            format="json",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "What about my debts right now?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        reply = response.data["messages"][-1]["message"]
        self.assertIn("open lent debts", reply)
        self.assertIn("open borrowed debts", reply)
        self.assertIn("150.0", reply)

    def test_debt_history_does_not_fill_budget_limit_metrics(self):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        Budget.objects.create(
            user=self.user,
            category=None,
            limit=Decimal("1000.00"),
            start_date=month_start,
            end_date=month_end,
        )

        self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "lent",
                "counterparty": "Friend",
                "amount": "200.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=7)).isoformat(),
            },
            format="json",
        )

        response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["expenses"])), Decimal("0"))
        self.assertEqual(Decimal(str(response.data["income"])), Decimal("0"))
        self.assertEqual(Decimal(str(response.data["total_spent"])), Decimal("0"))
        self.assertEqual(Decimal(str(response.data["budgets"][0]["spent"])), Decimal("0"))

    def test_transaction_list_can_exclude_debt_history_entries(self):
        today = timezone.localdate()
        food = Category.objects.create(user=self.user, name="Food")
        regular_tx = Transaction.objects.create(
            user=self.user,
            wallet=self.wallet,
            type="expense",
            category=food,
            amount=Decimal("45.00"),
            comment="Lunch",
        )

        debt_response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "lent",
                "counterparty": "Friend",
                "amount": "120.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=3)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(debt_response.status_code, 201)

        response = self.client.get("/api/transaction/?exclude_debts=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], regular_tx.id)


class WalletBalanceProtectionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="wallet-guard@example.com", password="secret123")
        self.client.force_authenticate(self.user)
        self.wallet = Wallet.objects.create(user=self.user, name="Cash", balance=Decimal("100.00"))

    def test_expense_transaction_is_blocked_when_wallet_balance_is_insufficient(self):
        food = Category.objects.create(user=self.user, name="Food")

        response = self.client.post(
            "/api/transaction/",
            {
                "wallet": self.wallet.id,
                "type": "expense",
                "category": food.id,
                "amount": "150.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Insufficient wallet balance.")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)

    def test_lent_debt_is_blocked_when_wallet_balance_is_insufficient(self):
        today = timezone.localdate()

        response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "lent",
                "counterparty": "Friend",
                "amount": "150.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=3)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Insufficient wallet balance.")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(Debt.objects.filter(user=self.user).count(), 0)

    def test_repaying_borrowed_debt_is_blocked_when_wallet_balance_is_insufficient(self):
        today = timezone.localdate()
        create_response = self.client.post(
            "/api/debt/",
            {
                "wallet": self.wallet.id,
                "type": "borrowed",
                "counterparty": "Colleague",
                "amount": "80.00",
                "issued_date": today.isoformat(),
                "due_date": (today + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        self.wallet.balance = Decimal("20.00")
        self.wallet.save(update_fields=["balance"])

        response = self.client.patch(
            f"/api/debt/{create_response.data['id']}/",
            {"returned": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Insufficient wallet balance.")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("20.00"))

        debt = Debt.objects.get(pk=create_response.data["id"], user=self.user)
        self.assertFalse(debt.returned)
        self.assertIsNone(debt.returned_transaction_id)


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

    def _create_custom_categories(self, count):
        return [
            Category.objects.create(user=self.user, name=f"Custom {index}")
            for index in range(count)
        ]

    def _activate_subscription_state(self, started_at=None):
        self.user.is_limit_subscription_active = True
        self.user.limit_subscription_started_at = started_at or timezone.now()
        self.user.limit_subscription_cancelled_at = None
        self.user.save(
            update_fields=[
                "is_limit_subscription_active",
                "limit_subscription_started_at",
                "limit_subscription_cancelled_at",
            ]
        )

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

    def test_limit_subscription_accepts_mobile_numeric_answer_format(self):
        challenge_response = self.client.get("/api/limit-subscription/challenge/")

        self.assertEqual(challenge_response.status_code, 200)
        answer = self._solve_question(challenge_response.data["question"])

        activate_response = self.client.post(
            "/api/limit-subscription/activate/",
            {"answer": f"{answer}.0"},
            format="json",
        )

        self.assertEqual(activate_response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_limit_subscription_active)

    def test_limit_subscription_payload_includes_expiry_date(self):
        challenge_response = self.client.get("/api/limit-subscription/challenge/")
        answer = self._solve_question(challenge_response.data["question"])

        activate_response = self.client.post(
            "/api/limit-subscription/activate/",
            {"answer": answer},
            format="json",
        )

        self.assertEqual(activate_response.status_code, 200)
        self.user.refresh_from_db()
        expected_expiry = get_limit_subscription_expires_at(self.user)
        self.assertIsNotNone(activate_response.data["limit_subscription_expires_at"])
        self.assertEqual(
            activate_response.data["limit_subscription_expires_at"].date(),
            expected_expiry.date(),
        )

    def test_expired_limit_subscription_is_deactivated_on_profile_request(self):
        started_at = timezone.now() - timedelta(days=40)
        self._activate_subscription_state(started_at=started_at)

        response = self.client.get("/api/user/")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(response.data["is_limit_subscription_active"])
        self.assertFalse(self.user.is_limit_subscription_active)
        self.assertIsNotNone(self.user.limit_subscription_cancelled_at)

    def test_expired_limit_subscription_allows_new_challenge(self):
        started_at = timezone.now() - timedelta(days=40)
        self._activate_subscription_state(started_at=started_at)

        response = self.client.get("/api/limit-subscription/challenge/")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_limit_subscription_active)
        self.assertTrue(response.data.get("question"))

    def test_premium_budget_requires_active_subscription(self):
        blocked_response = self.client.post(
            "/api/budget/",
            {"category": "Другие", "limit": "500"},
            format="json",
        )

        self.assertEqual(blocked_response.status_code, 400)
        self.assertIn("category", blocked_response.data)

        self._activate_subscription_state()

        allowed_response = self.client.post(
            "/api/budget/",
            {"category": "Другие", "limit": "500"},
            format="json",
        )

        self.assertEqual(allowed_response.status_code, 201)
        self.assertTrue(Category.objects.get(user=self.user, name="Другие").is_limit_subscription_premium)

    def test_custom_premium_budget_requires_active_subscription(self):
        blocked_response = self.client.post(
            "/api/budget/",
            {"category": "Фитнес", "limit": "500", "premium_slot": True},
            format="json",
        )

        self.assertEqual(blocked_response.status_code, 400)
        self.assertIn("category", blocked_response.data)

        self._activate_subscription_state()

        allowed_response = self.client.post(
            "/api/budget/",
            {"category": "Фитнес", "limit": "500", "premium_slot": True},
            format="json",
        )

        self.assertEqual(allowed_response.status_code, 201)
        self.assertTrue(Category.objects.get(user=self.user, name="Фитнес").is_limit_subscription_premium)

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

        self._activate_subscription_state()

        active_response = self.client.get("/api/finance/")

        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(len(active_response.data["budgets"]), 2)
        self.assertEqual(Decimal(str(active_response.data["total_limit"])), Decimal("1000.00"))

    def test_finance_hides_custom_premium_budgets_while_subscription_is_inactive(self):
        self._activate_subscription_state()

        custom_premium_response = self.client.post(
            "/api/budget/",
            {"category": "Фитнес", "limit": "700", "premium_slot": True},
            format="json",
        )
        self.assertEqual(custom_premium_response.status_code, 201)

        active_response = self.client.get("/api/finance/")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(len(active_response.data["budgets"]), 1)

        self.user.is_limit_subscription_active = False
        self.user.save(update_fields=["is_limit_subscription_active"])

        inactive_response = self.client.get("/api/finance/")
        self.assertEqual(inactive_response.status_code, 200)
        self.assertEqual(len(inactive_response.data["budgets"]), 0)

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

    def test_custom_category_create_is_limited_without_subscription(self):
        self._create_custom_categories(FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT)

        response = self.client.post(
            "/api/category/",
            {"name": "Extra custom"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
        self.assertFalse(Category.objects.filter(user=self.user, name="Extra custom").exists())

    def test_custom_category_transaction_create_is_limited_without_subscription(self):
        wallet = Wallet.objects.create(user=self.user, name="Cash", balance=Decimal("1000.00"))
        self._create_custom_categories(FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT)

        response = self.client.post(
            "/api/transaction/",
            {
                "wallet": wallet.id,
                "type": "expense",
                "category": "Extra custom",
                "amount": "120.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.data)
        self.assertFalse(Category.objects.filter(user=self.user, name="Extra custom").exists())

    def test_custom_category_limit_is_removed_with_subscription(self):
        self._create_custom_categories(FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT)
        self._activate_subscription_state()

        response = self.client.post(
            "/api/category/",
            {"name": "Extra custom"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Category.objects.filter(user=self.user, name="Extra custom").exists())

    def test_default_categories_do_not_count_toward_custom_category_limit(self):
        for name in [
            "Кафе",
            "Развлечение",
            "Одежда",
            "Продукты",
            "Транспорт",
            "Другие",
            "Здоровье",
            "Путешествия",
        ]:
            Category.objects.create(user=self.user, name=name)
        self._create_custom_categories(FREE_CUSTOM_EXPENSE_CATEGORY_LIMIT - 1)

        response = self.client.post(
            "/api/category/",
            {"name": "Custom final"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Category.objects.filter(user=self.user, name="Custom final").exists())

    def test_custom_limit_subscription_slots_can_grow_after_subscription(self):
        self._activate_subscription_state()

        for index in range(12):
            response = self.client.post(
                "/api/budget/",
                {"category": f"Premium Slot {index}", "limit": "500", "premium_slot": True},
                format="json",
            )
            self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Budget.objects.filter(user=self.user, category__is_limit_subscription_premium=True).count(),
            12,
        )

    def test_custom_limit_slot_spent_ignores_uncategorized_expenses(self):
        self._activate_subscription_state()
        wallet = Wallet.objects.create(user=self.user, name="Cash", balance=Decimal("5000.00"))

        create_response = self.client.post(
            "/api/budget/",
            {"category": "Premium Slot Zero", "limit": "10000", "premium_slot": True},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=None,
            amount=Decimal("555.00"),
        )

        response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        premium_budget = next(
            item for item in response.data["budgets"] if item["category"] == "Premium Slot Zero"
        )
        self.assertEqual(Decimal(str(premium_budget["spent"])), Decimal("0"))

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

    def test_weekly_budget_uses_current_calendar_week_range(self):
        response = self.client.post(
            "/api/budget/",
            {"category": "Кафе", "limit": "900.00", "period_type": "week"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        expected_start, expected_end = get_budget_date_range("week", reference_date=timezone.localdate())
        self.assertEqual(response.data["period_type"], "week")
        self.assertEqual(str(response.data["start_date"]), expected_start.isoformat())
        self.assertEqual(str(response.data["end_date"]), expected_end.isoformat())

    def test_finance_returns_only_active_budgets(self):
        category = Category.objects.create(user=self.user, name="Кафе")
        today = timezone.localdate()
        current_start, current_end = get_budget_date_range("month", reference_date=today)
        previous_month_day = current_start - timedelta(days=1)
        previous_start, previous_end = get_budget_date_range("month", reference_date=previous_month_day)

        expired_budget = Budget.objects.create(
            user=self.user,
            category=category,
            limit=Decimal("400.00"),
            period_type="month",
            start_date=previous_start,
            end_date=previous_end,
        )
        active_budget = Budget.objects.create(
            user=self.user,
            category=category,
            limit=Decimal("600.00"),
            period_type="month",
            start_date=current_start,
            end_date=current_end,
        )

        response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["budgets"]), 1)
        self.assertEqual(response.data["budgets"][0]["id"], active_budget.id)
        self.assertNotEqual(response.data["budgets"][0]["id"], expired_budget.id)

    def test_budget_history_returns_period_totals_and_top_category(self):
        food = Category.objects.create(user=self.user, name="Food")
        transport = Category.objects.create(user=self.user, name="Transport")
        wallet = Wallet.objects.create(user=self.user, name="Card", balance=Decimal("3000.00"))

        current_month_start, current_month_end = get_budget_date_range(
            "month",
            reference_date=timezone.localdate(),
        )
        previous_month_day = current_month_start - timedelta(days=1)
        previous_month_start, previous_month_end = get_budget_date_range(
            "month",
            reference_date=previous_month_day,
        )

        Budget.objects.create(
            user=self.user,
            category=food,
            limit=Decimal("1000.00"),
            period_type="month",
            start_date=current_month_start,
            end_date=current_month_end,
        )
        Budget.objects.create(
            user=self.user,
            category=transport,
            limit=Decimal("700.00"),
            period_type="month",
            start_date=previous_month_start,
            end_date=previous_month_end,
        )

        current_food_tx = Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=food,
            amount=Decimal("450.00"),
        )
        current_food_tx.date = current_month_start + timedelta(days=2)
        current_food_tx.save(update_fields=["date"])

        current_transport_tx = Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=transport,
            amount=Decimal("125.00"),
        )
        current_transport_tx.date = current_month_start + timedelta(days=3)
        current_transport_tx.save(update_fields=["date"])

        previous_tx = Transaction.objects.create(
            user=self.user,
            wallet=wallet,
            type="expense",
            category=transport,
            amount=Decimal("320.00"),
        )
        previous_tx.date = previous_month_start + timedelta(days=4)
        previous_tx.save(update_fields=["date"])

        response = self.client.get("/api/budget/history/?period_type=month&limit=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["period_type"], "month")
        self.assertEqual(len(response.data["history"]), 2)

        latest_period = response.data["history"][0]
        older_period = response.data["history"][1]

        self.assertEqual(str(latest_period["start_date"]), current_month_start.isoformat())
        self.assertEqual(Decimal(str(latest_period["total_spent"])), Decimal("575.00"))
        self.assertEqual(Decimal(str(latest_period["total_limit"])), Decimal("1000.00"))
        self.assertEqual(latest_period["top_category"]["name"], "Food")

        self.assertEqual(str(older_period["start_date"]), previous_month_start.isoformat())
        self.assertEqual(Decimal(str(older_period["total_spent"])), Decimal("320.00"))
        self.assertEqual(Decimal(str(older_period["total_limit"])), Decimal("700.00"))
        self.assertEqual(older_period["top_category"]["name"], "Transport")


class FinanceSchemaFallbackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="fallback@example.com", password="secret123")
        self.client.force_authenticate(self.user)
        self.wallet = Wallet.objects.create(user=self.user, name="Card", balance=Decimal("2000.00"))

    def test_finance_falls_back_when_debt_filtering_schema_is_unavailable(self):
        food = Category.objects.create(user=self.user, name="Food")
        Transaction.objects.create(
            user=self.user,
            wallet=self.wallet,
            type="expense",
            category=food,
            amount=Decimal("120.00"),
        )

        with patch(
            "accounts.views.exclude_debt_related_transactions",
            side_effect=ProgrammingError("missing debt history links"),
        ):
            response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["expenses"])), Decimal("120.00"))
        self.assertEqual(response.data["categories"][0]["name"], "Food")

    def test_finance_omits_budgets_when_budget_schema_is_unavailable(self):
        category = Category.objects.create(user=self.user, name="Кафе")
        Budget.objects.create(
            user=self.user,
            category=category,
            limit=Decimal("500.00"),
            start_date=timezone.localdate().replace(day=1),
            end_date=timezone.localdate(),
        )

        with patch(
            "accounts.views.BudgetSerializer.get_spent",
            side_effect=ProgrammingError("missing premium category schema"),
        ):
            response = self.client.get("/api/finance/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["budgets"], [])
        self.assertEqual(Decimal(str(response.data["total_limit"])), Decimal("0"))
        self.assertEqual(Decimal(str(response.data["total_spent"])), Decimal("0"))

    def test_transaction_list_falls_back_when_serializer_schema_is_unavailable(self):
        category = Category.objects.create(user=self.user, name="Transport")
        transaction = Transaction.objects.create(
            user=self.user,
            wallet=self.wallet,
            type="expense",
            category=category,
            amount=Decimal("80.00"),
            comment="Taxi",
        )

        with patch(
            "accounts.views.TransactionSerializer.data",
            new_callable=PropertyMock,
            side_effect=ProgrammingError("missing category schema"),
        ):
            response = self.client.get("/api/transaction/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], transaction.id)
        self.assertEqual(response.data[0]["category"], "Transport")
        self.assertEqual(response.data[0]["over_limit"], False)
        self.assertEqual(response.data[0]["percent_used"], 0)


class SupportPortalLoginTests(TestCase):
    def test_support_login_allows_support_agent_and_redirects_to_tickets(self):
        User.objects.create_user(
            email="support@example.com",
            password="secret123",
            is_active=True,
            is_support_agent=True,
        )

        response = self.client.post(
            "/support/login/",
            {"email": "support@example.com", "password": "secret123"},
        )

        self.assertRedirects(response, "/dashboard/tickets/")

    def test_support_login_rejects_regular_user_without_support_access(self):
        User.objects.create_user(
            email="user@example.com",
            password="secret123",
            is_active=True,
        )

        response = self.client.post(
            "/support/login/",
            {"email": "user@example.com", "password": "secret123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "техподдержка қызметкерлеріне арналған")

    def test_support_login_redirects_staff_user_to_tickets(self):
        User.objects.create_user(
            email="admin@example.com",
            password="secret123",
            is_active=True,
            is_staff=True,
        )

        response = self.client.post(
            "/support/login/",
            {"email": "admin@example.com", "password": "secret123"},
        )

        self.assertRedirects(response, "/dashboard/tickets/")

    def test_support_login_shows_verification_message_for_inactive_user(self):
        User.objects.create_user(
            email="pending@example.com",
            password="secret123",
            is_active=False,
        )

        response = self.client.post(
            "/support/login/",
            {"email": "pending@example.com", "password": "secret123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email әлі расталмаған")

    def test_support_agent_can_open_ticket_console_but_not_admin_overview(self):
        support_user = User.objects.create_user(
            email="support@example.com",
            password="secret123",
            is_active=True,
            is_support_agent=True,
        )
        self.client.force_login(support_user)

        tickets_response = self.client.get("/dashboard/tickets/")
        overview_response = self.client.get("/dashboard/overview/")

        self.assertEqual(tickets_response.status_code, 200)
        self.assertEqual(overview_response.status_code, 302)
        self.assertIn("/admin-login/", overview_response.url)

    def test_support_agent_can_use_support_ticket_api_but_not_admin_activity_api(self):
        support_user = User.objects.create_user(
            email="support@example.com",
            password="secret123",
            is_active=True,
            is_support_agent=True,
        )
        regular_user = User.objects.create_user(
            email="customer@example.com",
            password="secret123",
            is_active=True,
        )
        SupportTicket.objects.create(
            user=regular_user,
            subject="Payment issue",
            message="Need help with failed payment",
        )

        self.client.force_login(support_user)

        tickets_response = self.client.get("/api/admin/support/tickets/")
        activity_response = self.client.get("/api/admin/user-activity/")

        self.assertEqual(tickets_response.status_code, 200)
        self.assertEqual(len(tickets_response.json()), 1)
        self.assertEqual(activity_response.status_code, 403)
