from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from prism_infra.crypto import CredentialsCrypto


@dataclass(frozen=True)
class ProviderCredential:
    id: str
    provider: str
    name: str
    metadata: dict[str, Any]
    is_default: bool
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProviderCredentialWithSecrets(ProviderCredential):
    secrets: dict[str, str]


class PostgresCredentialStore:
    def __init__(self, database_url: str, crypto: CredentialsCrypto) -> None:
        self.database_url = database_url
        self.crypto = crypto

    def list_credentials(self) -> list[ProviderCredential]:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, provider, name, metadata, is_default, last_tested_at,
                       last_test_ok, last_test_error, created_at, updated_at
                FROM provider_credentials
                ORDER BY provider ASC, created_at ASC
                """
            )
            return [self._row_to_credential(row) for row in cur.fetchall()]

    def get_credential_with_secrets(
        self, credential_id: str
    ) -> ProviderCredentialWithSecrets | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, provider, name, metadata, is_default, last_tested_at,
                       last_test_ok, last_test_error, created_at, updated_at, secrets_enc
                FROM provider_credentials
                WHERE id = %s
                """,
                (credential_id,),
            )
            row = cur.fetchone()
            return self._row_to_credential_with_secrets(row) if row else None

    def get_default_credential_for_provider(
        self, provider: str
    ) -> ProviderCredentialWithSecrets | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, provider, name, metadata, is_default, last_tested_at,
                       last_test_ok, last_test_error, created_at, updated_at, secrets_enc
                FROM provider_credentials
                WHERE provider = %s AND is_default = TRUE
                LIMIT 1
                """,
                (provider,),
            )
            row = cur.fetchone()
            return self._row_to_credential_with_secrets(row) if row else None

    def create_credential(
        self,
        *,
        provider: str,
        name: str,
        secrets: dict[str, str],
        metadata: dict[str, Any],
        is_default: bool,
    ) -> ProviderCredential:
        secrets_enc = self.crypto.encrypt(json.dumps(secrets).encode("utf-8"))
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            if is_default:
                cur.execute(
                    "UPDATE provider_credentials SET is_default = FALSE WHERE provider = %s",
                    (provider,),
                )
            cur.execute(
                """
                INSERT INTO provider_credentials (provider, name, secrets_enc, metadata, is_default)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text, provider, name, metadata, is_default, last_tested_at,
                          last_test_ok, last_test_error, created_at, updated_at
                """,
                (provider, name, secrets_enc, Jsonb(metadata), is_default),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("credential insert returned no row")
            return self._row_to_credential(row)

    def update_credential(
        self,
        credential_id: str,
        *,
        name: str | None = None,
        secrets: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        is_default: bool | None = None,
    ) -> ProviderCredential | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT provider FROM provider_credentials WHERE id = %s",
                (credential_id,),
            )
            base = cur.fetchone()
            if not base:
                return None
            provider = str(base["provider"])
            if is_default is True:
                cur.execute(
                    "UPDATE provider_credentials SET is_default = FALSE WHERE provider = %s",
                    (provider,),
                )
            updates: list[str] = ["updated_at = now()"]
            params: list[Any] = []
            if name is not None:
                updates.append("name = %s")
                params.append(name)
            if secrets is not None:
                updates.append("secrets_enc = %s")
                params.append(self.crypto.encrypt(json.dumps(secrets).encode("utf-8")))
            if metadata is not None:
                updates.append("metadata = %s")
                params.append(Jsonb(metadata))
            if is_default is not None:
                updates.append("is_default = %s")
                params.append(is_default)
            params.append(credential_id)
            query = sql.SQL(
                """
                UPDATE provider_credentials
                SET {updates}
                WHERE id = %s
                RETURNING id::text, provider, name, metadata, is_default, last_tested_at,
                          last_test_ok, last_test_error, created_at, updated_at
                """
            ).format(
                updates=sql.SQL(", ").join(sql.SQL(cast(LiteralString, item)) for item in updates)
            )
            cur.execute(
                query,
                tuple(params),
            )
            row = cur.fetchone()
            return self._row_to_credential(row) if row else None

    def set_test_result(self, credential_id: str, *, ok: bool, error: str | None) -> None:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE provider_credentials
                SET last_tested_at = now(),
                    last_test_ok = %s,
                    last_test_error = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (ok, error, credential_id),
            )

    def delete_credential(self, credential_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM provider_credentials WHERE id = %s", (credential_id,))
            return cur.rowcount > 0

    def _row_to_credential(self, row: dict[str, Any]) -> ProviderCredential:
        return ProviderCredential(
            id=str(row["id"]),
            provider=str(row["provider"]),
            name=str(row["name"]),
            metadata=dict(row.get("metadata") or {}),
            is_default=bool(row["is_default"]),
            last_tested_at=row.get("last_tested_at"),
            last_test_ok=row.get("last_test_ok"),
            last_test_error=row.get("last_test_error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_credential_with_secrets(self, row: dict[str, Any]) -> ProviderCredentialWithSecrets:
        payload = self.crypto.decrypt(bytes(row["secrets_enc"]))
        secrets = json.loads(payload.decode("utf-8"))
        return ProviderCredentialWithSecrets(
            **self._row_to_credential(row).__dict__,
            secrets={str(key): str(value) for key, value in dict(secrets).items()},
        )
