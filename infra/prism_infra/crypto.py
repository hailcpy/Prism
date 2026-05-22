from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class CredentialsCrypto:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("PRISM_CREDS_KEY is required")
        self._fernet = Fernet(key.encode("utf-8"))

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise ValueError("invalid credential ciphertext or key") from exc
