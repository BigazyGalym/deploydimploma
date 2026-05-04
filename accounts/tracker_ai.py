from collections import defaultdict
from decimal import Decimal, InvalidOperation
import datetime
import re
import logging

from django.db.models import Q, Sum
from django.utils import timezone

from .gemini_client import GeminiConfigurationError, GeminiServiceError, generate_gemini_coach_reply
from .models import (
    AIAssistantMessage,
    Budget,
    Debt,
    Habit,
    Task,
    Tracker,
    Transaction,
    Wallet,
    exclude_debt_related_transactions,
)


logger = logging.getLogger(__name__)

SEVERITY_WEIGHT = {
    "high": 0,
    "medium": 1,
    "positive": 2,
    "low": 3,
}

SUPPORTED_LANGUAGES = {"en", "ru", "kz"}
BASE_PROMPT_KEYS = (
    "quick_prompt_focus_today",
    "quick_prompt_habit_attention",
    "quick_prompt_tracker_results",
    "quick_prompt_short_plan",
)

AI_COPY = {
    "en": {
        "welcome_message": (
            "I am your AI coach. Write to me about priorities, habits, tasks, or tracker trends, "
            "and I will turn your data into a simple action plan."
        ),
        "quick_prompt_focus_today": "What should I focus on today?",
        "quick_prompt_habit_attention": "Which habit needs attention most?",
        "quick_prompt_tracker_results": "How can I improve my tracker results?",
        "quick_prompt_short_plan": "Give me a short action plan for today.",
        "quick_prompt_overdue_task": "Which overdue task should I finish first?",
        "quick_prompt_habits_back": "How do I get back on track with my habits?",
        "quick_prompt_explain_trend": "Explain my {name} trend.",
        "quick_prompt_spending": "Where is most of my money going this month?",
        "quick_prompt_budget": "How can I reduce spending this week?",
        "quick_prompt_debt": "What should I do about my debts right now?",
        "quick_prompt_cashflow": "How is my cash flow this month?",
        "period_days": "days",
        "period_weeks": "weeks",
        "label_overall_budget": "Overall budget",
        "cta_complete_habit": "Complete habit",
        "cta_view_habit": "View habit",
        "cta_review_tasks": "Review tasks",
        "cta_resolve_overdue": "Resolve overdue tasks",
        "cta_prioritize_tasks": "Prioritize tasks",
        "cta_update_tracker": "Update tracker",
        "cta_review_trend": "Review trend",
        "cta_keep_it_up": "Keep it up",
        "cta_review_finances": "Review finances",
        "cta_review_budget": "Review budgets",
        "cta_review_debts": "Review debts",
        "cta_check_cash_flow": "Check cash flow",
        "rec_habit_catchup_title": "Catch up on {name}",
        "rec_habit_catchup_message": (
            "You missed {name} for {count} {period} in a row. Completing it now will restart momentum."
        ),
        "rec_habit_streak_title": "{name} streak milestone",
        "rec_habit_streak_message": "Your {name} streak reached {count}. Keep going to level up your badge.",
        "rec_task_high_priority_title": "High-priority tasks due today",
        "rec_task_high_priority_message": (
            "You have {count} high-priority tasks due today. Finish them first to avoid rollover."
        ),
        "rec_task_overdue_title": "Overdue tasks need attention",
        "rec_task_overdue_message": (
            "{count} tasks are overdue. Clearing the oldest one first will reduce pressure fastest."
        ),
        "rec_task_heavy_load_title": "Heavy task load this week",
        "rec_task_heavy_load_message": (
            "You have {count} open tasks. Group the finance tasks first so nothing urgent gets buried."
        ),
        "rec_tracker_checkin_title": "{name} needs a fresh check-in",
        "rec_tracker_checkin_message": (
            "The latest {name} update was marked incomplete. Logging a win today will rebuild consistency."
        ),
        "rec_tracker_below_title": "{name} is trending below target",
        "rec_tracker_below_message": (
            "Projected next value is {projected}, below your target of {target}. Adjust habits or tasks to recover."
        ),
        "rec_tracker_overshoot_title": "{name} may overshoot",
        "rec_tracker_overshoot_message": (
            "Projected next value is {projected}, above your limit of {target}. Consider reducing the drivers behind it."
        ),
        "rec_steady_title": "Everything looks steady",
        "rec_steady_message": (
            "Your habits, tasks, and trackers are in a healthy range right now. Keep logging consistently to improve future predictions."
        ),
        "rec_finance_top_spend_title": "Most spending is going to {category}",
        "rec_finance_top_spend_message": (
            "{category} accounts for about {share}% of this month's spending with {amount}. Review that category first if you want to free up money quickly."
        ),
        "rec_budget_over_title": "{category} budget is over limit",
        "rec_budget_over_message": (
            "You spent {spent} against a limit of {limit}. Pause new spending there and trim the next flexible expense first."
        ),
        "rec_budget_near_title": "{category} budget is almost full",
        "rec_budget_near_message": (
            "{percent}% of this budget is already used. Slow this category down before it spills over."
        ),
        "rec_cashflow_negative_title": "Expenses are ahead of income this month",
        "rec_cashflow_negative_message": (
            "Income is {income} and expenses are {expenses}, leaving net cash flow at {net}. Cut the biggest flexible category before the gap grows."
        ),
        "rec_debt_overdue_title": "Overdue debts need action",
        "rec_debt_overdue_message": (
            "{count} debts are overdue, totaling {amount}. Update the repayment plan today so the pressure does not compound."
        ),
        "rec_debt_due_soon_title": "Debt due dates are coming up",
        "rec_debt_due_soon_message": (
            "{count} debts are due within 24 hours. Prepare the payment or contact the counterparty before the deadline."
        ),
        "proactive_daily": "Automatic check-in for today: {title}. {message}",
        "tracker_pulse_current": "{name} is currently {trend}.",
        "tracker_pulse_next": "Next value may be {value}.",
        "proactive_tracker_pulse": "Tracker pulse: {note}",
        "proactive_steady": (
            "Automatic check-in for today: everything looks steady. Keep logging to help me give sharper advice."
        ),
        "trend_up": "trending up",
        "trend_down": "trending down",
        "trend_stable": "stable",
        "plan_oldest_overdue": "Clear the oldest overdue task first: {names}.",
        "plan_highest_priority": "Start with the highest-priority open task: {names}.",
        "plan_due_habits": "Check in on your due habits: {names}.",
        "plan_review_tracker": "Review your {name} tracker because it is {trend}.",
        "plan_projected_against": "Projected next value: {projected} against target {target}.",
        "plan_keep_logging": "Keep logging habits, tasks, and trackers so I can give you sharper guidance.",
        "reply_trend_current": "{name} is {trend}.",
        "reply_trend_latest": "Latest value: {value}.",
        "reply_trend_projected": "Projected next value: {value}.",
        "reply_trend_target": "Your target is {direction} {target}.",
        "direction_at_least": "at least",
        "direction_at_most": "at most",
        "reply_trend_next_step": (
            "Best next step: log a fresh update today and adjust the habit or task that influences this tracker most."
        ),
        "reply_habits_due": (
            "You have {count} habits waiting for check-in: {names}. Complete the easiest one first to rebuild momentum, then continue with the rest."
        ),
        "reply_habits_caught_up": (
            "All tracked habits look caught up right now. Keep the streak alive by checking in at your usual time."
        ),
        "reply_tasks_overdue": (
            "The most urgent tasks are overdue: {labels}. Start with the oldest overdue task, then move to high-priority items due today."
        ),
        "reply_tasks_open": (
            "Your next open tasks are {names}. I would tackle the highest-priority one first and batch related work together."
        ),
        "reply_tasks_none": (
            "You do not have pending tasks right now. This is a good moment to add the next meaningful action."
        ),
        "reply_finance_top_category": (
            "Your biggest spending category this month is {category}: {amount}, about {share}% of tracked expenses. First step: look for one expense there you can cut, delay, or replace this week."
        ),
        "reply_finance_budget_alerts": (
            "The budgets that need attention now are {items}. Start with the category under the most pressure and pause one non-essential expense there."
        ),
        "reply_finance_budget_healthy": (
            "Your budgets look under control right now. Keep an eye on the top spending category so it does not accelerate late in the period."
        ),
        "reply_finance_debt_urgent": (
            "You have {count} overdue debts totaling {amount}. Best move now: contact the counterparties, confirm dates, and update the return plan today."
        ),
        "reply_finance_debt_due_soon": (
            "{count} debts are due soon. Line up the payment now or warn the counterparty early if you need to adjust the timing."
        ),
        "reply_finance_debt_position": (
            "You currently have {lent_count} open lent debts totaling {lent_amount} and {borrowed_count} open borrowed debts totaling {borrowed_amount}. Keep the next due date visible so cash flow stays predictable."
        ),
        "reply_finance_debt_clear": (
            "You do not have urgent debt deadlines right now. This is a good time to plan the next repayment before it becomes stressful."
        ),
        "reply_finance_cashflow": (
            "This month income is {income}, expenses are {expenses}, and net cash flow is {net}. {follow_up}"
        ),
        "reply_finance_cashflow_followup_top": "The fastest place to review is {category}, because that is where the largest share of spending is going.",
        "reply_finance_cashflow_followup_plain": "The best next move is to review the largest flexible expense before it repeats.",
        "reply_finance_overview": (
            "Here is the clearest finance snapshot right now: {parts}. If you want, I can turn this into a spending cut plan, budget recovery plan, or debt action plan."
        ),
        "reply_advice_intro": "Here is the clearest advice from your current data.",
        "reply_advice_write_back": (
            "Write back if you want a habit-focused, task-focused, or tracker-focused plan."
        ),
        "reply_no_urgent": (
            "I do not see any urgent signals right now. Keep logging consistently and ask me for a daily plan whenever you need one."
        ),
    },
    "ru": {
        "welcome_message": (
            "Я ваш ИИ помощник. Напишите мне про приоритеты, привычки, задачи или тренды трекеров, "
            "и я превращу ваши данные в простой план действий."
        ),
        "quick_prompt_focus_today": "На чем мне сосредоточиться сегодня?",
        "quick_prompt_habit_attention": "Какая привычка требует больше всего внимания?",
        "quick_prompt_tracker_results": "Как улучшить результаты моих трекеров?",
        "quick_prompt_short_plan": "Дай мне короткий план действий на сегодня.",
        "quick_prompt_overdue_task": "Какую просроченную задачу мне закрыть первой?",
        "quick_prompt_habits_back": "Как вернуться в ритм по привычкам?",
        "quick_prompt_explain_trend": "Объясни тренд по трекеру {name}.",
        "quick_prompt_spending": "Куда уходит больше всего денег в этом месяце?",
        "quick_prompt_budget": "Как сократить траты на этой неделе?",
        "quick_prompt_debt": "Что мне делать с долгами прямо сейчас?",
        "quick_prompt_cashflow": "Какой у меня денежный поток в этом месяце?",
        "period_days": "дней",
        "period_weeks": "недель",
        "label_overall_budget": "Общий бюджет",
        "cta_complete_habit": "Отметить привычку",
        "cta_view_habit": "Открыть привычку",
        "cta_review_tasks": "Посмотреть задачи",
        "cta_resolve_overdue": "Разобрать просроченные",
        "cta_prioritize_tasks": "Расставить приоритеты",
        "cta_update_tracker": "Обновить трекер",
        "cta_review_trend": "Посмотреть тренд",
        "cta_keep_it_up": "Продолжать в том же духе",
        "cta_review_finances": "Посмотреть финансы",
        "cta_review_budget": "Посмотреть бюджеты",
        "cta_review_debts": "Посмотреть долги",
        "cta_check_cash_flow": "Проверить денежный поток",
        "rec_habit_catchup_title": "Вернуться к привычке {name}",
        "rec_habit_catchup_message": (
            "Вы пропустили {name} уже {count} {period} подряд. Если отметить ее сейчас, будет легче вернуть темп."
        ),
        "rec_habit_streak_title": "Серия по привычке {name}",
        "rec_habit_streak_message": "Серия по {name} достигла {count}. Продолжайте, чтобы повысить свой значок.",
        "rec_task_high_priority_title": "Высокий приоритет на сегодня",
        "rec_task_high_priority_message": (
            "Сегодня у вас {count} задач с высоким приоритетом. Закройте их в первую очередь, чтобы они не перенеслись дальше."
        ),
        "rec_task_overdue_title": "Просроченные задачи требуют внимания",
        "rec_task_overdue_message": (
            "{count} задач уже просрочены. Если закрыть самую старую первой, напряжение снизится быстрее всего."
        ),
        "rec_task_heavy_load_title": "Плотная нагрузка по задачам на этой неделе",
        "rec_task_heavy_load_message": (
            "У вас {count} открытых задач. Сначала сгруппируйте финансовые, чтобы срочное не потерялось."
        ),
        "rec_tracker_checkin_title": "Трекеру {name} нужна новая отметка",
        "rec_tracker_checkin_message": (
            "Последнее обновление по {name} было отмечено как неуспешное. Если зафиксировать результат сегодня, будет проще вернуть стабильность."
        ),
        "rec_tracker_below_title": "{name} уходит ниже цели",
        "rec_tracker_below_message": (
            "Следующее прогнозное значение {projected}, а ваша цель {target}. Стоит скорректировать привычки или задачи, которые влияют на этот трекер."
        ),
        "rec_tracker_overshoot_title": "{name} может выйти за предел",
        "rec_tracker_overshoot_message": (
            "Следующее прогнозное значение {projected}, а ваш лимит {target}. Подумайте, что можно уменьшить, чтобы вернуть показатель в норму."
        ),
        "rec_steady_title": "Все выглядит стабильно",
        "rec_steady_message": (
            "Сейчас привычки, задачи и трекеры выглядят здорово. Продолжайте отмечаться регулярно, чтобы прогнозы становились точнее."
        ),
        "rec_finance_top_spend_title": "Больше всего денег уходит на {category}",
        "rec_finance_top_spend_message": (
            "{category} забирает около {share}% расходов за этот месяц и уже составляет {amount}. Если хотите быстро высвободить деньги, начните проверку именно с этой категории."
        ),
        "rec_budget_over_title": "Бюджет по категории {category} превышен",
        "rec_budget_over_message": (
            "Потрачено {spent} при лимите {limit}. Остановите новые траты там и сначала сократите следующий гибкий расход."
        ),
        "rec_budget_near_title": "Бюджет по категории {category} почти заполнен",
        "rec_budget_near_message": (
            "Уже использовано {percent}% бюджета. Сбавьте темп в этой категории, пока она не вышла за предел."
        ),
        "rec_cashflow_negative_title": "Расходы обгоняют доход в этом месяце",
        "rec_cashflow_negative_message": (
            "Доход составляет {income}, расходы {expenses}, а чистый поток уже {net}. Сначала сократите самую крупную гибкую категорию, пока разрыв не вырос."
        ),
        "rec_debt_overdue_title": "Просроченные долги требуют действия",
        "rec_debt_overdue_message": (
            "Просрочено {count} долгов на сумму {amount}. Лучше сегодня же обновить план возврата, чтобы давление не росло дальше."
        ),
        "rec_debt_due_soon_title": "Сроки по долгам уже близко",
        "rec_debt_due_soon_message": (
            "{count} долгов нужно закрыть в ближайшие 24 часа. Подготовьте оплату или заранее свяжитесь со второй стороной."
        ),
        "proactive_daily": "Автоматическая проверка на сегодня: {title}. {message}",
        "tracker_pulse_current": "Сейчас {name} показывает состояние: {trend}.",
        "tracker_pulse_next": "Следующее значение может быть {value}.",
        "proactive_tracker_pulse": "Пульс трекера: {note}",
        "proactive_steady": (
            "Автоматическая проверка на сегодня: все выглядит стабильно. Продолжайте вносить данные, чтобы мои советы были точнее."
        ),
        "trend_up": "рост",
        "trend_down": "снижение",
        "trend_stable": "стабильность",
        "plan_oldest_overdue": "Сначала закройте самую старую просроченную задачу: {names}.",
        "plan_highest_priority": "Начните с самой приоритетной открытой задачи: {names}.",
        "plan_due_habits": "Отметьте привычки, которые ждут чек-ина: {names}.",
        "plan_review_tracker": "Посмотрите на трекер {name}, потому что сейчас по нему идет {trend}.",
        "plan_projected_against": "Следующее прогнозное значение: {projected} при цели {target}.",
        "plan_keep_logging": "Продолжайте отмечать привычки, задачи и трекеры, чтобы я мог давать более точные советы.",
        "reply_trend_current": "Сейчас по {name} наблюдается {trend}.",
        "reply_trend_latest": "Последнее значение: {value}.",
        "reply_trend_projected": "Следующее прогнозное значение: {value}.",
        "reply_trend_target": "Ваша цель: {direction} {target}.",
        "direction_at_least": "не меньше",
        "direction_at_most": "не больше",
        "reply_trend_next_step": (
            "Лучший следующий шаг: внесите новое обновление сегодня и поправьте привычку или задачу, которая сильнее всего влияет на этот трекер."
        ),
        "reply_habits_due": (
            "У вас {count} привычек ждут отметки: {names}. Сначала отметьте самую легкую, чтобы быстро вернуть темп, а затем переходите к остальным."
        ),
        "reply_habits_caught_up": (
            "По отслеживаемым привычкам сейчас все в порядке. Сохраните серию, отметившись в привычное для себя время."
        ),
        "reply_tasks_overdue": (
            "Самые срочные задачи уже просрочены: {labels}. Начните с самой старой просроченной, а потом переходите к высоким приоритетам на сегодня."
        ),
        "reply_tasks_open": (
            "Следующие открытые задачи: {names}. Я бы начал с той, у которой выше приоритет, и объединил похожие дела в один блок."
        ),
        "reply_tasks_none": (
            "Сейчас у вас нет открытых задач. Это хороший момент, чтобы добавить следующее важное действие."
        ),
        "reply_finance_top_category": (
            "Самая крупная категория расходов в этом месяце — {category}: {amount}, это примерно {share}% отслеживаемых трат. Первый шаг: найдите там одну покупку, которую можно сократить, отложить или заменить на этой неделе."
        ),
        "reply_finance_budget_alerts": (
            "Сейчас внимания требуют такие бюджеты: {items}. Начните с категории, где давление сильнее всего, и уберите там один необязательный расход."
        ),
        "reply_finance_budget_healthy": (
            "Сейчас бюджеты выглядят под контролем. Но все равно следите за самой крупной категорией расходов, чтобы она не ускорилась ближе к концу периода."
        ),
        "reply_finance_debt_urgent": (
            "У вас {count} просроченных долгов на сумму {amount}. Лучший следующий шаг — связаться со второй стороной, подтвердить даты и сегодня обновить план возврата."
        ),
        "reply_finance_debt_due_soon": (
            "{count} долгов скоро подойдут к сроку. Лучше заранее подготовить платеж или предупредить вторую сторону, если нужно сдвинуть дату."
        ),
        "reply_finance_debt_position": (
            "Сейчас у вас {lent_count} открытых выданных долгов на сумму {lent_amount} и {borrowed_count} открытых взятых долгов на сумму {borrowed_amount}. Держите ближайшую дату возврата на виду, чтобы денежный поток оставался предсказуемым."
        ),
        "reply_finance_debt_clear": (
            "Сейчас срочных долговых сроков нет. Это хороший момент заранее спланировать следующий возврат, пока ситуация спокойная."
        ),
        "reply_finance_cashflow": (
            "В этом месяце доход составляет {income}, расходы — {expenses}, а чистый денежный поток — {net}. {follow_up}"
        ),
        "reply_finance_cashflow_followup_top": "Быстрее всего стоит проверить категорию {category}, потому что именно туда уходит наибольшая доля расходов.",
        "reply_finance_cashflow_followup_plain": "Лучший следующий шаг — проверить самый крупный гибкий расход до того, как он повторится.",
        "reply_finance_overview": (
            "Вот самый понятный финансовый срез на сейчас: {parts}. Если хотите, я превращу это в план по сокращению трат, восстановлению бюджета или действиям по долгам."
        ),
        "reply_advice_intro": "Вот самый понятный вывод из ваших текущих данных.",
        "reply_advice_write_back": (
            "Напишите, если хотите отдельный план по привычкам, задачам или трекерам."
        ),
        "reply_no_urgent": (
            "Сейчас я не вижу срочных сигналов. Продолжайте отмечать данные и в любой момент попросите у меня план на день."
        ),
    },
    "kz": {
        "welcome_message": (
            "Мен сіздің ИИ көмекшіңізбін. Маған басымдықтар, әдеттер, тапсырмалар немесе трекер трендтері туралы жазыңыз, "
            "мен деректеріңізді қарапайым әрекет жоспарына айналдырамын."
        ),
        "quick_prompt_focus_today": "Бүгін неге назар аударуым керек?",
        "quick_prompt_habit_attention": "Қай әдетке көбірек көңіл бөлу керек?",
        "quick_prompt_tracker_results": "Трекер нәтижелерін қалай жақсартамын?",
        "quick_prompt_short_plan": "Бүгінге қысқа әрекет жоспарын берші.",
        "quick_prompt_overdue_task": "Қай мерзімі өткен тапсырманы бірінші бітіруім керек?",
        "quick_prompt_habits_back": "Әдеттер бойынша қалай қайта ырғаққа келемін?",
        "quick_prompt_explain_trend": "{name} трекерінің трендін түсіндіріп берші.",
        "quick_prompt_spending": "Осы айда ақша ең көп қайда кетіп жатыр?",
        "quick_prompt_budget": "Осы аптада шығынды қалай азайтамын?",
        "quick_prompt_debt": "Қазір қарыздар бойынша не істеуім керек?",
        "quick_prompt_cashflow": "Осы айдағы ақша ағымым қандай?",
        "period_days": "күн",
        "period_weeks": "апта",
        "label_overall_budget": "Жалпы бюджет",
        "cta_complete_habit": "Әдетті белгілеу",
        "cta_view_habit": "Әдетті ашу",
        "cta_review_tasks": "Тапсырмаларды қарау",
        "cta_resolve_overdue": "Мерзімі өткендерді реттеу",
        "cta_prioritize_tasks": "Басымдықтарды қою",
        "cta_update_tracker": "Трекерді жаңарту",
        "cta_review_trend": "Трендті қарау",
        "cta_keep_it_up": "Осы қарқынды сақтау",
        "cta_review_finances": "Қаржыны қарау",
        "cta_review_budget": "Бюджеттерді қарау",
        "cta_review_debts": "Қарыздарды қарау",
        "cta_check_cash_flow": "Ақша ағымын тексеру",
        "rec_habit_catchup_title": "{name} әдетіне қайта оралыңыз",
        "rec_habit_catchup_message": (
            "Сіз {name} әдетін {count} {period} қатарынан өткізіп алдыңыз. Қазір белгілесеңіз, қарқынды қайта бастау оңайырақ болады."
        ),
        "rec_habit_streak_title": "{name} әдетінің сериясы",
        "rec_habit_streak_message": "{name} бойынша серияңыз {count}-ке жетті. Белгіні көтеру үшін жалғастырыңыз.",
        "rec_task_high_priority_title": "Бүгінгі жоғары басымдықты тапсырмалар",
        "rec_task_high_priority_message": (
            "Бүгін сізде {count} жоғары басымдықты тапсырма бар. Кейінге қалдырмай, алдымен соларды аяқтаңыз."
        ),
        "rec_task_overdue_title": "Мерзімі өткен тапсырмалар назар сұрайды",
        "rec_task_overdue_message": (
            "{count} тапсырманың мерзімі өтіп кеткен. Ең ескісін бірінші жапсаңыз, қысым тезірек азаяды."
        ),
        "rec_task_heavy_load_title": "Осы аптада тапсырма жүктемесі көп",
        "rec_task_heavy_load_message": (
            "Сізде {count} ашық тапсырма бар. Шұғыл нәрсе көміліп қалмас үшін алдымен қаржылық тапсырмаларды топтаңыз."
        ),
        "rec_tracker_checkin_title": "{name} трекеріне жаңа белгілеу керек",
        "rec_tracker_checkin_message": (
            "{name} бойынша соңғы белгілеу сәтсіз болып тұр. Бүгін бір жақсы нәтижені тіркесеңіз, тұрақтылықты қайта жинау оңай болады."
        ),
        "rec_tracker_below_title": "{name} мақсаттан төмендеп барады",
        "rec_tracker_below_message": (
            "Келесі болжамды мән {projected}, ал мақсатыңыз {target}. Осы трекерге әсер ететін әдет не тапсырманы түзетіп көріңіз."
        ),
        "rec_tracker_overshoot_title": "{name} шектен асып кетуі мүмкін",
        "rec_tracker_overshoot_message": (
            "Келесі болжамды мән {projected}, ал шегіңіз {target}. Көрсеткішке әсер етіп тұрған факторларды азайтуды ойластырыңыз."
        ),
        "rec_steady_title": "Қазір бәрі тұрақты көрінеді",
        "rec_steady_message": (
            "Қазір әдеттер, тапсырмалар және трекерлер жақсы диапазонда. Болашақ болжамды күшейту үшін тұрақты түрде белгілеуді жалғастырыңыз."
        ),
        "rec_finance_top_spend_title": "Ақша ең көп {category} жағына кетіп жатыр",
        "rec_finance_top_spend_message": (
            "{category} осы айдағы шығынның шамамен {share}% алып отыр және қазірдің өзінде {amount} болды. Ақшаны тез босатқыңыз келсе, алдымен осы категорияны қарап шығыңыз."
        ),
        "rec_budget_over_title": "{category} бюджеті шектен асты",
        "rec_budget_over_message": (
            "{limit} лимитке қарсы {spent} жұмсалды. Ол жақта жаңа шығынды тоқтатып, келесі икемді шығынды бірінші қысқартыңыз."
        ),
        "rec_budget_near_title": "{category} бюджеті толуға жақын",
        "rec_budget_near_message": (
            "Бюджеттің {percent}% қолданылып қойды. Асып кетпей тұрып, осы категориядағы қарқынды баяулатыңыз."
        ),
        "rec_cashflow_negative_title": "Осы айда шығын табыстан асып барады",
        "rec_cashflow_negative_message": (
            "Табыс {income}, шығын {expenses}, ал таза ақша ағымы {net} болып тұр. Айырма үлкеймей тұрып, ең үлкен икемді категорияны қысқартыңыз."
        ),
        "rec_debt_overdue_title": "Мерзімі өткен қарыздармен айналысу керек",
        "rec_debt_overdue_message": (
            "{amount} сомасына {count} қарыздың мерзімі өтіп кеткен. Қысым көбеймей тұрып, бүгін қайтару жоспарын жаңартыңыз."
        ),
        "rec_debt_due_soon_title": "Қарыз мерзімдері жақындап қалды",
        "rec_debt_due_soon_message": (
            "{count} қарызды келесі 24 сағатта реттеу керек. Төлемді дайындаңыз немесе екінші тарапқа алдын ала хабар беріңіз."
        ),
        "proactive_daily": "Бүгінгі автоматты тексеру: {title}. {message}",
        "tracker_pulse_current": "Қазір {name} бойынша жағдай: {trend}.",
        "tracker_pulse_next": "Келесі мән {value} болуы мүмкін.",
        "proactive_tracker_pulse": "Трекер пульсі: {note}",
        "proactive_steady": (
            "Бүгінгі автоматты тексеру: қазір бәрі тұрақты. Кеңестерім дәлірек болуы үшін белгілеуді жалғастырыңыз."
        ),
        "trend_up": "өсім",
        "trend_down": "төмендеу",
        "trend_stable": "тұрақтылық",
        "plan_oldest_overdue": "Алдымен ең ескі мерзімі өткен тапсырманы жабыңыз: {names}.",
        "plan_highest_priority": "Ең жоғары басымдықты ашық тапсырмадан бастаңыз: {names}.",
        "plan_due_habits": "Белгілеуді күтіп тұрған әдеттерге кірісіңіз: {names}.",
        "plan_review_tracker": "{name} трекерін қарап шығыңыз, өйткені қазір онда {trend} байқалады.",
        "plan_projected_against": "Келесі болжамды мән: {projected}, ал мақсат: {target}.",
        "plan_keep_logging": "Мен дәлірек кеңес беруім үшін әдеттерді, тапсырмаларды және трекерлерді белгілеуді жалғастырыңыз.",
        "reply_trend_current": "Қазір {name} бойынша {trend} байқалады.",
        "reply_trend_latest": "Соңғы мән: {value}.",
        "reply_trend_projected": "Келесі болжамды мән: {value}.",
        "reply_trend_target": "Мақсатыңыз: {direction} {target}.",
        "direction_at_least": "кемінде",
        "direction_at_most": "көбі",
        "reply_trend_next_step": (
            "Ең дұрыс келесі қадам: бүгін жаңа белгілеу енгізіп, осы трекерге ең қатты әсер ететін әдетті не тапсырманы түзету."
        ),
        "reply_habits_due": (
            "Сізде белгілеуді күтіп тұрған {count} әдет бар: {names}. Қарқынды тез қайтару үшін алдымен ең жеңілінен бастаңыз, сосын қалғандарын жалғастырыңыз."
        ),
        "reply_habits_caught_up": (
            "Қазір бақыланатын әдеттердің бәрі орнында. Серияны сақтау үшін әдеттегі уақытыңызда белгілеуді ұмытпаңыз."
        ),
        "reply_tasks_overdue": (
            "Ең шұғыл тапсырмалар мерзімінен өтіп кеткен: {labels}. Алдымен ең ескісін жабыңыз, сосын бүгінгі жоғары басымдықтарға өтіңіз."
        ),
        "reply_tasks_open": (
            "Келесі ашық тапсырмалар: {names}. Мен алдымен басымдығы жоғарысын алып, ұқсас жұмыстарды бірге топтастырар едім."
        ),
        "reply_tasks_none": (
            "Қазір ашық тапсырмалар жоқ. Келесі маңызды әрекетті қосуға ыңғайлы сәт."
        ),
        "reply_finance_top_category": (
            "Осы айдағы ең үлкен шығын категориясы — {category}: {amount}, бұл бақыланған шығындардың шамамен {share}% құрайды. Бірінші қадам: сол жерден осы аптада қысқартуға, кейінге қалдыруға немесе ауыстыруға болатын бір шығын табыңыз."
        ),
        "reply_finance_budget_alerts": (
            "Қазір мына бюджеттерге назар керек: {items}. Қысым ең жоғары категориядан бастап, сол жердегі бір міндетті емес шығынды тоқтатыңыз."
        ),
        "reply_finance_budget_healthy": (
            "Қазір бюджеттер бақылауда көрінеді. Бірақ ай соңына қарай үдеп кетпеуі үшін ең үлкен шығын категориясын бәрібір бақылап отырыңыз."
        ),
        "reply_finance_debt_urgent": (
            "Сізде {amount} сомасына {count} мерзімі өткен қарыз бар. Ең дұрыс келесі қадам — екінші тараппен хабарласып, күндерді нақтылап, бүгін қайтару жоспарын жаңарту."
        ),
        "reply_finance_debt_due_soon": (
            "{count} қарыздың мерзімі жақындап тұр. Төлемді қазірден дайындаңыз немесе уақытты жылжыту керек болса, алдын ала хабар беріңіз."
        ),
        "reply_finance_debt_position": (
            "Қазір сізде жалпы {lent_amount} сомасына {lent_count} ашық берілген қарыз және {borrowed_amount} сомасына {borrowed_count} ашық алынған қарыз бар. Ақша ағымы болжамды болу үшін келесі қайтару күнін көз алдыңызда ұстаңыз."
        ),
        "reply_finance_debt_clear": (
            "Қазір шұғыл қарыз мерзімдері жоқ. Бұл келесі қайтаруды алдын ала жоспарлауға жақсы уақыт."
        ),
        "reply_finance_cashflow": (
            "Осы айда табыс {income}, шығын {expenses}, ал таза ақша ағымы {net}. {follow_up}"
        ),
        "reply_finance_cashflow_followup_top": "Ең алдымен {category} категориясын қараған дұрыс, өйткені шығынның ең үлкен үлесі сол жаққа кетіп жатыр.",
        "reply_finance_cashflow_followup_plain": "Ең дұрыс келесі қадам — қайталанбай тұрып, ең үлкен икемді шығынды қарап шығу.",
        "reply_finance_overview": (
            "Қазір ең анық қаржылық көрініс мынадай: {parts}. Қаласаңыз, осыны шығын қысқарту жоспарына, бюджет қалпына келтіру жоспарына немесе қарыз әрекет жоспарына айналдырып беремін."
        ),
        "reply_advice_intro": "Қазіргі деректеріңіз бойынша ең анық кеңес мынау.",
        "reply_advice_write_back": (
            "Қаласаңыз, әдеттерге, тапсырмаларға немесе трекерлерге бөлек жоспар да жасап беремін."
        ),
        "reply_no_urgent": (
            "Қазір шұғыл белгі көрініп тұрған жоқ. Белгілеуді жалғастырыңыз, керек кезде менен күндік жоспар сұрай аласыз."
        ),
    },
}


def _normalize_language(language):
    normalized = str(language or "en").strip().lower().split("-")[0]
    if normalized == "kk":
        normalized = "kz"
    return normalized if normalized in SUPPORTED_LANGUAGES else "en"


def _tr(language, key, **kwargs):
    normalized_language = _normalize_language(language)
    template = AI_COPY[normalized_language].get(key) or AI_COPY["en"].get(key) or key
    return template.format(**kwargs) if kwargs else template


def _localized_text(language, *, en, ru, kz):
    normalized_language = _normalize_language(language)
    if normalized_language == "ru":
        return ru
    if normalized_language == "kz":
        return kz
    return en


GENERAL_COACHING_RULES = [
    {
        "name": "motivation",
        "keywords": (
            "motivation", "motiv", "discipline", "discipl", "lazy", "procrast", "stuck",
            "мотива", "дисцип", "лень", "ленюсь", "прокраст", "застрял", "застряла",
            "мотивац", "тәртіп", "жалқа", "ерін", "кейінге", "бастай алмай", "ынта", "жігер",
        ),
        "responses": {
            "en": (
                "Motivation usually comes after action, not before it. Pick one step so small you can finish it in 10 minutes, start before you feel fully ready, and repeat it at the same time tomorrow. If you want, I can turn this into a simple routine."
            ),
            "ru": (
                "Мотивация чаще приходит после действия, а не до него. Выберите один шаг настолько маленький, чтобы закончить его за 10 минут, начните до того, как почувствуете полную готовность, и повторите это завтра в то же время. Если хотите, я соберу из этого простой режим."
            ),
            "kz": (
                "Мотивация көбіне әрекеттен кейін келеді, алдында емес. 10 минутта бітіре алатын өте кішкентай бір қадам таңдаңыз, толық дайын болмай тұрсаңыз да бастап кетіңіз, сосын ертең дәл сол уақытта қайталаңыз. Қаласаңыз, осыны қысқа күн тәртібіне айналдырып беремін."
            ),
        },
    },
    {
        "name": "stress",
        "keywords": (
            "stress", "anx", "worry", "overwhelm", "burnout",
            "стресс", "трев", "пережив", "выгора", "устал", "устала",
            "күйзел", "қобалж", "уайым", "шарша", "қысым",
        ),
        "responses": {
            "en": (
                "If you feel overloaded, shrink the problem to the next controllable step. Pause, take 5 slow breaths, write down the top 3 worries, and act only on the first one today. If you want, I can help you sort what needs action now and what can wait."
            ),
            "ru": (
                "Если вы чувствуете перегруз, сузьте проблему до следующего управляемого шага. Сделайте паузу, 5 медленных вдохов, запишите 3 главные тревоги и займитесь сегодня только первой. Если хотите, я помогу разделить, что делать сейчас, а что можно отложить."
            ),
            "kz": (
                "Егер бәрі тым ауыр болып тұрса, мәселені келесі басқаруға болатын бір қадамға дейін кішірейтіңіз. Кідіріп, 5 рет жай дем алыңыз, ең мазалайтын 3 нәрсені жазыңыз да, бүгін тек біріншісімен ғана айналысыңыз. Қаласаңыз, қазір істеу керек пен күте алатын дүниені бөліп беремін."
            ),
        },
    },
    {
        "name": "money",
        "keywords": (
            "money", "budget", "save", "saving", "debt", "income", "expense", "finance",
            "деньг", "бюдж", "долг", "доход", "расход", "финанс", "накоп",
            "ақша", "бюджет", "қарыз", "табыс", "шығын", "қаржы", "жинақ",
        ),
        "responses": {
            "en": (
                "When money feels confusing, start with clarity. Write down today's available cash, the fixed expenses due soon, and one thing you can cut, delay, or renegotiate this week. If you tell me the amount or situation, I can help you build a realistic mini-plan."
            ),
            "ru": (
                "Когда вопрос про деньги кажется запутанным, начните с ясности. Запишите, сколько денег доступно сегодня, какие фиксированные расходы скоро подойдут, и что одно вы можете сократить, отложить или пересмотреть на этой неделе. Если дадите сумму или ситуацию, я помогу собрать реалистичный мини-план."
            ),
            "kz": (
                "Ақшаға қатысты жағдай шатастырып тұрса, алдымен анықты енгізіңіз. Бүгін қолда бар соманы, жақында төленетін тұрақты шығындарды және осы аптада қысқартуға, кейінге қалдыруға немесе қайта келісуге болатын бір нәрсені жазыңыз. Сомаңызды не жағдайды айтсаңыз, мен нақты шағын жоспар жасап беремін."
            ),
        },
    },
    {
        "name": "focus",
        "keywords": (
            "focus", "study", "exam", "learn", "work", "career", "start",
            "фокус", "учеб", "экзам", "работ", "карьер", "не могу начать",
            "назар", "оқу", "емтихан", "жұмыс", "мансап", "бастау",
        ),
        "responses": {
            "en": (
                "If focus is the problem, make the task smaller than your resistance. Set one 25-minute block for a clear outcome, remove one distraction before you start, and stop only after that first block is done. Then decide whether you need another block or a real break."
            ),
            "ru": (
                "Если проблема в концентрации, сделайте задачу меньше вашего сопротивления. Поставьте один блок на 25 минут под конкретный результат, уберите один отвлекающий фактор до старта и завершите хотя бы этот первый блок. Потом решите, нужен ли еще один блок или полноценный перерыв."
            ),
            "kz": (
                "Егер мәселе назарда болса, тапсырманы өз қарсылығыңыздан да кішірек етіңіз. 25 минуттық бір блокты нақты нәтижеге арнаңыз, бастар алдында бір алаңдататын нәрсені алып тастаңыз және ең болмаса сол бірінші блокты бітіріңіз. Содан кейін тағы бір блок керек пе, әлде үзіліс керек пе, соны шешіңіз."
            ),
        },
    },
    {
        "name": "confidence",
        "keywords": (
            "confidence", "self-esteem", "insecure", "not good enough",
            "уверен", "самооцен", "неувер", "недостаточно хорош",
            "сенім", "өзіме сен", "сенімсіз", "жеткіліксіз",
        ),
        "responses": {
            "en": (
                "Confidence grows from evidence, not from waiting to feel ready. Choose one action today that proves competence, finish it fully, and write down what went well afterward. Small repeated wins change how you trust yourself."
            ),
            "ru": (
                "Уверенность растет из доказательств, а не из ожидания правильного чувства. Выберите сегодня одно действие, которое подтвердит вашу компетентность, доведите его до конца и потом запишите, что получилось хорошо. Маленькие повторяющиеся победы меняют доверие к себе."
            ),
            "kz": (
                "Өзіңе сенім дайын сезімнен емес, нақты дәлелден өседі. Бүгін қабілетіңізді дәлелдейтін бір әрекет таңдаңыз, оны толық аяқтаңыз да, содан кейін не жақсы шыққанын жазып қойыңыз. Осындай қайталанатын шағын жеңістер өзіңізге деген сенімді өзгертеді."
            ),
        },
    },
    {
        "name": "relationship",
        "keywords": (
            "relationship", "partner", "boyfriend", "girlfriend", "family", "friend",
            "отношен", "партнер", "парень", "девуш", "семья", "друг",
            "қатынас", "серіктес", "жігіт", "қыз", "отбасы", "дос",
        ),
        "responses": {
            "en": (
                "Start with one honest sentence and one clear boundary. Focus on what you need, what you can offer, and what behavior is no longer okay for you. If you want, I can help you phrase the conversation calmly."
            ),
            "ru": (
                "Начните с одной честной фразы и одной понятной границы. Сфокусируйтесь на том, что вам нужно, что вы готовы дать и какое поведение для вас больше не подходит. Если хотите, я помогу спокойно сформулировать разговор."
            ),
            "kz": (
                "Бәрін бірден шешуге тырыспай, бір шынайы сөйлемнен және бір анық шектен бастаңыз. Сізге не керек, не бере аласыз және енді қандай мінез-құлыққа келіспейтініңізге назар аударыңыз. Қаласаңыз, осы әңгімені сабырлы түрде қалай бастауға болатынын бірге құрастырамын."
            ),
        },
    },
]

GENERAL_QUESTION_MARKERS = (
    "?", "how ", "what ", "why ", "can ", "should ", "help ", "i feel", "i am",
    "как ", "что ", "почему ", "мне ", "можно ", "стоит ", "помоги",
    "қалай", "не ", "неге", "маған", "көмек", "мен ",
)

GENERIC_GENERAL_RESPONSES = {
    "en": (
        "We can break this down without solving everything at once. Start with one small step around {topic} that you can finish today, make that step easier than you think it should be, and review the result tonight. If you want, I can turn this into a concrete 3-step plan."
    ),
    "ru": (
        "Это можно разобрать без попытки решить все сразу. Начните с одного маленького шага вокруг темы {topic}, который реально завершить сегодня, сделайте этот шаг проще, чем вам кажется правильным, и вечером оцените результат. Если хотите, я превращу это в конкретный план из 3 шагов."
    ),
    "kz": (
        "Мұны бірден түгел шешпей-ақ, бөліп қарастыруға болады. {topic} тақырыбы бойынша бүгін бітіруге болатын бір кішкентай қадамнан бастаңыз, сол қадамды өзіңіз ойлағаннан да жеңіл етіңіз, кешке нәтижесін қарап шығыңыз. Қаласаңыз, мен мұны нақты 3 қадамдық жоспарға айналдырып беремін."
    ),
}

GENERIC_TOPIC_FALLBACK = {
    "en": "your situation",
    "ru": "вашей ситуации",
    "kz": "жағдайыңыз",
}


def _extract_general_topic(question):
    cleaned = re.sub(r"[^\w\s\u0400-\u04FF\u0600-\u06FF-]", " ", str(question or ""), flags=re.UNICODE)
    cleaned = re.sub(
        r"\b(how|what|why|can|should|help|please|i|me|my|как|что|почему|мне|мен|маған|қалай|не|неге|көмек)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    normalized = " ".join(cleaned.split())
    if not normalized:
        return None
    return " ".join(normalized.split()[:6])


def _build_general_coaching_reply(question, language="en"):
    language = _normalize_language(language)
    question_lower = str(question or "").strip().lower()
    if not question_lower:
        return None

    for rule in GENERAL_COACHING_RULES:
        if any(keyword in question_lower for keyword in rule["keywords"]):
            return rule["responses"][language]

    if len(question_lower) < 12:
        return None

    if any(marker in question_lower for marker in GENERAL_QUESTION_MARKERS):
        topic = _extract_general_topic(question) or GENERIC_TOPIC_FALLBACK[language]
        return GENERIC_GENERAL_RESPONSES[language].format(topic=topic)

    return None


def _decimal_or_none(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _round_number(value):
    if value is None:
        return None
    return round(float(value), 2)


def _numeric_projection(values):
    if len(values) < 2:
        return None, "stable"

    points = [Decimal(str(value)) for value in values]
    x_axis = list(range(len(points)))
    x_mean = Decimal(sum(x_axis)) / Decimal(len(x_axis))
    y_mean = sum(points) / Decimal(len(points))

    numerator = sum((Decimal(x) - x_mean) * (y - y_mean) for x, y in zip(x_axis, points))
    denominator = sum((Decimal(x) - x_mean) ** 2 for x in x_axis)
    slope = numerator / denominator if denominator else Decimal("0")
    projected_next = points[-1] + slope

    if slope > Decimal("0.1"):
        trend = "up"
    elif slope < Decimal("-0.1"):
        trend = "down"
    else:
        trend = "stable"
    return projected_next, trend


def build_tracker_trends(trackers):
    grouped = defaultdict(list)
    for tracker in trackers:
        grouped[tracker.name].append(tracker)

    trend_cards = []
    for name, entries in grouped.items():
        ordered = sorted(entries, key=lambda item: item.date)
        latest = ordered[-1]
        numeric_values = [
            _decimal_or_none(entry.value)
            for entry in ordered
            if not isinstance(entry.value, bool) and _decimal_or_none(entry.value) is not None
        ]
        bool_values = [entry.value for entry in ordered if isinstance(entry.value, bool)]
        projected_next, trend = _numeric_projection(numeric_values)

        streak = 0
        for value in reversed(bool_values):
            if value:
                streak += 1
            else:
                break

        trend_cards.append(
            {
                "name": name,
                "type": latest.tracker_type,
                "latest_value": latest.value,
                "latest_date": latest.date,
                "target_value": _round_number(latest.target_value),
                "goal_direction": latest.goal_direction,
                "projected_next_value": _round_number(projected_next),
                "trend": trend if numeric_values else ("up" if streak else "stable"),
                "success_streak": streak if bool_values else None,
                "entry_count": len(ordered),
            }
        )

    trend_cards.sort(key=lambda item: (item["name"].lower(), item["latest_date"]), reverse=False)
    return trend_cards


def _budget_label(budget, language):
    if budget.category_id and budget.category:
        return budget.category.name
    return _tr(language, "label_overall_budget")


def _combine_debt_due_at(debt):
    due_at = datetime.datetime.combine(debt.due_date, debt.due_time)
    if timezone.is_naive(due_at):
        return timezone.make_aware(due_at, timezone.get_current_timezone())
    return timezone.localtime(due_at)


def _budget_spent_amount(user, budget):
    queryset = exclude_debt_related_transactions(
        Transaction.objects.filter(
            user=user,
            type="expense",
            date__range=(budget.start_date, budget.end_date),
        )
    )
    if budget.category_id:
        queryset = queryset.filter(category=budget.category)
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def build_finance_snapshot(user, reference_date=None, language="en"):
    language = _normalize_language(language)
    today = reference_date or timezone.localdate()
    now = timezone.now()
    month_transactions = exclude_debt_related_transactions(
        Transaction.objects.filter(
            user=user,
            date__year=today.year,
            date__month=today.month,
        )
    )
    all_transactions = exclude_debt_related_transactions(
        Transaction.objects.filter(user=user).select_related("wallet", "category")
    )

    month_income = month_transactions.filter(type="income").aggregate(total=Sum("amount"))["total"] or Decimal("0")
    month_expenses = month_transactions.filter(type="expense").aggregate(total=Sum("amount"))["total"] or Decimal("0")

    top_category = None
    category_totals = list(
        month_transactions.filter(type="expense", category__isnull=False)
        .values("category__name")
        .annotate(amount=Sum("amount"))
        .order_by("-amount")
    )
    if category_totals:
        leader = category_totals[0]
        share_percent = 0.0
        if month_expenses:
            share_percent = round(float((leader["amount"] / month_expenses) * Decimal("100")), 1)
        top_category = {
            "name": leader["category__name"],
            "amount": _round_number(leader["amount"]),
            "share_percent": share_percent,
        }
    top_categories = []
    for item in category_totals[:5]:
        share_percent = 0.0
        if month_expenses:
            share_percent = round(float((item["amount"] / month_expenses) * Decimal("100")), 1)
        top_categories.append(
            {
                "name": item["category__name"],
                "amount": _round_number(item["amount"]),
                "share_percent": share_percent,
            }
        )

    active_budgets = list(
        Budget.objects.filter(user=user, start_date__lte=today, end_date__gte=today).select_related("category")
    )
    budget_alerts = []
    over_budget_count = 0
    near_budget_count = 0
    budget_details = []

    for budget in active_budgets:
        spent = _budget_spent_amount(user, budget)
        percent_used = 0.0
        if budget.limit:
            percent_used = round(float((spent / budget.limit) * Decimal("100")), 1)

        severity = None
        status = "ok"
        if budget.limit and spent > budget.limit:
            severity = "high"
            status = "over"
            over_budget_count += 1
        elif budget.limit and percent_used >= 85:
            severity = "medium"
            status = "near"
            near_budget_count += 1

        limit_value = _round_number(budget.limit)
        spent_value = _round_number(spent)
        remaining_value = _round_number((budget.limit or Decimal("0")) - spent)
        budget_details.append(
            {
                "name": _budget_label(budget, language),
                "limit": limit_value,
                "spent": spent_value,
                "remaining": remaining_value,
                "percent_used": percent_used,
                "status": status,
                "start_date": budget.start_date.isoformat(),
                "end_date": budget.end_date.isoformat(),
            }
        )

        if severity:
            budget_alerts.append(
                {
                    "name": _budget_label(budget, language),
                    "limit": limit_value,
                    "spent": spent_value,
                    "percent_used": percent_used,
                    "severity": severity,
                }
            )

    budget_alerts.sort(key=lambda item: (0 if item["severity"] == "high" else 1, -item["percent_used"]))
    budget_details.sort(
        key=lambda item: (
            0 if item["status"] == "over" else 1 if item["status"] == "near" else 2,
            -item["percent_used"],
        )
    )

    wallet_balances = [
        {
            "name": wallet.name,
            "balance": _round_number(wallet.balance),
        }
        for wallet in Wallet.objects.filter(user=user).order_by("-balance", "name")[:6]
    ]

    recent_transactions = []
    for item in all_transactions.order_by("-date", "-time")[:8]:
        recent_transactions.append(
            {
                "type": item.type,
                "amount": _round_number(item.amount),
                "category": item.category.name if item.category_id and item.category else None,
                "wallet": item.wallet.name if item.wallet_id and item.wallet else None,
                "date": item.date.isoformat() if item.date else None,
                "time": item.time.isoformat() if item.time else None,
                "comment": item.comment or "",
            }
        )

    open_debts = list(Debt.objects.filter(user=user, returned=False))
    overdue_debts = []
    due_soon_debts = []
    open_lent_debts = []
    open_borrowed_debts = []
    open_debt_items = []
    for debt in open_debts:
        if debt.type == "lent":
            open_lent_debts.append(debt)
        elif debt.type == "borrowed":
            open_borrowed_debts.append(debt)
        due_at = _combine_debt_due_at(debt)
        debt_status = "open"
        if due_at < now:
            overdue_debts.append(debt)
            debt_status = "overdue"
        elif due_at <= now + datetime.timedelta(hours=24):
            due_soon_debts.append(debt)
            debt_status = "due_soon"
        open_debt_items.append(
            {
                "type": debt.type,
                "counterparty": debt.counterparty,
                "amount": _round_number(debt.amount),
                "due_at": due_at.isoformat(),
                "status": debt_status,
            }
        )

    overdue_amount = sum((debt.amount for debt in overdue_debts), Decimal("0"))
    due_soon_amount = sum((debt.amount for debt in due_soon_debts), Decimal("0"))
    open_lent_amount = sum((debt.amount for debt in open_lent_debts), Decimal("0"))
    open_borrowed_amount = sum((debt.amount for debt in open_borrowed_debts), Decimal("0"))

    return {
        "month_income": _round_number(month_income),
        "month_expenses": _round_number(month_expenses),
        "net_cashflow": _round_number(month_income - month_expenses),
        "top_category": top_category,
        "top_categories": top_categories,
        "budget_alerts": budget_alerts[:3],
        "budget_details": budget_details[:6],
        "over_budget_count": over_budget_count,
        "near_budget_count": near_budget_count,
        "overdue_debt_count": len(overdue_debts),
        "due_soon_debt_count": len(due_soon_debts),
        "overdue_debt_amount": _round_number(overdue_amount),
        "due_soon_debt_amount": _round_number(due_soon_amount),
        "open_lent_debt_count": len(open_lent_debts),
        "open_borrowed_debt_count": len(open_borrowed_debts),
        "open_lent_debt_amount": _round_number(open_lent_amount),
        "open_borrowed_debt_amount": _round_number(open_borrowed_amount),
        "wallet_balances": wallet_balances,
        "recent_transactions": recent_transactions,
        "open_debts": open_debt_items[:6],
        "month_transaction_count": month_transactions.count(),
    }


def _build_app_context(today, now, habits, due_habits, missed_habits, tasks, pending_tasks, overdue_tasks, tracker_trends):
    return {
        "today": today.isoformat(),
        "due_habits": [
            {
                "name": habit.name,
                "frequency": habit.frequency,
                "streak_count": habit.streak_count,
            }
            for habit in due_habits[:5]
        ],
        "missed_habits": [
            {
                "name": habit.name,
                "missed_periods": missed_count,
                "frequency": habit.frequency,
            }
            for habit, missed_count in missed_habits[:5]
        ],
        "pending_tasks": [
            {
                "name": task.name,
                "priority": task.priority,
                "category": task.category,
                "due_at": timezone.localtime(task.due_date).isoformat() if task.due_date else None,
            }
            for task in pending_tasks[:6]
        ],
        "overdue_tasks": [
            {
                "name": task.name,
                "priority": task.priority,
                "category": task.category,
                "due_at": timezone.localtime(task.due_date).isoformat() if task.due_date else None,
            }
            for task in overdue_tasks[:5]
        ],
        "recent_trackers": [
            {
                "name": trend["name"],
                "trend": trend["trend"],
                "latest_value": trend["latest_value"],
                "projected_next_value": trend["projected_next_value"],
                "target_value": trend["target_value"],
                "goal_direction": trend["goal_direction"],
            }
            for trend in tracker_trends[:5]
        ],
        "counts": {
            "habit_count": len(habits),
            "task_count": len(tasks),
            "pending_tasks": len(pending_tasks),
            "overdue_tasks": len(overdue_tasks),
        },
        "generated_at": timezone.localtime(now).isoformat(),
    }


def generate_recommendations(user, language="en"):
    language = _normalize_language(language)
    today = timezone.localdate()
    now = timezone.now()

    habits = list(Habit.objects.filter(user=user).order_by("name"))
    tasks = list(Task.objects.filter(user=user).order_by("completed", "due_date"))
    trackers = list(Tracker.objects.filter(user=user).order_by("-date")[:100])
    tracker_trends = build_tracker_trends(trackers)
    finance_snapshot = build_finance_snapshot(user, reference_date=today, language=language)

    recommendations = []

    due_habits = [habit for habit in habits if habit.is_due(today)]
    missed_habits = [(habit, habit.missed_periods(today)) for habit in habits if habit.missed_periods(today) > 0]
    for habit, missed_count in missed_habits:
        severity = "high" if missed_count >= 2 else "medium"
        day_label = _tr(language, "period_weeks" if habit.frequency == "weekly" else "period_days")
        recommendations.append(
            {
                "id": f"habit-missed-{habit.id}",
                "module": "habit",
                "severity": severity,
                "title": _tr(language, "rec_habit_catchup_title", name=habit.name),
                "message": _tr(
                    language,
                    "rec_habit_catchup_message",
                    name=habit.name,
                    count=missed_count,
                    period=day_label,
                ),
                "cta": _tr(language, "cta_complete_habit"),
            }
        )

    milestone_streaks = [habit for habit in habits if habit.streak_count in {3, 7, 14, 30}]
    for habit in milestone_streaks:
        recommendations.append(
            {
                "id": f"habit-streak-{habit.id}-{habit.streak_count}",
                "module": "habit",
                "severity": "positive",
                "title": _tr(language, "rec_habit_streak_title", name=habit.name),
                "message": _tr(
                    language,
                    "rec_habit_streak_message",
                    name=habit.name,
                    count=habit.streak_count,
                ),
                "cta": _tr(language, "cta_view_habit"),
            }
        )

    pending_tasks = [task for task in tasks if not task.completed]
    overdue_tasks = [task for task in pending_tasks if task.is_overdue(now)]
    high_priority_today = [
        task
        for task in pending_tasks
        if task.priority == "high" and timezone.localtime(task.due_date).date() == today
    ]
    if high_priority_today:
        recommendations.append(
            {
                "id": "task-high-priority-today",
                "module": "task",
                "severity": "high",
                "title": _tr(language, "rec_task_high_priority_title"),
                "message": _tr(
                    language,
                    "rec_task_high_priority_message",
                    count=len(high_priority_today),
                ),
                "cta": _tr(language, "cta_review_tasks"),
            }
        )
    if overdue_tasks:
        recommendations.append(
            {
                "id": "task-overdue",
                "module": "task",
                "severity": "high",
                "title": _tr(language, "rec_task_overdue_title"),
                "message": _tr(language, "rec_task_overdue_message", count=len(overdue_tasks)),
                "cta": _tr(language, "cta_resolve_overdue"),
            }
        )
    elif len(pending_tasks) >= 5:
        recommendations.append(
            {
                "id": "task-heavy-load",
                "module": "task",
                "severity": "medium",
                "title": _tr(language, "rec_task_heavy_load_title"),
                "message": _tr(language, "rec_task_heavy_load_message", count=len(pending_tasks)),
                "cta": _tr(language, "cta_prioritize_tasks"),
            }
        )

    for trend in tracker_trends:
        target_value = _decimal_or_none(trend["target_value"])
        projected_value = _decimal_or_none(trend["projected_next_value"])
        latest_value = trend["latest_value"]

        if isinstance(latest_value, bool):
            if trend["success_streak"] == 0:
                recommendations.append(
                    {
                        "id": f"tracker-checkin-{trend['name']}",
                        "module": "tracker",
                        "severity": "medium",
                        "title": _tr(language, "rec_tracker_checkin_title", name=trend["name"]),
                        "message": _tr(language, "rec_tracker_checkin_message", name=trend["name"]),
                        "cta": _tr(language, "cta_update_tracker"),
                    }
                )
            continue

        if target_value is None or projected_value is None:
            continue

        if trend["goal_direction"] == "at_least" and projected_value < target_value:
            recommendations.append(
                {
                    "id": f"tracker-below-{trend['name']}",
                    "module": "tracker",
                    "severity": "high",
                    "title": _tr(language, "rec_tracker_below_title", name=trend["name"]),
                    "message": _tr(
                        language,
                        "rec_tracker_below_message",
                        projected=trend["projected_next_value"],
                        target=trend["target_value"],
                    ),
                    "cta": _tr(language, "cta_review_trend"),
                }
            )
        if trend["goal_direction"] == "at_most" and projected_value > target_value:
            recommendations.append(
                {
                    "id": f"tracker-overshoot-{trend['name']}",
                    "module": "tracker",
                    "severity": "high",
                    "title": _tr(language, "rec_tracker_overshoot_title", name=trend["name"]),
                    "message": _tr(
                        language,
                        "rec_tracker_overshoot_message",
                        projected=trend["projected_next_value"],
                        target=trend["target_value"],
                    ),
                    "cta": _tr(language, "cta_review_trend"),
                }
            )

    top_category = finance_snapshot.get("top_category") or {}
    if top_category.get("name") and top_category.get("share_percent", 0) >= 30:
        recommendations.append(
            {
                "id": "finance-top-category",
                "module": "finance",
                "severity": "medium",
                "title": _tr(language, "rec_finance_top_spend_title", category=top_category["name"]),
                "message": _tr(
                    language,
                    "rec_finance_top_spend_message",
                    category=top_category["name"],
                    share=top_category["share_percent"],
                    amount=top_category["amount"],
                ),
                "cta": _tr(language, "cta_review_finances"),
            }
        )

    for index, alert in enumerate(finance_snapshot.get("budget_alerts") or []):
        if alert["severity"] == "high":
            recommendations.append(
                {
                    "id": f"budget-over-{index}",
                    "module": "finance",
                    "severity": "high",
                    "title": _tr(language, "rec_budget_over_title", category=alert["name"]),
                    "message": _tr(
                        language,
                        "rec_budget_over_message",
                        spent=alert["spent"],
                        limit=alert["limit"],
                    ),
                    "cta": _tr(language, "cta_review_budget"),
                }
            )
        else:
            recommendations.append(
                {
                    "id": f"budget-near-{index}",
                    "module": "finance",
                    "severity": "medium",
                    "title": _tr(language, "rec_budget_near_title", category=alert["name"]),
                    "message": _tr(
                        language,
                        "rec_budget_near_message",
                        percent=alert["percent_used"],
                    ),
                    "cta": _tr(language, "cta_review_budget"),
                }
            )

    if (finance_snapshot.get("net_cashflow") or 0) < 0:
        recommendations.append(
            {
                "id": "finance-negative-cashflow",
                "module": "finance",
                "severity": "high",
                "title": _tr(language, "rec_cashflow_negative_title"),
                "message": _tr(
                    language,
                    "rec_cashflow_negative_message",
                    income=finance_snapshot.get("month_income", 0),
                    expenses=finance_snapshot.get("month_expenses", 0),
                    net=finance_snapshot.get("net_cashflow", 0),
                ),
                "cta": _tr(language, "cta_check_cash_flow"),
            }
        )

    if finance_snapshot.get("overdue_debt_count"):
        recommendations.append(
            {
                "id": "finance-overdue-debts",
                "module": "finance",
                "severity": "high",
                "title": _tr(language, "rec_debt_overdue_title"),
                "message": _tr(
                    language,
                    "rec_debt_overdue_message",
                    count=finance_snapshot.get("overdue_debt_count", 0),
                    amount=finance_snapshot.get("overdue_debt_amount", 0),
                ),
                "cta": _tr(language, "cta_review_debts"),
            }
        )
    elif finance_snapshot.get("due_soon_debt_count"):
        recommendations.append(
            {
                "id": "finance-due-soon-debts",
                "module": "finance",
                "severity": "medium",
                "title": _tr(language, "rec_debt_due_soon_title"),
                "message": _tr(
                    language,
                    "rec_debt_due_soon_message",
                    count=finance_snapshot.get("due_soon_debt_count", 0),
                ),
                "cta": _tr(language, "cta_review_debts"),
            }
        )

    if not recommendations:
        recommendations.append(
            {
                "id": "steady",
                "module": "ai",
                "severity": "positive",
                "title": _tr(language, "rec_steady_title"),
                "message": _tr(language, "rec_steady_message"),
                "cta": _tr(language, "cta_keep_it_up"),
            }
        )

    recommendations.sort(key=lambda item: (SEVERITY_WEIGHT.get(item["severity"], 99), item["title"].lower()))

    summary = {
        "habit_count": len(habits),
        "habits_due": len(due_habits),
        "missed_habits": len(missed_habits),
        "task_count": len(tasks),
        "pending_tasks": len(pending_tasks),
        "overdue_tasks": len(overdue_tasks),
        "high_priority_due_today": len(high_priority_today),
        "tracker_entry_count": len(trackers),
        "active_tracker_names": len({tracker.name for tracker in trackers}),
        "month_income": finance_snapshot.get("month_income", 0),
        "month_expenses": finance_snapshot.get("month_expenses", 0),
        "net_cashflow": finance_snapshot.get("net_cashflow", 0),
        "over_budget_budgets": finance_snapshot.get("over_budget_count", 0),
        "near_limit_budgets": finance_snapshot.get("near_budget_count", 0),
        "overdue_debts": finance_snapshot.get("overdue_debt_count", 0),
        "due_soon_debts": finance_snapshot.get("due_soon_debt_count", 0),
    }
    app_context = _build_app_context(
        today,
        now,
        habits,
        due_habits,
        missed_habits,
        tasks,
        pending_tasks,
        overdue_tasks,
        tracker_trends,
    )

    return {
        "generated_at": timezone.now(),
        "summary": summary,
        "recommendations": recommendations[:10],
        "tracker_trends": tracker_trends,
        "finance_snapshot": finance_snapshot,
        "app_context": app_context,
    }


def _sanitize_source_key(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "")).strip("_")[:110]


def build_quick_prompts(ai_payload, language="en"):
    language = _normalize_language(language)
    prompts = [_tr(language, key) for key in BASE_PROMPT_KEYS]
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    if ai_payload["summary"].get("overdue_tasks"):
        prompts.insert(1, _tr(language, "quick_prompt_overdue_task"))
    if ai_payload["summary"].get("missed_habits"):
        prompts.insert(1, _tr(language, "quick_prompt_habits_back"))
    if ai_payload["tracker_trends"]:
        prompts.append(
            _tr(
                language,
                "quick_prompt_explain_trend",
                name=ai_payload["tracker_trends"][0]["name"],
            )
        )
    if (finance_snapshot.get("top_category") or {}).get("name"):
        prompts.append(_tr(language, "quick_prompt_spending"))
    if finance_snapshot.get("over_budget_count") or finance_snapshot.get("near_budget_count"):
        prompts.append(_tr(language, "quick_prompt_budget"))
    if finance_snapshot.get("overdue_debt_count") or finance_snapshot.get("due_soon_debt_count"):
        prompts.append(_tr(language, "quick_prompt_debt"))
    if finance_snapshot.get("month_income") or finance_snapshot.get("month_expenses"):
        prompts.append(_tr(language, "quick_prompt_cashflow"))

    deduped = []
    seen = set()
    for prompt in prompts:
        normalized = prompt.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(prompt)
    return deduped[:5]


def _compose_daily_proactive_messages(ai_payload, language="en"):
    language = _normalize_language(language)
    today = timezone.localdate().isoformat()
    recommendations = ai_payload.get("recommendations") or []
    tracker_trends = ai_payload.get("tracker_trends") or []
    messages = []

    if recommendations:
        top_item = recommendations[0]
        messages.append(
            {
                "kind": "proactive",
                "source_key": f"daily-{today}-{_sanitize_source_key(top_item.get('id') or top_item['title'])}",
                "message": _tr(
                    language,
                    "proactive_daily",
                    title=top_item["title"],
                    message=top_item["message"],
                ),
            }
        )

    if tracker_trends:
        focus_trend = tracker_trends[0]
        trend_note = _tr(
            language,
            "tracker_pulse_current",
            name=focus_trend["name"],
            trend=_tr(language, f"trend_{focus_trend['trend']}",) if focus_trend["trend"] in {"up", "down", "stable"} else focus_trend["trend"],
        )
        if focus_trend["projected_next_value"] is not None:
            trend_note += " " + _tr(
                language,
                "tracker_pulse_next",
                value=focus_trend["projected_next_value"],
            )
        messages.append(
            {
                "kind": "proactive",
                "source_key": f"trend-{today}-{_sanitize_source_key(focus_trend['name'])}",
                "message": _tr(language, "proactive_tracker_pulse", note=trend_note),
            }
        )

    if not messages:
        messages.append(
            {
                "kind": "proactive",
                "source_key": f"steady-{today}",
                "message": _tr(language, "proactive_steady"),
            }
        )

    return messages[:2]


def build_default_ai_messages(ai_payload, language="en"):
    messages = [
        {
            "id": "welcome-v1",
            "role": "assistant",
            "kind": "system",
            "message": _tr(language, "welcome_message"),
        }
    ]
    for item in _compose_daily_proactive_messages(ai_payload, language=language):
        messages.append(
            {
                "id": item["source_key"] or f"proactive-{len(messages)}",
                "role": "assistant",
                "kind": item["kind"],
                "message": item["message"],
            }
        )
    return messages


def ensure_ai_messages_seeded(user, ai_payload=None, language="en"):
    ai_payload = ai_payload or generate_recommendations(user, language=language)
    existing_messages = {
        message.source_key: message
        for message in AIAssistantMessage.objects.filter(user=user).exclude(source_key="")
    }

    seed_messages = []
    messages_to_update = []
    welcome_message = _tr(language, "welcome_message")

    if "welcome-v1" not in existing_messages:
        seed_messages.append(
            AIAssistantMessage(
                user=user,
                role="assistant",
                kind="system",
                source_key="welcome-v1",
                message=welcome_message,
            )
        )
    elif existing_messages["welcome-v1"].message != welcome_message:
        existing_messages["welcome-v1"].message = welcome_message
        messages_to_update.append(existing_messages["welcome-v1"])

    for item in _compose_daily_proactive_messages(ai_payload, language=language):
        existing_message = existing_messages.get(item["source_key"])
        if existing_message:
            if existing_message.message != item["message"]:
                existing_message.message = item["message"]
                messages_to_update.append(existing_message)
            continue
        seed_messages.append(
            AIAssistantMessage(
                user=user,
                role="assistant",
                kind=item["kind"],
                source_key=item["source_key"],
                message=item["message"],
            )
        )

    if seed_messages:
        AIAssistantMessage.objects.bulk_create(seed_messages)
    if messages_to_update:
        AIAssistantMessage.objects.bulk_update(messages_to_update, ["message"])

    return ai_payload


def _format_task_due(task):
    return timezone.localtime(task.due_date).strftime("%d.%m %H:%M")


def _format_budget_alert_items(alerts):
    items = []
    for alert in alerts[:3]:
        items.append(f"{alert['name']} ({alert['percent_used']}%)")
    return ", ".join(items)


def _build_finance_health_note(finance_snapshot, language="en"):
    language = _normalize_language(language)
    snapshot = finance_snapshot or {}
    wallet_total = sum(
        Decimal(str(item.get("balance") or 0))
        for item in (snapshot.get("wallet_balances") or [])
    )
    top_category = snapshot.get("top_category") or {}
    note = _localized_text(
        language,
        en=(
            "Current app picture: monthly income {income}, expenses {expenses}, net cash flow {net}, "
            "wallet balances total about {wallet_total}."
        ),
        ru=(
            "Текущая картина в приложении: доход за месяц {income}, расходы {expenses}, чистый поток {net}, "
            "суммарный баланс кошельков около {wallet_total}."
        ),
        kz=(
            "Қосымшадағы қазіргі көрініс: айлық табыс {income}, шығын {expenses}, таза ақша ағымы {net}, "
            "әмияндардағы жалпы сома шамамен {wallet_total}."
        ),
    ).format(
        income=snapshot.get("month_income", 0),
        expenses=snapshot.get("month_expenses", 0),
        net=snapshot.get("net_cashflow", 0),
        wallet_total=_round_number(wallet_total),
    )

    extras = []
    if top_category.get("name"):
        extras.append(
            _localized_text(
                language,
                en="Top spending category right now is {name} at {amount} ({share}%).",
                ru="Самая крупная категория расходов сейчас — {name}: {amount} ({share}%).",
                kz="Қазір ең үлкен шығын категориясы — {name}: {amount} ({share}%).",
            ).format(
                name=top_category.get("name"),
                amount=top_category.get("amount"),
                share=top_category.get("share_percent"),
            )
        )
    if snapshot.get("overdue_debt_count"):
        extras.append(
            _localized_text(
                language,
                en="{count} debts are overdue, so any new long-term commitment needs caution.",
                ru="{count} долгов уже просрочены, поэтому к новым долгим обязательствам стоит подходить осторожно.",
                kz="{count} қарыздың мерзімі өтіп кеткен, сондықтан жаңа ұзақ міндеттемеге абайлап қараған дұрыс.",
            ).format(count=snapshot.get("overdue_debt_count", 0))
        )
    elif snapshot.get("over_budget_count"):
        extras.append(
            _localized_text(
                language,
                en="{count} active budgets are already over limit, so first free up room in spending.",
                ru="{count} активных бюджетов уже вышли за лимит, поэтому сначала нужно освободить место в расходах.",
                kz="{count} белсенді бюджет лимиттен асып тұр, сондықтан алдымен шығынға орын босату керек.",
            ).format(count=snapshot.get("over_budget_count", 0))
        )
    return " ".join([note, *extras]).strip()


def _build_wallet_history_reply(ai_payload, language="en"):
    language = _normalize_language(language)
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    wallet_balances = finance_snapshot.get("wallet_balances") or []
    recent_transactions = finance_snapshot.get("recent_transactions") or []
    if not wallet_balances and not recent_transactions:
        return None

    wallet_text = ", ".join(
        f"{item.get('name')}: {item.get('balance')}"
        for item in wallet_balances[:5]
    ) or _localized_text(
        language,
        en="no wallet data yet",
        ru="данных по кошелькам пока нет",
        kz="әмиян деректері әзірге жоқ",
    )
    transaction_text = " | ".join(
        f"{item.get('date')} {item.get('type')} {item.get('amount')} "
        f"{item.get('category') or item.get('comment') or ''}".strip()
        for item in recent_transactions[:5]
    ) or _localized_text(
        language,
        en="no recent transaction history yet",
        ru="пока нет свежей истории транзакций",
        kz="соңғы транзакция тарихы әзірге жоқ",
    )
    top_category = finance_snapshot.get("top_category") or {}
    top_line = ""
    if top_category.get("name"):
        top_line = _localized_text(
            language,
            en="Main spending pressure: {name} at {amount} ({share}%).",
            ru="Главное давление по расходам: {name} на {amount} ({share}%).",
            kz="Негізгі шығын қысымы: {name} — {amount} ({share}%).",
        ).format(
            name=top_category.get("name"),
            amount=top_category.get("amount"),
            share=top_category.get("share_percent"),
        )

    return _localized_text(
        language,
        en=(
            "Here is the clearest picture from your app right now.\n"
            "1. Wallet balances: {wallets}\n"
            "2. Recent transaction history: {transactions}\n"
            "3. {top_line}\n"
            "If you want, I can turn this into a monthly income/expense review or a spending cut plan."
        ),
        ru=(
            "Вот самая понятная картина по данным приложения сейчас.\n"
            "1. Балансы кошельков: {wallets}\n"
            "2. Последняя история транзакций: {transactions}\n"
            "3. {top_line}\n"
            "Если хотите, я превращу это в разбор доходов и расходов за месяц или план сокращения трат."
        ),
        kz=(
            "Қосымшаңыздағы ең анық көрініс қазір мынадай.\n"
            "1. Әмиян баланстары: {wallets}\n"
            "2. Соңғы транзакция тарихы: {transactions}\n"
            "3. {top_line}\n"
            "Қаласаңыз, осыны айлық табыс-шығын талдауына немесе шығынды қысқарту жоспарына айналдырып беремін."
        ),
    ).format(
        wallets=wallet_text,
        transactions=transaction_text,
        top_line=top_line or _localized_text(
            language,
            en="Top spending category is not clear yet.",
            ru="Самая затратная категория пока не выделяется.",
            kz="Ең көп шығын категориясы әзірге анық емес.",
        ),
    )


def _build_house_purchase_reply(ai_payload, language="en"):
    language = _normalize_language(language)
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    health_note = _build_finance_health_note(finance_snapshot, language=language)
    net_cashflow = finance_snapshot.get("net_cashflow") or 0
    caution_line = _localized_text(
        language,
        en="If your cash flow stays positive and debts are controlled, you can plan the purchase more aggressively.",
        ru="Если денежный поток остается положительным и долги под контролем, к покупке можно двигаться активнее.",
        kz="Егер ақша ағымы оң болып, қарыз бақылауда тұрса, сатып алуға батылырақ жоспар құруға болады.",
    )
    if net_cashflow <= 0 or finance_snapshot.get("overdue_debt_count") or finance_snapshot.get("over_budget_count"):
        caution_line = _localized_text(
            language,
            en="Right now I would first stabilize cash flow, overdue debts, or over-limit budgets before taking on a mortgage-sized commitment.",
            ru="Сейчас я бы сначала стабилизировал денежный поток, просроченные долги и бюджеты с превышением лимита, а потом уже брал обязательство уровня ипотеки.",
            kz="Қазір мен алдымен ақша ағымын, мерзімі өткен қарыздарды және лимиттен асқан бюджеттерді реттеп алып, содан кейін ғана ипотека сияқты үлкен міндеттеме алар едім.",
        )

    return _localized_text(
        language,
        en=(
            "Buying a home should start as a financial plan, not only as a wish.\n"
            "{health_note}\n"
            "1. Define the target: price range, city/area, and whether you need a mortgage or can buy with cash.\n"
            "2. Calculate the down payment, closing costs, repair budget, and keep an emergency reserve for at least 3-6 months.\n"
            "3. Check whether the future monthly payment fits safely into your cash flow after current expenses and debts.\n"
            "4. Clean up expensive debt, late payments, and unstable spending before applying for a mortgage.\n"
            "5. Compare mortgage rates, documents, insurance, developer risks, and total cost, not just the monthly payment.\n"
            "{caution_line}\n"
            "Next step: tell me your target price and time frame, and I can estimate a realistic savings or mortgage plan from your current numbers."
        ),
        ru=(
            "Покупку жилья лучше начинать как финансовый план, а не только как желание.\n"
            "{health_note}\n"
            "1. Определите цель: диапазон цены, район/город и нужен ли вам ипотечный кредит или покупка будет за наличные.\n"
            "2. Посчитайте первоначальный взнос, расходы на оформление, возможный ремонт и резерв хотя бы на 3-6 месяцев.\n"
            "3. Проверьте, вписывается ли будущий ежемесячный платеж в ваш денежный поток после текущих расходов и долгов.\n"
            "4. До ипотеки приведите в порядок дорогие долги, просрочки и нестабильные траты.\n"
            "5. Сравнивайте не только ежемесячный платеж, но и ставку, документы, страховку, риски застройщика и итоговую стоимость.\n"
            "{caution_line}\n"
            "Следующий шаг: скажите желаемую цену жилья и срок, и я помогу прикинуть реалистичный план накопления или ипотеки по вашим текущим цифрам."
        ),
        kz=(
            "Үй сатып алуды жай тілек ретінде емес, нақты қаржылық жоспар ретінде бастаған дұрыс.\n"
            "{health_note}\n"
            "1. Мақсатты нақтылаңыз: баға диапазоны, қала/аудан және ипотека керек пе, әлде қолма-қол алу жоспары ма.\n"
            "2. Бастапқы жарнаны, рәсімдеу шығындарын, жөндеу қорын және кемінде 3-6 айлық қауіпсіздік қорын есептеңіз.\n"
            "3. Қазіргі шығындар мен қарыздардан кейін болашақ айлық төлем сіздің ақша ағымыңызға қауіпсіз сия ма, соны тексеріңіз.\n"
            "4. Ипотекаға дейін қымбат қарыздарды, кешіктірілген төлемдерді және тұрақсыз шығындарды реттеңіз.\n"
            "5. Тек айлық төлемге емес, пайызға, құжаттарға, сақтандыруға, құрылысшы тәуекеліне және жалпы толық құнға қараңыз.\n"
            "{caution_line}\n"
            "Келесі қадам: үйдің шамамен бағасын және қанша уақытта алғыңыз келетінін жазыңыз, мен қазіргі сандарыңызға сүйеніп жинақ не ипотека жоспарын есептеп беремін."
        ),
    ).format(
        health_note=health_note,
        caution_line=caution_line,
    )


def _build_major_purchase_reply(question, ai_payload, language="en"):
    language = _normalize_language(language)
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    health_note = _build_finance_health_note(finance_snapshot, language=language)
    topic = _extract_general_topic(question) or _localized_text(
        language,
        en="this purchase",
        ru="эту покупку",
        kz="осы сатып алуды",
    )
    return _localized_text(
        language,
        en=(
            "For {topic}, make the decision through numbers first.\n"
            "{health_note}\n"
            "1. Write the full cost: purchase price, delivery, setup, maintenance, and a safety buffer.\n"
            "2. Decide whether you will pay from savings, monthly cash flow, or debt, and compare the real long-term cost.\n"
            "3. Check what has to be reduced, delayed, or renegotiated in your current spending so this purchase does not create pressure later.\n"
            "4. Set a deadline and a monthly amount for the goal, then review progress against your spending history.\n"
            "Next step: tell me the exact item and price, and I will turn it into a realistic saving or purchase plan."
        ),
        ru=(
            "По {topic} лучше принимать решение сначала через цифры.\n"
            "{health_note}\n"
            "1. Запишите полную стоимость: цена покупки, доставка, запуск, обслуживание и запас безопасности.\n"
            "2. Решите, будете платить из накоплений, из ежемесячного потока или через долг, и сравните реальную долгосрочную стоимость.\n"
            "3. Проверьте, что нужно сократить, отложить или пересмотреть в текущих расходах, чтобы эта покупка не создала давление позже.\n"
            "4. Поставьте срок и ежемесячную сумму на цель, затем отслеживайте прогресс через историю расходов.\n"
            "Следующий шаг: напишите точный предмет и цену, и я превращу это в реалистичный план накопления или покупки."
        ),
        kz=(
            "{topic} бойынша шешімді алдымен эмоциямен емес, сандармен қабылдаған дұрыс.\n"
            "{health_note}\n"
            "1. Толық құнын жазыңыз: сатып алу бағасы, жеткізу, іске қосу, күтім және қауіпсіздік қоры.\n"
            "2. Ақшаны жинақтан төлейсіз бе, айлық ақша ағымынан ба, әлде қарыз арқылы ма — соның ұзақ мерзімді нақты құнын салыстырыңыз.\n"
            "3. Бұл сатып алу кейін қысым тудырмауы үшін қазіргі шығыннан нені қысқарту, кейінге қалдыру немесе қайта қарау керек екенін анықтаңыз.\n"
            "4. Мерзім мен ай сайынғы мақсат сомасын қойып, прогресті шығын тарихыңызбен салыстырып отырыңыз.\n"
            "Келесі қадам: нақты затты және бағасын жазыңыз, мен оны шынайы жинақ не сатып алу жоспарына айналдырып беремін."
        ),
    ).format(topic=topic, health_note=health_note)


def _build_contextual_general_reply(question, ai_payload, language="en"):
    language = _normalize_language(language)
    topic = _extract_general_topic(question) or _localized_text(
        language,
        en="your situation",
        ru="вашу ситуацию",
        kz="жағдайыңызды",
    )
    summary = ai_payload.get("summary") or {}
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    app_note_parts = []
    if summary.get("overdue_tasks"):
        app_note_parts.append(
            _localized_text(
                language,
                en="{count} overdue tasks are already competing for your attention.",
                ru="У вас уже есть {count} просроченных задач, которые забирают внимание.",
                kz="Қазірдің өзінде назарыңызды алып тұрған {count} мерзімі өткен тапсырма бар.",
            ).format(count=summary.get("overdue_tasks", 0))
        )
    if finance_snapshot.get("net_cashflow") is not None and (
        finance_snapshot.get("month_income") or finance_snapshot.get("month_expenses")
    ):
        app_note_parts.append(_build_finance_health_note(finance_snapshot, language=language))
    app_note = " ".join(app_note_parts).strip()

    return _localized_text(
        language,
        en=(
            "Here is the clearest way to approach {topic}.\n"
            "1. Define the exact outcome you want and the deadline.\n"
            "2. Break it into the smallest next action you can finish today.\n"
            "3. Remove the biggest blocker: lack of time, lack of money, unclear priority, or fear of starting.\n"
            "{app_note}\n"
            "If you want, send me one more detail and I will turn this into a more exact step-by-step plan."
        ),
        ru=(
            "Вот самый понятный способ подойти к теме {topic}.\n"
            "1. Сначала определите точный результат и срок.\n"
            "2. Разбейте это на самый маленький следующий шаг, который реально завершить сегодня.\n"
            "3. Уберите главный блокер: нехватку времени, денег, неясный приоритет или страх начать.\n"
            "{app_note}\n"
            "Если хотите, дайте мне еще одну деталь, и я превращу это в более точный пошаговый план."
        ),
        kz=(
            "{topic} мәселесіне ең түсінікті жолмен былай жақындауға болады.\n"
            "1. Алдымен нақты нәтиже мен мерзімді анықтаңыз.\n"
            "2. Соны бүгін бітіруге болатын ең кішкентай келесі қадамға бөліңіз.\n"
            "3. Ең үлкен бөгетті алып тастаңыз: уақыт жетпеуі, ақша жетпеуі, басымдықтың анық еместігі немесе бастау қорқынышы.\n"
            "{app_note}\n"
            "Қаласаңыз, бір қосымша деталь жазыңыз, мен мұны одан да нақты қадамдық жоспарға айналдырып беремін."
        ),
    ).format(
        topic=topic,
        app_note=app_note or _localized_text(
            language,
            en="",
            ru="",
            kz="",
        ),
    ).strip()


def _build_finance_reply(question_lower, ai_payload, language="en"):
    language = _normalize_language(language)
    finance_snapshot = ai_payload.get("finance_snapshot") or {}
    top_category = finance_snapshot.get("top_category") or {}
    budget_alerts = finance_snapshot.get("budget_alerts") or []
    wallet_balances = finance_snapshot.get("wallet_balances") or []
    recent_transactions = finance_snapshot.get("recent_transactions") or []

    spending_keywords = (
        "money", "spend", "spending", "expense", "expenses", "category", "categories",
        "деньг", "трат", "расход", "категор",
        "ақша", "шығын", "категор",
    )
    budget_keywords = ("budget", "limit", "лимит", "бюдж", "бюджет", "лимит")
    debt_keywords = ("debt", "debts", "loan", "borrow", "lend", "долг", "қарыз")
    cashflow_keywords = (
        "cash flow", "cashflow", "income", "earn", "salary", "net",
        "денежный поток", "доход", "зарплат", "чист",
        "ақша ағымы", "табыс", "жалақы", "таза",
    )
    history_keywords = (
        "history", "transaction", "transactions", "wallet", "wallets",
        "истор", "транзак", "кошел",
        "тарих", "транзак", "әмиян",
    )
    finance_keywords = spending_keywords + budget_keywords + debt_keywords + cashflow_keywords

    if any(keyword in question_lower for keyword in history_keywords) and (wallet_balances or recent_transactions):
        return _build_wallet_history_reply(ai_payload, language=language)

    if any(keyword in question_lower for keyword in spending_keywords) and top_category.get("name"):
        return _tr(
            language,
            "reply_finance_top_category",
            category=top_category["name"],
            amount=top_category["amount"],
            share=top_category["share_percent"],
        )

    if any(keyword in question_lower for keyword in budget_keywords):
        if budget_alerts:
            return _tr(
                language,
                "reply_finance_budget_alerts",
                items=_format_budget_alert_items(budget_alerts),
            )
        return _tr(language, "reply_finance_budget_healthy")

    if any(keyword in question_lower for keyword in debt_keywords):
        if finance_snapshot.get("overdue_debt_count"):
            return _tr(
                language,
                "reply_finance_debt_urgent",
                count=finance_snapshot.get("overdue_debt_count", 0),
                amount=finance_snapshot.get("overdue_debt_amount", 0),
            )
        if finance_snapshot.get("due_soon_debt_count"):
            return _tr(
                language,
                "reply_finance_debt_due_soon",
                count=finance_snapshot.get("due_soon_debt_count", 0),
            )
        if finance_snapshot.get("open_lent_debt_count") or finance_snapshot.get("open_borrowed_debt_count"):
            return _tr(
                language,
                "reply_finance_debt_position",
                lent_count=finance_snapshot.get("open_lent_debt_count", 0),
                lent_amount=finance_snapshot.get("open_lent_debt_amount", 0),
                borrowed_count=finance_snapshot.get("open_borrowed_debt_count", 0),
                borrowed_amount=finance_snapshot.get("open_borrowed_debt_amount", 0),
            )
        return _tr(language, "reply_finance_debt_clear")

    if any(keyword in question_lower for keyword in cashflow_keywords):
        follow_up = _tr(language, "reply_finance_cashflow_followup_plain")
        if top_category.get("name"):
            follow_up = _tr(
                language,
                "reply_finance_cashflow_followup_top",
                category=top_category["name"],
            )
        return _tr(
            language,
            "reply_finance_cashflow",
            income=finance_snapshot.get("month_income", 0),
            expenses=finance_snapshot.get("month_expenses", 0),
            net=finance_snapshot.get("net_cashflow", 0),
            follow_up=follow_up,
        )

    if any(keyword in question_lower for keyword in finance_keywords):
        parts = []
        if top_category.get("name"):
            parts.append(f"{top_category['name']} = {top_category['amount']} ({top_category['share_percent']}%)")
        if budget_alerts:
            parts.append(_format_budget_alert_items(budget_alerts))
        if finance_snapshot.get("overdue_debt_count"):
            parts.append(
                f"{finance_snapshot['overdue_debt_count']} overdue debts for {finance_snapshot.get('overdue_debt_amount', 0)}"
            )
        elif finance_snapshot.get("due_soon_debt_count"):
            parts.append(f"{finance_snapshot['due_soon_debt_count']} debts due soon")
        elif finance_snapshot.get("open_lent_debt_count") or finance_snapshot.get("open_borrowed_debt_count"):
            parts.append(
                "open debts: "
                f"lent {finance_snapshot.get('open_lent_debt_amount', 0)} "
                f"across {finance_snapshot.get('open_lent_debt_count', 0)}, "
                f"borrowed {finance_snapshot.get('open_borrowed_debt_amount', 0)} "
                f"across {finance_snapshot.get('open_borrowed_debt_count', 0)}"
            )
        if finance_snapshot.get("month_income") or finance_snapshot.get("month_expenses"):
            parts.append(
                f"income {finance_snapshot.get('month_income', 0)}, expenses {finance_snapshot.get('month_expenses', 0)}, net {finance_snapshot.get('net_cashflow', 0)}"
            )
        if parts:
            return _tr(language, "reply_finance_overview", parts="; ".join(parts))

    return None


def _build_general_plan(due_habits, overdue_tasks, pending_tasks, tracker_trends, language="en"):
    language = _normalize_language(language)
    action_lines = []
    if overdue_tasks:
        action_lines.append(
            _tr(
                language,
                "plan_oldest_overdue",
                names=", ".join(task.name for task in overdue_tasks[:2]),
            )
        )
    elif pending_tasks:
        action_lines.append(
            _tr(
                language,
                "plan_highest_priority",
                names=", ".join(task.name for task in pending_tasks[:2]),
            )
        )

    if due_habits:
        action_lines.append(
            _tr(
                language,
                "plan_due_habits",
                names=", ".join(habit.name for habit in due_habits[:3]),
            )
        )

    if tracker_trends:
        focus_trend = tracker_trends[0]
        line = _tr(
            language,
            "plan_review_tracker",
            name=focus_trend["name"],
            trend=_tr(language, f"trend_{focus_trend['trend']}"),
        )
        if focus_trend["target_value"] is not None and focus_trend["projected_next_value"] is not None:
            line += " " + _tr(
                language,
                "plan_projected_against",
                projected=focus_trend["projected_next_value"],
                target=focus_trend["target_value"],
            )
        action_lines.append(line)

    if not action_lines:
        action_lines.append(_tr(language, "plan_keep_logging"))

    return "\n".join(f"{index}. {line}" for index, line in enumerate(action_lines[:3], start=1))


def _build_local_ai_chat_reply(user, question, ai_payload=None, language="en"):
    language = _normalize_language(language)
    ai_payload = ai_payload or generate_recommendations(user, language=language)
    now = timezone.now()
    today = timezone.localdate()
    question_lower = str(question or "").strip().lower()
    house_keywords = (
        "house", "home", "apartment", "flat", "mortgage", "property",
        "дом", "квартир", "жиль", "ипотек",
        "үй", "пәтер", "баспана", "ипотек",
    )
    purchase_keywords = (
        "buy", "purchase", "afford", "save for", "save up",
        "куп", "покуп", "накоп",
        "сатып", "алу", "жина",
    )

    habits = list(Habit.objects.filter(user=user).order_by("name"))
    tasks = list(Task.objects.filter(user=user).order_by("completed", "due_date", "-created_at"))
    due_habits = [habit for habit in habits if habit.is_due(today)]
    pending_tasks = [task for task in tasks if not task.completed]
    overdue_tasks = [task for task in pending_tasks if task.is_overdue(now)]
    tracker_trends = ai_payload.get("tracker_trends") or []
    recommendations = ai_payload.get("recommendations") or []

    tracker_name_map = {trend["name"].lower(): trend for trend in tracker_trends}
    matched_trend = next((trend for name, trend in tracker_name_map.items() if name in question_lower), None)

    if matched_trend:
        reply = [
            _tr(
                language,
                "reply_trend_current",
                name=matched_trend["name"],
                trend=_tr(language, f"trend_{matched_trend['trend']}"),
            ),
            _tr(language, "reply_trend_latest", value=matched_trend["latest_value"]),
        ]
        if matched_trend["projected_next_value"] is not None:
            reply.append(
                _tr(
                    language,
                    "reply_trend_projected",
                    value=matched_trend["projected_next_value"],
                )
            )
        if matched_trend["target_value"] is not None:
            direction = _tr(
                language,
                "direction_at_least" if matched_trend["goal_direction"] == "at_least" else "direction_at_most",
            )
            reply.append(
                _tr(
                    language,
                    "reply_trend_target",
                    direction=direction,
                    target=matched_trend["target_value"],
                )
            )
        reply.append(_tr(language, "reply_trend_next_step"))
        return " ".join(reply)

    if any(keyword in question_lower for keyword in house_keywords):
        return _build_house_purchase_reply(ai_payload, language=language)

    if any(keyword in question_lower for keyword in purchase_keywords) and any(
        marker in question_lower
        for marker in ("buy", "purchase", "сатып", "куп", "afford", "накоп", "жина")
    ):
        return _build_major_purchase_reply(question, ai_payload, language=language)

    finance_reply = _build_finance_reply(question_lower, ai_payload, language=language)
    if finance_reply:
        return finance_reply

    habit_keywords = ("habit", "habits", "привыч", "әдет")
    task_keywords = ("task", "tasks", "задач", "тапсыр")
    plan_keywords = ("focus", "today", "plan", "priority", "help", "кеңес", "бүгін", "сегодня")

    if any(keyword in question_lower for keyword in habit_keywords):
        if due_habits:
            due_names = ", ".join(habit.name for habit in due_habits[:3])
            return _tr(
                language,
                "reply_habits_due",
                count=len(due_habits),
                names=due_names,
            )
        return _tr(language, "reply_habits_caught_up")

    if any(keyword in question_lower for keyword in task_keywords):
        if overdue_tasks:
            overdue_labels = ", ".join(f"{task.name} ({_format_task_due(task)})" for task in overdue_tasks[:3])
            return _tr(
                language,
                "reply_tasks_overdue",
                labels=overdue_labels,
            )
        if pending_tasks:
            pending_labels = ", ".join(task.name for task in pending_tasks[:3])
            return _tr(
                language,
                "reply_tasks_open",
                names=pending_labels,
            )
        return _tr(language, "reply_tasks_none")

    if any(keyword in question_lower for keyword in plan_keywords):
        return _build_general_plan(
            due_habits,
            overdue_tasks,
            pending_tasks,
            tracker_trends,
            language=language,
        )

    general_reply = _build_general_coaching_reply(question, language=language)
    if general_reply:
        return general_reply

    if len(question_lower) >= 12:
        return _build_contextual_general_reply(question, ai_payload, language=language)

    if recommendations:
        focus_items = recommendations[:2]
        joined = " ".join(f"{item['title']}: {item['message']}" for item in focus_items)
        return f"{_tr(language, 'reply_advice_intro')} {joined} {_tr(language, 'reply_advice_write_back')}"

    return _tr(language, "reply_no_urgent")


def build_ai_chat_reply(user, question, ai_payload=None, history=None, language="en"):
    ai_payload = ai_payload or generate_recommendations(user, language=language)
    try:
        return generate_gemini_coach_reply(
            question,
            ai_payload=ai_payload,
            history=history or [],
            language=language,
        )
    except GeminiConfigurationError:
        logger.info("Gemini API key is not configured; falling back to local AI coach reply.")
    except GeminiServiceError as exc:
        logger.warning("Gemini reply failed; falling back to local AI coach reply. %s", exc)

    return _build_local_ai_chat_reply(user, question, ai_payload=ai_payload, language=language)
