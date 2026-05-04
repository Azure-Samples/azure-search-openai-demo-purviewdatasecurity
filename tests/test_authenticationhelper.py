import base64
import json
import re
from datetime import datetime, timedelta, timezone

import aiohttp
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from core.authentication import AuthenticationHelper, AuthError

from .mocks import MockResponse


def create_authentication_helper(
    require_access_control: bool = False,
    enable_global_documents: bool = False,
    enable_unauthenticated_access: bool = False,
):
    return AuthenticationHelper(
        search_index=None,
        use_authentication=True,
        server_app_id="SERVER_APP",
        server_app_secret="SERVER_SECRET",
        client_app_id="CLIENT_APP",
        tenant_id="TENANT_ID",
        require_access_control=require_access_control,
        enable_global_documents=enable_global_documents,
        enable_unauthenticated_access=enable_unauthenticated_access,
    )


def create_mock_jwt(kid="mock_kid", oid="OID_X"):
    payload = {
        "iss": "https://login.microsoftonline.com/TENANT_ID/v2.0",
        "sub": "AaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA",
        "aud": "SERVER_APP",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "nbf": int(datetime.now(timezone.utc).timestamp()),
        "name": "John Doe",
        "oid": oid,
        "preferred_username": "john.doe@example.com",
        "rh": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.",
        "tid": "22222222-2222-2222-2222-222222222222",
        "uti": "AbCdEfGhIjKlMnOp-ABCDEFG",
        "ver": "2.0",
    }
    header = {"kid": kid, "alg": "RS256", "typ": "JWT"}
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(payload, private_key, algorithm="RS256", headers=header)
    return token, private_key.public_key(), payload


@pytest.mark.asyncio
async def test_get_auth_claims_success(mock_confidential_client_success, mock_validate_token_success):
    helper = create_authentication_helper()
    auth_claims = await helper.get_auth_claims_if_enabled(headers={"Authorization": "Bearer Token"})

    assert auth_claims == {"access_token": "MockSearchToken", "graph_access_token": "MockGraphToken"}


@pytest.mark.asyncio
async def test_get_auth_claims_unauthorized(mock_confidential_client_unauthorized, mock_validate_token_success):
    helper = create_authentication_helper()
    auth_claims = await helper.get_auth_claims_if_enabled(headers={"Authorization": "Bearer Token"})
    assert len(auth_claims.keys()) == 0


def test_auth_setup(mock_confidential_client_success, mock_validate_token_success, snapshot):
    helper = create_authentication_helper()
    result = helper.get_auth_setup_for_client()
    snapshot.assert_match(json.dumps(result, indent=4), "result.json")


def test_auth_setup_required_access_control(mock_confidential_client_success, mock_validate_token_success, snapshot):
    helper = create_authentication_helper(require_access_control=True)
    result = helper.get_auth_setup_for_client()
    snapshot.assert_match(json.dumps(result, indent=4), "result.json")


def test_auth_setup_required_access_control_and_unauthenticated_access(
    mock_confidential_client_success, mock_validate_token_success, snapshot
):
    helper = create_authentication_helper(require_access_control=True, enable_unauthenticated_access=True)
    result = helper.get_auth_setup_for_client()
    snapshot.assert_match(json.dumps(result, indent=4), "result.json")


def test_get_auth_token(mock_confidential_client_success, mock_validate_token_success):
    with pytest.raises(AuthError) as exc_info:
        AuthenticationHelper.get_token_auth_header({})
    assert exc_info.value.status_code == 401
    with pytest.raises(AuthError) as exc_info:
        AuthenticationHelper.get_token_auth_header({"Authorization": ". ."})
    assert exc_info.value.status_code == 401
    with pytest.raises(AuthError) as exc_info:
        AuthenticationHelper.get_token_auth_header({"Authorization": "invalid"})
    assert exc_info.value.status_code == 401
    with pytest.raises(AuthError) as exc_info:
        AuthenticationHelper.get_token_auth_header({"Authorization": "invalid MockToken"})
    assert exc_info.value.status_code == 401
    assert AuthenticationHelper.get_token_auth_header({"Authorization": "Bearer MockToken"}) == "MockToken"
    assert AuthenticationHelper.get_token_auth_header({"x-ms-token-aad-access-token": "MockToken"}) == "MockToken"


@pytest.mark.asyncio
async def test_create_pem_format(mock_confidential_client_success, mock_validate_token_success):
    helper = create_authentication_helper()
    mock_token, public_key, payload = create_mock_jwt(oid="OID_X")
    _, other_public_key, _ = create_mock_jwt(oid="OID_Y")
    mock_jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "other_mock_kid",
                "use": "sig",
                "n": base64.urlsafe_b64encode(
                    other_public_key.public_numbers().n.to_bytes(
                        (other_public_key.public_numbers().n.bit_length() + 7) // 8, byteorder="big"
                    )
                )
                .decode("utf-8")
                .rstrip("="),
                "e": base64.urlsafe_b64encode(
                    other_public_key.public_numbers().e.to_bytes(
                        (other_public_key.public_numbers().e.bit_length() + 7) // 8, byteorder="big"
                    )
                )
                .decode("utf-8")
                .rstrip("="),
            },
            {
                "kty": "RSA",
                "kid": "mock_kid",
                "use": "sig",
                "n": base64.urlsafe_b64encode(
                    public_key.public_numbers().n.to_bytes(
                        (public_key.public_numbers().n.bit_length() + 7) // 8, byteorder="big"
                    )
                )
                .decode("utf-8")
                .rstrip("="),
                "e": base64.urlsafe_b64encode(
                    public_key.public_numbers().e.to_bytes(
                        (public_key.public_numbers().e.bit_length() + 7) // 8, byteorder="big"
                    )
                )
                .decode("utf-8")
                .rstrip("="),
            },
        ]
    }

    pem_key = await helper.create_pem_format(mock_jwks, mock_token)

    assert isinstance(pem_key, bytes), "create_pem_format should return bytes"
    pem_str = pem_key.decode("utf-8")
    assert pem_str.startswith("-----BEGIN PUBLIC KEY-----"), "PEM key should start with the correct marker"
    assert pem_str.endswith("-----END PUBLIC KEY-----\n"), "PEM key should end with the correct marker"
    pem_regex = r"^-----BEGIN PUBLIC KEY-----\n([A-Za-z0-9+/\n]+={0,2})\n-----END PUBLIC KEY-----\n$"
    assert re.match(pem_regex, pem_str), "PEM key format is incorrect"

    decoded = jwt.decode(mock_token, key=pem_key, algorithms=["RS256"], audience=payload["aud"], issuer=payload["iss"])
    assert decoded["oid"] == payload["oid"], "Decoded token should contain correct OID"

    loaded_public_key = serialization.load_pem_public_key(pem_key)
    assert isinstance(loaded_public_key, rsa.RSAPublicKey), "Loaded key should be an RSA public key"


@pytest.mark.asyncio
async def test_validate_access_token(monkeypatch, mock_confidential_client_success):
    mock_token, public_key, payload = create_mock_jwt(oid="OID_X")

    def mock_get(*args, **kwargs):
        return MockResponse(
            status=200,
            text=json.dumps(
                {
                    "keys": [
                        {
                            "kty": "RSA",
                            "use": "sig",
                            "kid": "23nt",
                            "x5t": "23nt",
                            "n": "hu2SJ",
                            "e": "AQAB",
                            "x5c": ["MIIC/jCC"],
                            "issuer": "https://login.microsoftonline.com/TENANT_ID/v2.0",
                        },
                        {
                            "kty": "RSA",
                            "use": "sig",
                            "kid": "MGLq",
                            "x5t": "MGLq",
                            "n": "yfNcG8",
                            "e": "AQAB",
                            "x5c": ["MIIC/jCC"],
                            "issuer": "https://login.microsoftonline.com/TENANT_ID/v2.0",
                        },
                    ]
                }
            ),
        )

    monkeypatch.setattr(aiohttp.ClientSession, "get", mock_get)

    def mock_decode(*args, **kwargs):
        return payload

    monkeypatch.setattr(jwt, "decode", mock_decode)

    async def mock_create_pem_format(*args, **kwargs):
        return public_key

    monkeypatch.setattr(AuthenticationHelper, "create_pem_format", mock_create_pem_format)

    helper = create_authentication_helper()
    await helper.validate_access_token(mock_token)
