from django.db import migrations

def create_default_site(apps, schema_editor):
    try:
        Site = apps.get_model('sites', 'Site')
        if not Site.objects.filter(id=1).exists():
            Site.objects.create(id=1, domain='deploydimploma.onrender.com', name='Finance App')
    except Exception:
        pass

def reverse_default_site(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_category_is_limit_subscription_premium'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(create_default_site, reverse_default_site),
    ]
