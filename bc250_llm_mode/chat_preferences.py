"""Private, bounded chat preferences; never part of operational diagnostics."""
from __future__ import annotations

import json
import math
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from .fsops import atomic_write_text, ensure_private_dir
from .profile_access import profile_access, require_writable_profile

MAX_INSTRUCTION_BYTES = 16 * 1024
MAX_TEMPLATES = 20
MAX_TEMPLATE_FILE_BYTES = 384 * 1024
RESPONSE_LENGTHS = {"Brief (512 tokens)": 512, "Standard (2048 tokens)": 2048,
                    "Long (4096 tokens)": 4096, "Extended (8192 tokens)": 8192}
BUILTIN_TEMPLATES = {
    "Writing": "Help me write clear, concise prose. Preserve my meaning and voice. Ask when an important detail is missing.",
    "Coding": "Help me understand and improve code. State assumptions, explain relevant tradeoffs, and suggest how to verify changes. Never claim to have run code you have not run.",
    "Summarizing": "Summarize the supplied material faithfully. Separate facts, decisions, open questions, and next actions. Do not invent missing information.",
}


@dataclass(frozen=True)
class ConversationOptions:
    instructions: str = ""
    temperature: float | None = None
    response_tokens: int = 2048

    def __post_init__(self):
        if not isinstance(self.instructions, str) or len(self.instructions.encode("utf-8")) > MAX_INSTRUCTION_BYTES:
            raise ValueError("Instructions must fit within 16 KiB of text.")
        if self.temperature is not None and (
            isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("Temperature must be between 0 and 2, or use the model default.")
        if type(self.response_tokens) is not int or self.response_tokens not in RESPONSE_LENGTHS.values():
            raise ValueError("Choose a supported response length (512–8192 tokens).")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {"instructions", "temperature", "response_tokens"}:
            raise ValueError("Saved conversation settings are malformed.")
        return cls(**value)


class PromptTemplateService:
    """A small explicit library, with content confined to the private profile."""

    def __init__(self, profile: Path):
        self.profile = profile
        self.path = profile / "chat-preferences" / "templates.json"

    def list(self) -> dict[str, str]:
        with profile_access(self.profile):
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return dict(BUILTIN_TEMPLATES)
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("Template library must be a regular file.")
                raw = source.read(MAX_TEMPLATE_FILE_BYTES + 1)
            if len(raw) > MAX_TEMPLATE_FILE_BYTES:
                raise ValueError("Template library exceeds its size limit.")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Template library needs repair; its file has been preserved.") from exc
            return self.validate(data)

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or len(data) > MAX_TEMPLATES:
            raise ValueError("The library supports at most 20 templates.")
        for name, instructions in data.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(ord(c) < 32 for c in name):
                raise ValueError("Template names must contain 1–60 readable characters.")
            ConversationOptions(instructions=instructions)
        return dict(data)

    def save_template(self, name: str, instructions: str):
        with profile_access(self.profile):
            require_writable_profile(self.profile)
            with profile_access(self.path.parent, exclusive=True):
                data = self.list()
                data[name.strip()] = instructions
                data = self.validate(data)
                ensure_private_dir(self.path.parent)
                atomic_write_text(self.path, json.dumps(data, ensure_ascii=False), mode=0o600)
                return data

    def merge_import(self, values, digest):
        """Import without replacing a user's template; stable names allow retry."""
        values = self.validate(values)
        with profile_access(self.profile):
            require_writable_profile(self.profile)
            with profile_access(self.path.parent, exclusive=True):
                current = self.list()
                added = 0
                for name, content in values.items():
                    target = name
                    if name in current and current[name] != content:
                        target = name[:40] + " (import " + digest[:8] + ")"
                    if target in current:
                        if current[target] != content:
                            raise ValueError("An imported template was edited; existing text is preserved.")
                        continue
                    current[target] = content
                    added += 1
                checked = self.validate(current)
                ensure_private_dir(self.path.parent)
                atomic_write_text(self.path, json.dumps(checked, ensure_ascii=False), mode=0o600)
                return added
