"""P5 §11.3: the support bundle — redacted by construction. The P5 exit
gate requires the bundle to pass secret/path/prompt canaries and size
limits. Each is pinned here against a REAL temporary database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bc250_llm_mode import support_bundle as sb_mod
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.paths import AppPaths
from bc250_llm_mode.repositories import SettingsRepository
from bc250_llm_mode.support_bundle import (
    MAX_FILE_BYTES,
    MAX_LOG_TAIL_BYTES,
    MAX_TOTAL_BYTES,
    PROFILE_LABEL,
    REDACTED,
    SupportBundleCancelled,
    SupportBundleService,
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    Redactor,
    mask_settings,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

FIXED_NOW = "2026-02-01T12:00:00+00:00"

SECRET_CANARY = "CANARY-SECRET-VALUE-12345"
HF_CANARY = "hf_CANARYTOKEN1234567890"
SETTING_CANARY = "CANARYSETTINGSECRET123"
PROMPT_CANARY = "CANARY-PROMPT-SECRET-XYZ"
MODEL_FILENAME = "super-secret-model-name.gguf"


class BundleWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths.temporary(tmp_path / "profile")
        initialize_and_close(self.paths.database_path)
        self.units = UnitOfWorkFactory(self.paths.database_path)
        self.output = tmp_path / "bundle"

    def set_settings(self, **values) -> None:
        with self.units.begin() as conn:
            SettingsRepository(conn).set_many(values)

    def service(self, **kwargs) -> SupportBundleService:
        return SupportBundleService(
            self.units, self.paths, clock=lambda: FIXED_NOW, **kwargs
        )

    def read_all(self) -> str:
        """Concatenate every file in the bundle for canary scanning."""
        chunks = []
        for path in sorted(self.output.rglob("*")):
            if path.is_file():
                chunks.append(path.read_bytes().decode("utf-8", "replace"))
        return "\n".join(chunks)


@pytest.fixture()
def world(tmp_path):
    return BundleWorld(tmp_path)


def _seed_secrets(world: BundleWorld) -> None:
    # A live credential file: read ONLY to feed the scrubber.
    (world.paths.app_dir / "gateway-credential").write_text(SECRET_CANARY)
    # A secret-named setting.
    world.set_settings(hf_token=SETTING_CANARY, current_model="tiny")
    # A log line carrying several secret shapes + the profile path + the
    # sensitive model filename (so redaction is observable).
    world.paths.logs_dir.mkdir(parents=True, exist_ok=True)
    (world.paths.logs_dir / "app.log").write_text(
        f"started with token {SECRET_CANARY}\n"
        f"using {HF_CANARY} for download\n"
        f"profile at {world.paths.app_dir}\n"
        f"loading model {world.paths.models_dir / MODEL_FILENAME}\n"
        f"normal line\n",
        encoding="utf-8",
    )
    # A conversation that must NEVER be read.
    world.paths.conversations_dir.mkdir(parents=True, exist_ok=True)
    (world.paths.conversations_dir / "chat.json").write_text(
        json.dumps([{"role": "user", "content": PROMPT_CANARY}]),
        encoding="utf-8",
    )
    # An installed model with a sensitive filename.
    with world.units.begin() as conn:
        conn.execute(
            "INSERT INTO model_installations (alias, path, quant,"
            " display_name, sampling_json, provenance, validation_status,"
            " imported_at) VALUES ('tiny', ?, 'Q4_K_M', 'Tiny', '{}',"
            " 'test', 'validated', '2026-01-01T00:00:00Z')",
            (str(world.paths.models_dir / MODEL_FILENAME),),
        )


def test_bundle_passes_secret_path_and_prompt_canaries(world):
    _seed_secrets(world)
    manifest = world.service().build(world.output)
    blob = world.read_all()

    # Secret canaries never appear.
    assert SECRET_CANARY not in blob
    assert HF_CANARY not in blob
    assert SETTING_CANARY not in blob
    # Prompt canary never appears (conversations are never read).
    assert PROMPT_CANARY not in blob
    # The raw profile path is normalized to a label.
    assert str(world.paths.app_dir) not in blob
    assert PROFILE_LABEL in blob
    # The credential file content is not copied into the bundle.
    assert not (world.output / "gateway-credential").exists()
    # The raw database is never copied.
    assert not (world.output / "state.db").exists()
    # Manifest is well-formed.
    assert manifest.schema_version == SUPPORT_BUNDLE_SCHEMA_VERSION
    assert manifest.bundle_sha256
    assert manifest.redaction_policy["conversations_included"] is False


def test_bundle_redacts_model_filenames_by_default(world):
    _seed_secrets(world)
    world.service().build(world.output)
    blob = world.read_all()
    assert MODEL_FILENAME not in blob
    assert "<model-1>" in blob


def test_bundle_can_keep_model_filenames_when_requested(world):
    _seed_secrets(world)
    world.service(redact_model_filenames=False).build(world.output)
    blob = world.read_all()
    assert MODEL_FILENAME in blob
    # Secrets are STILL scrubbed even when filenames are kept.
    assert SECRET_CANARY not in blob
    assert PROMPT_CANARY not in blob


def test_bundle_is_size_bounded(world):
    _seed_secrets(world)
    # A log far larger than the per-file tail bound.
    big = world.paths.logs_dir / "big.log"
    big.write_bytes(b"x" * (MAX_LOG_TAIL_BYTES * 4))
    manifest = world.service().build(world.output)
    assert manifest.total_bytes <= MAX_TOTAL_BYTES
    for record in manifest.files:
        assert record["bytes"] <= MAX_FILE_BYTES
    tail = (world.output / "logs" / "big.log.tail")
    assert tail.exists()
    assert tail.stat().st_size <= MAX_LOG_TAIL_BYTES


def test_bundle_is_cancellable(world):
    _seed_secrets(world)
    with pytest.raises(SupportBundleCancelled):
        world.service().build(world.output, cancel=lambda: True)
    # A cancelled build leaves no manifest (nothing misleading).
    assert not (world.output / "manifest.json").exists()


def test_bundle_consistent_with_composed_home_and_doctor(world):
    _seed_secrets(world)
    from bc250_llm_mode.doctor import DoctorService
    from bc250_llm_mode.home import HomeQueryService

    home = HomeQueryService(world.units, world.paths, clock=lambda: FIXED_NOW)
    doctor = DoctorService(world.units, world.paths, clock=lambda: FIXED_NOW)
    service = world.service(home=home, doctor=doctor)
    service.build(world.output)

    home_payload = json.loads((world.output / "home.json").read_text())
    assert set(home_payload["cards"]) == set(home.snapshot().to_dict()["cards"])
    doctor_payload = json.loads((world.output / "doctor.json").read_text())
    assert doctor_payload["schema_version"] == doctor.run().to_dict()["schema_version"]


def test_manifest_records_per_file_digests_and_policy(world):
    _seed_secrets(world)
    manifest = world.service().build(world.output)
    payload = manifest.to_dict()
    assert payload["schema_version"] == SUPPORT_BUNDLE_SCHEMA_VERSION
    assert payload["generated_at"] == FIXED_NOW
    names = {f["path"] for f in payload["files"]}
    assert {"home.json", "doctor.json", "settings.json",
            "operations.json"} <= names
    for record in payload["files"]:
        assert len(record["sha256"]) == 64
    policy = payload["redaction_policy"]
    assert policy["model_filenames_redacted"] is True
    assert policy["raw_database_included"] is False
    json.dumps(payload)


def test_redactor_masks_secret_keys_and_scrubs_text():
    redactor = Redactor("/tmp/profile")
    masked = mask_settings(
        {"hf_token": "abcdef123456789", "plain": "ok",
         "nested": {"api_key": "zzzz999999999", "count": 3}},
        redactor,
    )
    assert masked["hf_token"] == REDACTED
    assert masked["nested"]["api_key"] == REDACTED
    assert masked["plain"] == "ok"
    assert masked["nested"]["count"] == 3

    text = redactor.redact(
        "Authorization: Bearer abcdef1234567890 and hf_XXXXXXXXXXXXXXXX "
        "at /tmp/profile/models/m.gguf"
    )
    assert "abcdef1234567890" not in text
    assert "hf_XXXXXXXXXXXXXXXX" not in text
    assert "/tmp/profile" not in text
    assert PROFILE_LABEL in text


def test_support_bundle_module_is_read_only_by_construction():
    """AST guard: the bundle opens only READ units, imports no process/HTTP
    machinery, and never reads the conversations directory."""
    import ast

    source = Path(sb_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"subprocess", "httpx"}, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {"subprocess", "httpx"}, node.module
        if isinstance(node, ast.Attribute):
            assert node.attr != "begin", "support bundle must be query-only"
    assert "elevated(" not in source
    assert "conversations_dir" not in source


def test_support_bundle_parser_surface():
    from bc250_llm_mode import __main__ as entry

    args = entry._parser().parse_args(
        ("support-bundle", "--output", "/tmp/b"))
    assert args.command == "support-bundle"
    assert args.output == "/tmp/b"
    assert args.keep_model_filenames is False


def test_support_bundle_cli_writes_redacted_bundle(tmp_path, monkeypatch,
                                                   capsys):
    """Exit gate: the CLI surface produces a redacted bundle + manifest."""
    from bc250_llm_mode import __main__ as entry
    from bc250_llm_mode.app import Application

    world = BundleWorld(tmp_path)
    _seed_secrets(world)
    application = Application.compose(world.paths)
    monkeypatch.setattr(
        Application, "compose",
        classmethod(lambda cls, *a, **k: application),
    )
    out = tmp_path / "cli-bundle"
    assert entry.cli(("support-bundle", "--output", str(out))) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["schema_version"] == SUPPORT_BUNDLE_SCHEMA_VERSION
    assert (out / "manifest.json").exists()
    # Canary scrubbed through the CLI path too.
    blob = "\n".join(
        p.read_bytes().decode("utf-8", "replace")
        for p in sorted(out.rglob("*")) if p.is_file())
    assert SECRET_CANARY not in blob
    assert PROMPT_CANARY not in blob
