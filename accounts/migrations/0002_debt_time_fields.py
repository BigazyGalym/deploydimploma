from django.db import migrations, models
import datetime
import datetime as dt


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="debt",
            name="issued_date",
            field=models.DateField(default=dt.date.today),
        ),
        migrations.AddField(
            model_name="debt",
            name="issued_time",
            field=models.TimeField(default=datetime.time(9, 0)),
        ),
        migrations.AddField(
            model_name="debt",
            name="due_time",
            field=models.TimeField(default=datetime.time(18, 0)),
        ),
    ]
