"""
Django settings for the BOQ & Store MVP (config project).

Values that differ between developers or environments (secret key,
debug flag, database credentials) are read from a `.env` file via
python-decouple rather than hard-coded here, so this file is safe to
commit and each machine (or later, each server) supplies its own
`.env`. See `.env.example` for the variables it expects.
"""

from pathlib import Path

from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# PRODUCTION=True in a server's .env switches on everything Section 7.2's
# "Security" line needs once the app is live behind Nginx with a real
# HTTPS certificate (see "Production security" at the end of this file).
# It is a separate flag from DEBUG on purpose: Django's test runner
# always forces DEBUG off, so tying HTTPS redirects to DEBUG would make
# every test request bounce to https:// and fail.
PRODUCTION = config("PRODUCTION", default=False, cast=bool)

if PRODUCTION and DEBUG:
    raise ImproperlyConfigured(
        "PRODUCTION=True and DEBUG=True together: DEBUG shows full error "
        "pages (settings, SQL, code) to anyone. Set DEBUG=False on the server."
    )


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "accounts",
    "core",
    "boq",
    "store",
    "reports",
    "pwa",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-wide templates (base.html, registration/login.html) live
        # here; each app can still keep its own templates/<app_name>/ dir
        # too, which APP_DIRS below picks up automatically.
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# PostgreSQL per Section 7.3 of the requirements spec (money/transactions
# need a reliable relational database, not SQLite).

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}


# Custom user model
# Swapping this after tables exist requires a painful migration, so it's
# set from the very first commit even though the extra fields Section 2
# needs (roles, per-project access) are built in a later step.
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Section 7.2: "session timeout after 30 minutes idle". The session lasts
# 30 minutes, and SESSION_SAVE_EVERY_REQUEST pushes that deadline forward
# on every page a user opens, so an active user is never logged out
# mid-task -- only someone who has left the app untouched for 30 minutes.
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/
# Section 7.2 of the spec: English interface, dates as DD/MM/YYYY.

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "static/"

# `python manage.py collectstatic` copies every app's static files here,
# and Nginx serves them directly on the server (Django/Gunicorn don't
# serve static files when DEBUG is off). Already in .gitignore.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Section 3's "attachments ... stored off the database" — GRN
# attachments (store/models.py's GRNAttachment) land here.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Production security (Section 7.2: "HTTPS only")
# Only active when PRODUCTION=True (see the top of this file). Assumes the
# deployment in Section 7.3: Nginx terminates HTTPS and passes requests
# to Gunicorn over plain HTTP on the same machine, telling Django the
# original request was HTTPS through the X-Forwarded-Proto header.

if PRODUCTION:
    # Any http:// request is redirected to https://.
    SECURE_SSL_REDIRECT = True
    # Trust Nginx's word that the browser used HTTPS. Nginx MUST set this
    # header itself (proxy_set_header X-Forwarded-Proto $scheme;) so a
    # client can't fake it -- see the README's deployment notes.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Login and form-security cookies are only ever sent over HTTPS.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Browser remembers "HTTPS only" for this site. Starts at one hour so
    # a certificate mistake in the first days can't lock users out for a
    # year; raise HSTS_SECONDS to 31536000 (one year) once HTTPS has run
    # cleanly for a few weeks.
    SECURE_HSTS_SECONDS = config("HSTS_SECONDS", default=3600, cast=int)
    # Behind a proxy on a real domain, Django needs the https:// origin
    # listed to accept form posts, e.g. https://boq.example.com
    CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

    # Two HSTS options are deliberately left off, so their deploy-check
    # warnings are silenced here rather than "fixed":
    #   W005 (include subdomains): would force HTTPS on every subdomain of
    #     the company's domain, including ones this app doesn't control.
    #   W021 (preload): submitting to browsers' built-in HTTPS list is
    #     close to irreversible. Worth doing only once the domain is final.
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
