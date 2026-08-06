"""
WSGI config for financial_app project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'financial_app.settings')

application = get_wsgi_application()

try:
    from django.db import connection
    from django.core.management import call_command
    if 'accounts_user' not in connection.introspection.table_names():
        call_command('migrate', '--no-input')
except Exception as _e:
        print(f"WSGI auto-migrate info: {_e}")
