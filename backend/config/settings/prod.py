"""
Pilot settings. This environment holds real patient data.

Every value below is read from the environment. The deployment pipeline
populates the environment from AWS Secrets Manager at instance start.
"""

import os

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Fail fast. A missing key here means patient identifiers would be written in
# plain text, so the application must refuse to start.
_REQUIRED = {
    "DJANGO_SECRET_KEY": SECRET_KEY,  # noqa: F405
    "FIELD_ENCRYPTION_KEY": DATA_PROTECTION["FIELD_ENCRYPTION_KEY"],  # noqa: F405
    "COGNITO_USER_POOL_ID": COGNITO["USER_POOL_ID"],  # noqa: F405
    "TERMII_API_KEY": TERMII["API_KEY"],  # noqa: F405
    "TERMII_WEBHOOK_SECRET": TERMII["WEBHOOK_SECRET"],  # noqa: F405
    "DB_PASSWORD": DATABASES["default"]["PASSWORD"],  # noqa: F405
}
_missing = [k for k, v in _REQUIRED.items() if not v or "insecure" in str(v)]
if _missing:
    raise RuntimeError(
        "Refusing to start. These settings are missing or still hold a "
        f"placeholder value: {', '.join(sorted(_missing))}"
    )
