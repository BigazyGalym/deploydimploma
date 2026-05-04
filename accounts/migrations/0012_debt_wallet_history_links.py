from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_user_is_support_agent"),
    ]

    operations = [
        migrations.AddField(
            model_name="debt",
            name="issued_transaction",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="issued_debts",
                to="accounts.transaction",
            ),
        ),
        migrations.AddField(
            model_name="debt",
            name="returned_transaction",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="returned_debts",
                to="accounts.transaction",
            ),
        ),
        migrations.AddField(
            model_name="debt",
            name="wallet",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="debts",
                to="accounts.wallet",
            ),
        ),
    ]
