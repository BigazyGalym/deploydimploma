# financial_app/settings.py
"""
Django settings for financial_app project.
"""
import os
from pathlib import Path
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_env_list(name: str, default=None):
    value = os.getenv(name)
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


# Load financial_app/.env into process env
load_env_file(BASE_DIR / '.env')

# SECURITY
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-local-dev-key')
DEBUG = get_env_bool('DEBUG', False)
ALLOWED_HOSTS = get_env_list(
    'ALLOWED_HOSTS',
    ['*'],
)

# ----------------------
# Applications
# ----------------------
INSTALLED_APPS = [
    # Django built-in
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    # Third-party
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'dj_rest_auth',
    'dj_rest_auth.registration',
    # Local apps
    'accounts',
]

SITE_ID = 1

# ----------------------
# Middleware
# ----------------------
MIDDLEWARE = [
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.UserActivityMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# settings.py
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'APP': {
            'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
            'secret': os.getenv('GOOGLE_CLIENT_SECRET', ''),
            'key': ''
        }
    }
}

# Google OAuth (немесе басқа әлеуметтік логиндер) үшін
GOOGLE_OAUTH_CALLBACK_URL = os.getenv(
    'GOOGLE_OAUTH_CALLBACK_URL',
    "http://localhost:8000/accounts/google/login/callback/",
)

ROOT_URLCONF = 'financial_app.urls'

# ----------------------
# Templates
# ----------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'financial_app.wsgi.application'

# ----------------------
# Database
# ----------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'onlineagrotechnics'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# ----------------------
# Email
# ----------------------
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = get_env_bool('EMAIL_USE_TLS', True)
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)

# ----------------------
# Gemini AI
# ----------------------
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip()
GEMINI_API_TIMEOUT = int(os.getenv('GEMINI_API_TIMEOUT', '20'))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv('GEMINI_MAX_OUTPUT_TOKENS', '600'))

# ----------------------
# REST Framework
# ----------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}

REST_USE_JWT = True
REST_AUTH_REGISTER_SERIALIZER = 'accounts.serializers.CustomRegisterSerializer'
CORS_ALLOW_ALL_ORIGINS = True

# ----------------------
# Password validation
# ----------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ----------------------
# Internationalization
# ----------------------
LANGUAGE_CODE = 'en-us'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Алматы уақыты
TIME_ZONE = 'Asia/Almaty'

LANGUAGES = [
    ('en', 'English'),
    ('ru', 'Russian'),
    ('kz', 'Kazakh'),
]

LOCALE_PATHS = [os.path.join(BASE_DIR, 'locale')]


# ----------------------
# Static files
# ----------------------
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

# ----------------------
# Custom User
# ----------------------
AUTH_USER_MODEL = 'accounts.User'
# Allauth email-based auth (new allauth settings)
ACCOUNT_USER_MODEL_USERNAME_FIELD = None  # username жоқ
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
)
SOCIALACCOUNT_ADAPTER = 'accounts.adapters.CustomSocialAccountAdapter'

# ----------------------
# Default primary key field type
# ----------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

STATIC_ROOT = BASE_DIR / 'staticfiles'
