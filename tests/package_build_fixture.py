"""Build-input-only copies keep ignored historical output out of wheel gates."""
from pathlib import Path
import shutil


def clean_build_source(tmp_path):
    root = Path(__file__).resolve().parent.parent
    source = tmp_path / "clean-source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(root / name, source / name)
    shutil.copytree(root / "bc250_llm_mode", source / "bc250_llm_mode",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return source
