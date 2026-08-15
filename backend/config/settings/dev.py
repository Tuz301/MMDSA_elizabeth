"""Development settings. This environment must never hold real patient data."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

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
