"""
Configuration management for Personal AI system
"""
import os
from typing import Optional
from pydantic import BaseSettings


class Settings(BaseSettings):
    """Core system settings"""
    
    # API Configuration
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_debug: bool = os.getenv("API_DEBUG", "false").lower() == "true"
    api_reload: bool = os.getenv("API_RELOAD", "false").lower() == "true"
    
    # Database Configuration
    database_url: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://pai_user:pai_password@localhost:5432/personal_ai"
    )
    database_echo: bool = os.getenv("DATABASE_ECHO", "false").lower() == "true"
    
    # Redis Configuration
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_ttl: int = int(os.getenv("REDIS_TTL", "3600"))
    
    # NATS Configuration
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    nats_timeout: int = int(os.getenv("NATS_TIMEOUT", "5"))
    
    # Voice Configuration
    voice_stt_provider: str = os.getenv("STT_PROVIDER", "whisper")  # whisper, google
    voice_tts_provider: str = os.getenv("TTS_PROVIDER", "piper")  # piper, google
    voice_language: str = os.getenv("VOICE_LANGUAGE", "en-US")
    voice_sample_rate: int = int(os.getenv("VOICE_SAMPLE_RATE", "16000"))
    
    # LLM Configuration
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")  # openai, local
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    llm_api_key: Optional[str] = os.getenv("LLM_API_KEY", None)
    
    # Plugin Configuration
    plugins_dir: str = os.getenv("PLUGINS_DIR", "./plugins")
    plugins_enabled: bool = os.getenv("PLUGINS_ENABLED", "true").lower() == "true"
    
    # Agent Configuration
    agents_enabled: bool = os.getenv("AGENTS_ENABLED", "true").lower() == "true"
    agent_timeout: int = int(os.getenv("AGENT_TIMEOUT", "300"))
    
    # Memory Configuration
    memory_type: str = os.getenv("MEMORY_TYPE", "hybrid")  # hybrid, vector, graph
    vector_db_url: str = os.getenv("VECTOR_DB_URL", "http://localhost:8000")
    vector_db_type: str = os.getenv("VECTOR_DB_TYPE", "chromadb")  # chromadb, weaviate
    
    # Security Configuration
    jwt_secret: str = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expiration: int = int(os.getenv("JWT_EXPIRATION", "86400"))
    
    # Logging Configuration
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: Optional[str] = os.getenv("LOG_FILE", None)
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
