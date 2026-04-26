from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_rename_accounts_ai_user_id_44b2b8_idx_accounts_ai_user_id_f516d8_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_limit_subscription_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="limit_subscription_answer",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="limit_subscription_cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="limit_subscription_challenge",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="user",
            name="limit_subscription_challenge_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="limit_subscription_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
