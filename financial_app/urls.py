from django.contrib import admin
from django.contrib.staticfiles import views as staticfiles_views
from django.urls import path, include
from django.urls import re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from accounts.views import (
    root_entry_view,
    admin_login_view,
    admin_dashboard_view,
    admin_tickets_view,
    admin_activity_view,
    admin_users_view,
    admin_logout_view,
    support_login_view,
    support_portal_view,
    support_logout_view,
)

import sys
import traceback
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok", "message": "Backend is online"})

def custom_500_handler(request, *args, **kwargs):
    exc_type, exc_val, exc_tb = sys.exc_info()
    tb_text = "".join(traceback.format_exception(exc_type, exc_val, exc_tb)) if exc_type else "No exception info"
    return JsonResponse({"error": str(exc_val), "traceback": tb_text}, status=500)

handler500 = 'financial_app.urls.custom_500_handler'

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    path('api/', include('accounts.urls')),
    path('', root_entry_view, name='root'),
    path('admin-login/', admin_login_view, name='admin_login'),
    path('dashboard/', admin_dashboard_view, name='admin_dashboard'),
    path('dashboard/overview/', admin_dashboard_view, name='admin_overview'),
    path('dashboard/tickets/', admin_tickets_view, name='admin_tickets'),
    path('dashboard/activity/', admin_activity_view, name='admin_activity'),
    path('dashboard/users/', admin_users_view, name='admin_users_page'),
    path('dashboard/logout/', admin_logout_view, name='admin_dashboard_logout'),
    path('support/login/', support_login_view, name='support_login'),
    path('support/', support_portal_view, name='support_portal'),
    path('support/logout/', support_logout_view, name='support_logout'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if not settings.DEBUG:
    # Mobile clients hit the Django app directly on the local network, so
    # expose media files even when DEBUG is disabled.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]

urlpatterns += [
    re_path(r'^static/(?P<path>.*)$', staticfiles_views.serve, {'insecure': True}),
]
