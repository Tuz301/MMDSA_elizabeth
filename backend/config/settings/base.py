"""
Base settings for the Mentor Mother Digital Supervision Application.

Values that differ between environments live in dev.py and prod.py. In the
pilot environment every secret comes from AWS Secrets Manager. No secret is
ever written into this file or into the repository.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-development-key")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Third party.
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    # Local. Order matters: common defines the abstract base models.
    "apps.common",
    "apps.accounts",
    "apps.registry",
    "apps.visits",
    "apps.eid",
    "apps.alerts",
    "apps.messaging",
    "apps.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Captures the acting user for the audit trail. Must run after
    # authentication.
    "apps.audit.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
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

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "mmdsa"),
        "USER": os.environ.get("DB_USER", "mmdsa_app"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"sslmode": os.environ.get("DB_SSLMODE", "require")},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-ng"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.CognitoJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    # StandardPagination caps the page size. The bare LimitOffsetPagination
    # has no max_limit, which would let one request dump every patient row a
    # scope allows.
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
        # ScopedRateThrottle only limits a view that declares a scope, so
        # every other endpoint gets a per-user ceiling from this one.
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "sync": "120/hour",
        "auth": "20/hour",
        "inbound_sms": "600/hour",
        "user": "1000/hour",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DATETIME_FORMAT": "iso-8601",
}

# The API surface of a PMTCT registry is not public documentation: the schema
# and its viewer require an authenticated, active health worker.
SPECTACULAR_SETTINGS = {
    "TITLE": "MMDSA API",
    "DESCRIPTION": (
        "Mentor Mother Digital Supervision Application. Identifier fields "
        "are role-gated and absent from responses for roles that may not "
        "read them; programme codes are always present."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["apps.accounts.permissions.IsActiveHealthWorker"],
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# ---------------------------------------------------------------------------
# Identity (AWS Cognito)
# ---------------------------------------------------------------------------
COGNITO = {
    "REGION": os.environ.get("AWS_REGION", "af-south-1"),
    "USER_POOL_ID": os.environ.get("COGNITO_USER_POOL_ID", ""),
    "MOBILE_CLIENT_ID": os.environ.get("COGNITO_MOBILE_CLIENT_ID", ""),
    "WEB_CLIENT_ID": os.environ.get("COGNITO_WEB_CLIENT_ID", ""),
    "JWKS_CACHE_SECONDS": 3600,
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# ---------------------------------------------------------------------------
# SMS gateway (Termii)
# ---------------------------------------------------------------------------
TERMII = {
    "BASE_URL": os.environ.get("TERMII_BASE_URL", "https://api.ng.termii.com"),
    "API_KEY": os.environ.get("TERMII_API_KEY", ""),
    "SENDER_ID": os.environ.get("TERMII_SENDER_ID", "IHVN-PMTCT"),
    "CHANNEL": "dnd",  # Reaches numbers on the Do Not Disturb list.
    "TIMEOUT_SECONDS": 15,
    "MAX_RETRIES": 3,
    # Hard stop. The gateway refuses to send if the body fails the PHI check.
    "ENFORCE_PHI_GUARD": True,
    # Signs inbound callbacks. When it is unset the webhook refuses every
    # callback: a check that cannot run denies, exactly as the privacy guard
    # does outbound.
    "WEBHOOK_SECRET": os.environ.get("TERMII_WEBHOOK_SECRET", ""),
}

# ---------------------------------------------------------------------------
# Programme rules
# ---------------------------------------------------------------------------
PROGRAMME = {
    # A home visit counts as location verified when the reported accuracy is
    # within this radius of the registered household point.
    "LOCATION_MATCH_RADIUS_METRES": 250,
    # Readings less precise than this are recorded but not counted as verified.
    "LOCATION_MAX_ACCURACY_METRES": 100,
    # M and E target. See the indicator table in the main proposal.
    "LOCATION_VERIFIED_TARGET_PERCENT": 85,
    # Hours a supervisor has to acknowledge an alert before it escalates.
    "ALERT_SLA_HOURS": {
        "CRITICAL": 4,
        "HIGH": 24,
        "MEDIUM": 72,
        "LOW": 168,
    },
    # Age in weeks at which each EID sample is due, from the national
    # PMTCT guideline.
    "EID_SCHEDULE_WEEKS": {
        "BIRTH": 0,
        "WEEK_6": 6,
        "MONTH_9": 39,
        "MONTH_18": 78,
    },
    # Records unsynchronised for longer than this raise an operations alarm.
    "SYNC_BACKLOG_ALARM_HOURS": 72,
}

# ---------------------------------------------------------------------------
# Data protection (NDPR / NDPA 2023)
# ---------------------------------------------------------------------------
DATA_PROTECTION = {
    # Key used for column-level encryption of direct identifiers. Supplied by
    # Secrets Manager. The application refuses to start in the pilot
    # environment without it.
    "FIELD_ENCRYPTION_KEY": os.environ.get("FIELD_ENCRYPTION_KEY", ""),
    # Retention period for clinical records after programme exit.
    "CLINICAL_RETENTION_YEARS": 7,
    # Retention period for the audit log.
    "AUDIT_RETENTION_YEARS": 7,
    # Period after which an inactive account is disabled.
    "ACCOUNT_INACTIVITY_DAYS": 90,
}

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            )
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "apps.messaging": {"level": "INFO", "propagate": True},
        "apps.alerts": {"level": "INFO", "propagate": True},
        "apps.audit": {"level": "INFO", "propagate": True},
    },
}
