import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter
API_KEY = os.getenv("OPENROUTER_API_KEY", "")
API_BASE_URL = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o")

# Browser
BROWSER_HEADLESS = False
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "")
USER_DATA_DIR = os.path.expanduser("~/.agent_user_data")
BROWSER_VIEWPORT_WIDTH = 1280
BROWSER_VIEWPORT_HEIGHT = 900

# Agent
MAX_STEPS = 50
MAX_PAGE_TEXT_CHARS = 5000

# Timeouts
ACTION_TIMEOUT_MS = 10_000
NAV_TIMEOUT_MS = 30_000
LLM_TIMEOUT_S = 60