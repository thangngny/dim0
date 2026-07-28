"""Utilities for loading secrets from Doppler."""

import logging
import os
import secrets

try:
    from dopplersdk import DopplerSDK
except ImportError:  # dopplersdk is optional in local/dev (use .env)
    DopplerSDK = None

from topix.datatypes.stage import StageEnum

logger = logging.getLogger(__name__)


class DopplerUnavailableError(RuntimeError):
    """Raised when the Doppler SDK is not installed and no token is set."""


def generate_jwt_secret() -> str:
    """Generate a secure random JWT secret key."""
    logger.info("Generating a new JWT secret.")
    return secrets.token_urlsafe(64)


def load_secrets(
    stage: StageEnum = StageEnum.LOCAL
):
    """Load secrets from Doppler based on the provided stage."""
    if DopplerSDK is None:
        raise DopplerUnavailableError(
            "dopplersdk not installed — falling back to .env defaults."
        )
    if stage in [StageEnum.LOCAL, StageEnum.DEV, StageEnum.TEST]:
        secret_name = f"dev_{stage}"
    else:
        secret_name = stage
    doppler = DopplerSDK()
    doppler.set_access_token(os.getenv("DOPPLER_TOKEN"))
    secret = doppler.secrets.get("CONFIG", secret_name, "topix")
    logger.info("Loaded secrets for stage `%s`.", stage)
    return secret.value['raw']
