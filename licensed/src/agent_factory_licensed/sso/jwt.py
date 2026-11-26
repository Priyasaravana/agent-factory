# SPDX-License-Identifier: LicenseRef-AgentFactory-Commercial
# Copyright 2026 The Agent Software Factory authors. See licensed/LICENSE.
"""ID token checks for OpenID Connect (ADR-0052): a JWS signed with a key from the provider's JWKS.

Only asymmetric algorithms are accepted (RS256, PS256, ES256); `none` and the HMAC family are refused, so a
token can't be "signed" with the client secret or not at all. The key is chosen by `kid` (or the only key of
the right type when the provider publishes one). Claims are then checked by the caller's rules: `verify`
returns them only after the signature, issuer, audience, authorised party, expiry, not-before and nonce hold.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ALGS = {"RS256", "PS256", "ES256"}
LEEWAY = 60  # seconds of clock difference allowed either way


class TokenInvalid(Exception):
    pass


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _int(text: str) -> int:
    return int.from_bytes(b64d(text), "big")


def _key(jwk: dict[str, Any]) -> rsa.RSAPublicKey | ec.EllipticCurvePublicKey:
    if jwk.get("kty") == "RSA":
        return rsa.RSAPublicNumbers(_int(jwk["e"]), _int(jwk["n"])).public_key()
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return ec.EllipticCurvePublicNumbers(_int(jwk["x"]), _int(jwk["y"]), ec.SECP256R1()).public_key()
    raise TokenInvalid(f"unsupported key type {jwk.get('kty')}/{jwk.get('crv')}")


def _pick(jwks: dict[str, Any], kid: str | None, alg: str) -> dict[str, Any]:
    kty = "EC" if alg.startswith("ES") else "RSA"
    keys = [k for k in jwks.get("keys", []) if k.get("kty") == kty and k.get("use", "sig") == "sig"]
    if kid is not None:
        keys = [k for k in keys if k.get("kid") == kid]
    if len(keys) != 1:
        raise TokenInvalid(f"no single {kty} signing key for kid {kid!r} in the provider's JWKS")
    return keys[0]


def check_signature(token: str, jwks: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(header, claims) once the signature holds."""
    try:
        h64, p64, s64 = token.split(".")
        header = json.loads(b64d(h64))
        claims = json.loads(b64d(p64))
        sig = b64d(s64)
    except ValueError as exc:
        raise TokenInvalid("not a signed JWT") from exc
    alg = header.get("alg")
    if alg not in ALGS:
        raise TokenInvalid(f"algorithm {alg!r} is not accepted")
    if not isinstance(claims, dict):
        raise TokenInvalid("claims are not an object")
    key = _key(_pick(jwks, header.get("kid"), alg))
    data = f"{h64}.{p64}".encode()
    try:
        if alg == "RS256" and isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "PS256" and isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, data, padding.PSS(padding.MGF1(hashes.SHA256()), 32), hashes.SHA256())
        elif alg == "ES256" and isinstance(key, ec.EllipticCurvePublicKey):
            if len(sig) != 64:
                raise TokenInvalid("ES256 signature has the wrong length")
            der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
            key.verify(der, data, ec.ECDSA(hashes.SHA256()))
        else:
            raise TokenInvalid(f"key does not fit algorithm {alg}")
    except InvalidSignature as exc:
        raise TokenInvalid("the signature does not match") from exc
    return header, claims


def verify(
    token: str,
    jwks: dict[str, Any],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
    now: float | None = None,
) -> dict[str, Any]:
    _, c = check_signature(token, jwks)
    now = time.time() if now is None else now
    if c.get("iss") != issuer:
        raise TokenInvalid(f"issuer {c.get('iss')!r} is not {issuer!r}")
    aud = c.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise TokenInvalid("the token is not for this client")
    if len(auds) > 1 and c.get("azp") != client_id:
        raise TokenInvalid("several audiences and this client is not the authorised party")
    if "azp" in c and c["azp"] != client_id:
        raise TokenInvalid("the authorised party is another client")
    try:
        exp = float(c["exp"])
        iat = float(c["iat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenInvalid("exp and iat are required") from exc
    if now > exp + LEEWAY:
        raise TokenInvalid("the token has expired")
    if iat > now + LEEWAY or ("nbf" in c and float(c["nbf"]) > now + LEEWAY):
        raise TokenInvalid("the token is not valid yet")
    if not hmac.compare_digest(str(c.get("nonce", "")).encode(), nonce.encode()):
        raise TokenInvalid("the nonce does not match this sign-in")
    if not c.get("sub"):
        raise TokenInvalid("no subject")
    return c
