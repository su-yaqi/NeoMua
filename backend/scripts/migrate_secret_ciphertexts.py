"""Rewrite legacy v1 provider/runtime ciphertexts as authenticated v2 AES-GCM."""

import logging

from sqlmodel import Session, select

from app.core.db import engine
from app.llm_provider_service import open_secret_payload, seal_secret_payload
from app.models import LlmProviderConfig
from app.runtime.models import RuntimeSecret


def migrate() -> tuple[int, int]:
    provider_count = 0
    runtime_count = 0
    with Session(engine) as session:
        providers = session.exec(select(LlmProviderConfig).with_for_update()).all()
        for provider in providers:
            if provider.secret_ciphertext and provider.secret_ciphertext.startswith(
                "v1."
            ):
                payload = open_secret_payload(provider.secret_ciphertext)
                replacement = seal_secret_payload(payload)
                assert replacement and open_secret_payload(replacement) == payload
                provider.secret_ciphertext = replacement
                session.add(provider)
                provider_count += 1
        runtime_secrets = session.exec(select(RuntimeSecret).with_for_update()).all()
        for secret in runtime_secrets:
            if secret.secret_ciphertext.startswith("v1."):
                payload = open_secret_payload(secret.secret_ciphertext)
                replacement = seal_secret_payload(payload)
                assert replacement and open_secret_payload(replacement) == payload
                secret.secret_ciphertext = replacement
                session.add(secret)
                runtime_count += 1
        session.commit()
    return provider_count, runtime_count


if __name__ == "__main__":
    migrated = migrate()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info(
        "secret migration complete: provider=%s runtime=%s", *migrated
    )
