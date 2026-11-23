from functools import lru_cache

from loguru import logger

from pai.config.app import Config


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load and validate the PAI configuration."""
    logger.debug("Loading configuration from config/config.yaml and .env")
    config = Config()
    logger.debug("Configuration loaded: %s", config)
    logger.debug("Configuration loaded and validated successfully")
    return config