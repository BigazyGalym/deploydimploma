from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_supportticket"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportChatMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sender", models.CharField(choices=[("user", "User"), ("admin", "Admin")], max_length=10)),
                ("message", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="support_messages",
                        to="accounts.user",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
