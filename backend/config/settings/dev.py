"""Development settings. This environment must never hold real patient data."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# The Vite dev server proxies /api here but the browser's Origin header
# stays on the Vite port, so session-mode writes need it trusted. Development
# only; the pilot serves the dashboard from the same origin as the API.
CSRF_TRUSTED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Session-mode development needs to echo the CSRF cookie in a header, which
# means reading it from JavaScript. The pilot authenticates with a bearer
# token, where Django's CSRF machinery is not involved at all, so the
# HttpOnly flag stays on everywhere but here.
CSRF_COOKIE_HTTPONLY = False

DATABASES["default"]["OPTIONS"] = {"sslmode": "prefer"}  # noqa: F405

# SQLite fallback so that a developer can run checks and tests without a local
# PostgreSQL server. PostgreSQL-only fields are not exercised on SQLite.
import os  # noqa: E402

if os.environ.get("USE_SQLITE") == "1":
    DATABASES["default"] = {  # noqa: F405
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "dev.sqlite3",  # noqa: F405
    }

CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER") == "1"
TERMII["ENFORCE_PHI_GUARD"] = True  # noqa: F405
