"""
The Cognito token path, tested against realistically shaped tokens.

Every other test in the suite uses force_authenticate, which bypasses this
class entirely; these tests exist so the one door real traffic walks through
is not the one door nothing exercises. Tokens are signed with a local RSA key
and the JWKS lookup is stubbed, so the shapes are Cognito's but no network is
involved.

The load-bearing case: a Cognito ACCESS token carries client_id and no aud.
The original implementation passed audience= to jwt.decode, which rejects
exactly that token — the one the dashboard sends.
"""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIRequestFactory

from apps.accounts import authentication as auth_module
from apps.accounts.authentication import CognitoJWTAuthentication
from apps.accounts.models import Role, User

POOL_ID = "af-south-1_TESTPOOL"
WEB_CLIENT = "web-client-id"
MOBILE_CLIENT = "mobile-client-id"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubSigningKey:
    def __init__(self, key):
        self.key = key


class _StubJwks:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _StubSigningKey(self._public_key)


@pytest.fixture
def cognito(settings, rsa_key, monkeypatch):
    settings.COGNITO = {
        **settings.COGNITO,
        "REGION": "af-south-1",
        "USER_POOL_ID": POOL_ID,
        "MOBILE_CLIENT_ID": MOBILE_CLIENT,
        "WEB_CLIENT_ID": WEB_CLIENT,
    }
    monkeypatch.setattr(
        auth_module, "_jwks", lambda: _StubJwks(rsa_key.public_key())
    )
    return settings.COGNITO


def _issuer():
    return f"https://cognito-idp.af-south-1.amazonaws.com/{POOL_ID}"


def _token(rsa_key, *, sub, token_use="access", client=WEB_CLIENT,
           issuer=None, expires_in=300, **extra):
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": issuer or _issuer(),
        "token_use": token_use,
        "iat": now,
        "exp": now + expires_in,
        **extra,
    }
    # The claim that names the client differs by token kind. This asymmetry
    # is the whole point of the test file.
    if token_use == "access":
        claims["client_id"] = client
    elif token_use == "id":
        claims["aud"] = client
    return jwt.encode(claims, rsa_key, algorithm="RS256")


def _authenticate(token):
    request = APIRequestFactory().get(
        "/api/v1/accounts/me/", HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    return CognitoJWTAuthentication().authenticate(request)


@pytest.fixture
def cognito_user(facility):
    return User.objects.create(
        username="cognito-supervisor",
        role=Role.FACILITY_SUPERVISOR,
        facility=facility,
        cognito_sub=str(uuid.uuid4()),
    )


@pytest.mark.django_db
class TestTokenKinds:
    def test_an_access_token_authenticates(self, cognito, rsa_key, cognito_user):
        user, _ = _authenticate(
            _token(rsa_key, sub=cognito_user.cognito_sub, token_use="access")
        )
        assert user == cognito_user

    def test_an_id_token_authenticates(self, cognito, rsa_key, cognito_user):
        user, _ = _authenticate(
            _token(rsa_key, sub=cognito_user.cognito_sub, token_use="id")
        )
        assert user == cognito_user

    def test_a_mobile_client_token_authenticates(
        self, cognito, rsa_key, cognito_user
    ):
        user, _ = _authenticate(
            _token(rsa_key, sub=cognito_user.cognito_sub, client=MOBILE_CLIENT)
        )
        assert user == cognito_user


@pytest.mark.django_db
class TestRejections:
    def _refused(self, token):
        from rest_framework.exceptions import AuthenticationFailed

        with pytest.raises(AuthenticationFailed):
            _authenticate(token)

    def test_a_token_from_an_unknown_client_is_refused(
        self, cognito, rsa_key, cognito_user
    ):
        self._refused(
            _token(rsa_key, sub=cognito_user.cognito_sub, client="attacker-client")
        )

    def test_an_unrecognised_token_kind_is_refused(
        self, cognito, rsa_key, cognito_user
    ):
        self._refused(
            _token(rsa_key, sub=cognito_user.cognito_sub, token_use="refresh")
        )

    def test_a_wrong_issuer_is_refused(self, cognito, rsa_key, cognito_user):
        self._refused(
            _token(
                rsa_key,
                sub=cognito_user.cognito_sub,
                issuer="https://cognito-idp.af-south-1.amazonaws.com/OTHER_POOL",
            )
        )

    def test_an_expired_token_is_refused(self, cognito, rsa_key, cognito_user):
        self._refused(
            _token(rsa_key, sub=cognito_user.cognito_sub, expires_in=-60)
        )

    def test_an_unknown_subject_is_refused(self, cognito, rsa_key):
        self._refused(_token(rsa_key, sub=str(uuid.uuid4())))

    def test_a_disabled_account_is_refused(self, cognito, rsa_key, cognito_user):
        cognito_user.disable("test")
        self._refused(_token(rsa_key, sub=cognito_user.cognito_sub))

    def test_with_no_clients_configured_every_token_is_refused(
        self, cognito, rsa_key, cognito_user, settings
    ):
        # Fail closed. The previous behaviour skipped the client check when
        # the list was empty, which accepted any token from the pool.
        settings.COGNITO = {
            **settings.COGNITO, "MOBILE_CLIENT_ID": "", "WEB_CLIENT_ID": "",
        }
        self._refused(_token(rsa_key, sub=cognito_user.cognito_sub))
