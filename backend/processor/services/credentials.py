"""Short-lived, encrypted storage for S3 connection details.

Credentials are never placed in the database, in a Job record, in a task argument,
or in application logs. The browser only receives an opaque random connection id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache


class ConnectionExpired(Exception):
    """The transient S3 connection can no longer be used."""


class CredentialVault:
    key_prefix = "s3-connection:"

    @classmethod
    def _fernet(cls) -> Fernet:
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    @classmethod
    def put(cls, credentials: dict[str, str]) -> str:
        connection_id = secrets.token_urlsafe(32)
        encrypted = cls._fernet().encrypt(json.dumps(credentials).encode("utf-8")).decode("ascii")
        cache.set(cls.key_prefix + connection_id, encrypted, timeout=settings.S3_CONNECTION_TTL_SECONDS)
        return connection_id

    @classmethod
    def get(cls, connection_id: str) -> dict[str, str]:
        encrypted = cache.get(cls.key_prefix + connection_id)
        if not encrypted:
            raise ConnectionExpired("Your S3 connection has expired. Connect again and resubmit the job.")
        try:
            return json.loads(cls._fernet().decrypt(encrypted.encode("ascii")).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionExpired("The saved S3 connection is no longer valid. Connect again.") from exc

    @classmethod
    def delete(cls, connection_id: str) -> None:
        cache.delete(cls.key_prefix + connection_id)
