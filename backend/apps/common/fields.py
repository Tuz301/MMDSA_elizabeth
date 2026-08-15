"""
Column-level encryption for direct identifiers.

The data classification matrix in the compliance annex puts names, telephone
numbers and household addresses in the highest tier. Disk encryption alone does
not protect these values, because anyone who can read the database can read
them. These fields encrypt the value before it reaches the database, so a
database dump or a mistaken query result shows ciphertext.

The key comes from AWS Secrets Manager. It never appears in the repository.

Fernet is used because it authenticates the ciphertext. A tampered value fails
to decrypt instead of returning a wrong plaintext.

A search on an encrypted column is not possible. Where a lookup is needed, store
a separate keyed hash column and search on that. SearchableEncryptedField does
this. The hash is keyed, so an attacker who copies the column cannot test
candidate names against it without the key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    from cryptography.fernet import Fernet

    key = settings.DATA_PROTECTION.get("FIELD_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not set. Direct identifiers cannot be "
            "written without it."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def blind_index(value: str) -> str:
    """
    Return a keyed hash of a value, for equality lookups on an encrypted column.

    The output is deterministic, so two identical names produce the same index.
    That allows a lookup. It also means the index leaks which rows share a
    value, so never place a blind index on a low-cardinality field such as a
    surname on its own.
    """
    key = settings.DATA_PROTECTION.get("FIELD_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not set.")
    normalised = value.strip().casefold().encode()
    digest = hmac.new(
        key.encode() if isinstance(key, str) else key, normalised, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode()[:44]


class EncryptedTextField(models.TextField):
    """A text column whose value is encrypted at rest."""

    description = "Text encrypted with Fernet before it is stored."

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # A value that will not decrypt is a real incident: either the key
            # has been rotated without re-encrypting, or the row was altered.
            # Returning a marker keeps the request alive so that the audit
            # record is written, rather than raising inside a serialiser.
            return "[UNREADABLE: key mismatch or altered ciphertext]"


class EncryptedCharField(EncryptedTextField):
    """
    Encrypted equivalent of CharField.

    The column stays a TEXT column, because ciphertext is longer than the
    plaintext and the length varies. max_length is kept for form validation
    only.
    """

    def __init__(self, *args, max_length: int | None = None, **kwargs):
        self.plain_max_length = max_length
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.plain_max_length is not None:
            kwargs["max_length"] = self.plain_max_length
        return name, path, args, kwargs
