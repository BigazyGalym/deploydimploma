from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_user_limit_subscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_support_agent",
            field=models.BooleanField(default=False),
        ),
    ]
