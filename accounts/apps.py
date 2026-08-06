from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        try:
            from django.db import connection
            from django.core.management import call_command
            tables = connection.introspection.table_names()
            if 'accounts_user' not in tables:
                call_command('migrate', interactive=False)
        except Exception:
            pass
