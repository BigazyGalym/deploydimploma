from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_debt_time_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailOTP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(db_index=True, max_length=254)),
                ("code", models.CharField(max_length=6)),
                (
                    "purpose",
                    models.CharField(
                        choices=[("verify", "Verify Email"), ("reset", "Reset Password")],
                        max_length=16,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.user",
                    ),
                ),
            ],
        ),
    ]
