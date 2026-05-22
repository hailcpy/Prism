from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from prism_infra.crypto import CredentialsCrypto


def test_credentials_crypto_roundtrip() -> None:
    crypto = CredentialsCrypto(Fernet.generate_key().decode("utf-8"))
    plaintext = b'{"api_key":"secret"}'
    encrypted = crypto.encrypt(plaintext)
    assert encrypted != plaintext
    assert crypto.decrypt(encrypted) == plaintext


def test_credentials_crypto_rejects_missing_key() -> None:
    with pytest.raises(ValueError):
        CredentialsCrypto("")
