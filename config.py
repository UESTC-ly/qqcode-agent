from pathlib import Path
import os
from typing import Dict

from dotenv import load_dotenv

load_dotenv()

class Config:
    PROVIDER = os.getenv("PROVIDER", os.getenv("LLM_PROVIDER", "anthropic")).lower()
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    ANTHROPIC_BASE_URL = os.getenv('ANTHROPIC_BASE_URL')
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    MODEL = os.getenv("MODEL", "deepseek-v4-flash")
    MAX_TOKENS = 80000
    MAX_CONVERSATION_TOKENS = 900000  # Maximum tokens per conversation

    # Paths
    BASE_DIR = Path(__file__).parent
    TOOLS_DIR = BASE_DIR / "tools"
    PROMPTS_DIR = BASE_DIR / "prompts"

    # Assistant Configuration
    ENABLE_THINKING = True
    SHOW_TOOL_USAGE = True
    DEFAULT_TEMPERATURE = 0.7


    @classmethod
    def using_openai_compat(cls) -> bool:
        """Return True when using an OpenAI-compatible Chat Completions API."""
        return cls.PROVIDER in {"openai", "openai_compat", "openai-compatible"}

    @classmethod
    def openai_chat_completions_url(cls) -> str:
        """Return the OpenAI-compatible chat completions endpoint URL."""
        return f"{cls.OPENAI_BASE_URL.rstrip('/')}/chat/completions"

    @classmethod
    def anthropic_client_kwargs(cls) -> Dict[str, str]:
        """Return keyword arguments for Anthropic client initialization."""
        kwargs: Dict[str, str] = {}
        if cls.ANTHROPIC_API_KEY:
            kwargs["api_key"] = cls.ANTHROPIC_API_KEY
        if cls.ANTHROPIC_BASE_URL:
            kwargs["base_url"] = cls.ANTHROPIC_BASE_URL
        return kwargs
