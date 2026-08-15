"""
Cognito JWT authentication for the REST API.

The token is verified against the Cognito JSON Web Key Set. Three checks are
made in order: the signature, the audience, and the issuer. A token that fails
any of them is rejected without a database query, so an attacker cannot use the
authentication path to probe for valid usernames.

The Django user row is matched on the Cognito subject identifier, never on the
username. A username can be changed in the identity pool. The subject cannot.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

from .models import User

_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def _issuer() -> str:
    cfg = settings.COGNITO
    return f"https://cognito-idp.{cfg['REGION']}.amazonaws.com/{cfg['USER_POOL_ID']}"


def _jwks() -> Any:
    """Fetch and cache the signing keys."""
    ttl = settings.COGNITO["JWKS_CACHE_SECONDS"]
    if _jwks_cache["keys"] and time.time() - _jwks_cache["fetched_at"] < ttl:
        return _jwks_cache["keys"]

    client = jwt.PyJWKClient(f"{_issuer()}/.well-known/jwks.json")
    _jwks_cache["keys"] = client
    _jwks_cache["fetched_at"] = time.time()
    return client


class CognitoJWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Malformed authorisation header.")

        token = header[1].decode()
        claims = self._decode(token)

        subject = claims.get("sub")
        try:
            user = User.objects.select_related("facility", "lga", "state").get(
                cognito_sub=subject
            )
        except User.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed(
                "This identity has no account in the programme register. "
                "An administrator must create it."
            ) from exc

        if not user.is_active_account:
            raise exceptions.AuthenticationFailed("This account is disabled.")

        return (user, token)

    def _decode(self, token: str) -> dict[str, Any]:
        cfg = settings.COGNITO
        audiences = [
            a for a in (cfg["MOBILE_CLIENT_ID"], cfg["WEB_CLIENT_ID"]) if a
        ]
        try:
            signing_key = _jwks().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audiences or None,
                issuer=_issuer(),
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise exceptions.AuthenticationFailed("The token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed("The token is not valid.") from exc

    def authenticate_header(self, request):
        return self.keyword
