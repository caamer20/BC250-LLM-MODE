import re
from pathlib import Path

APP_NAME = "BC250 LLM MODE"
AMD_VENDOR_ID = "0x1002"
FAST_VRAM_GIB = 12.0
COMFORTABLE_VRAM_GIB = 10.5
GTT_SPILL_GIB = 2.5
OVERHEAD_GIB = 1.0
DEFAULT_CONTAINER = "llm"
DEFAULT_SERVICE = "bc250-llm.service"
# Known-good llama.cpp tag vetted for BC-250/GFX1013 Vulkan. Refreshed with
# each application release; updates are always explicit, never automatic.
KNOWN_GOOD_LLAMACPP = "b7598"
TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_PORT = 8080
DEFAULT_CTX = 8192
DEFAULT_APP_DIR = Path.home() / ".bc250-llm-mode"
DEFAULT_STATE_PATH = DEFAULT_APP_DIR / "state.json"
DEFAULT_MODELS_DIR = DEFAULT_APP_DIR / "models"
DEFAULT_LOGS_DIR = DEFAULT_APP_DIR / "logs"

