from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_debt_wallet_history_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="is_limit_subscription_premium",
            field=models.BooleanField(default=False),
        ),
    ]
