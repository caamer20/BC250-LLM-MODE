from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bc250_llm_mode.operations.application_update import (
    decode_application_update_request,
)
from bc250_llm_mode.operations.validation import OperationValidationError

ROOT = Path(__file__).parent.parent


def test_physical_update_round_trip_is_detailed_and_still_explicitly_pending():
    guide = (ROOT / "docs/application-update-physical-qualification.md").read_text(
        encoding="utf-8"
    )
    assert "Status: PENDING" in guide
    assert "Bazzite" in guide and "CachyOS" in guide
    for required in (
        "eligible signed", "offline", "replacement process", "rollback",
        "BACKUP_RESTORE v1", "RECOVERY_REQUIRED", "eight-hour",
        "no LLM running", "release/EVIDENCE_HANDOFF.md",
    ):
        assert required in guide
    assert "- [x]" not in guide.lower()


def test_update_request_cannot_persist_path_url_signature_or_raw_confirmation():
    base = {
        "mode": "APPLY",
        "release_set_digest": "a" * 64,
        "expected_current_installation_id": "b" * 64,
        "expected_previous_installation_id": "c" * 64,
        "expected_pointer_generation": 1,
        "preview_digest": "d" * 64,
        "confirmation_digest": "e" * 64,
        "requested_by": "test",
    }
    for field in ("path", "url", "signature", "confirmation_token", "notes"):
        with pytest.raises(OperationValidationError):
            decode_application_update_request({**base, field: "canary"})


def test_offline_reader_is_streamed_and_never_uses_unsafe_archive_extraction():
    source = (ROOT / "bc250_llm_mode/application_bundle.py").read_text(
        encoding="utf-8"
    )
    assert "extractall" not in source
    assert ".getmembers(" not in source
    assert "COPY_CHUNK_BYTES" in source
    assert "info.isreg()" in source
    assert "info.issparse()" in source
    assert "casefold()" in source
    assert "unicodedata.normalize" in source


def test_updates_refresh_calls_status_only_and_never_checks_or_mutates():
    source_path = ROOT / "bc250_llm_mode/gui/updates_page.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    refresh = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "refresh"
    )
    text = ast.unparse(refresh)
    assert "application_update_commands.status" in text
    for forbidden in (".check(", ".import_bundle(", ".apply(", ".rollback("):
        assert forbidden not in text


def test_update_page_and_post_update_mode_create_no_second_window_or_autostart():
    gui = (ROOT / "bc250_llm_mode/gui/updates_page.py").read_text(encoding="utf-8")
    post = (ROOT / "bc250_llm_mode/application_post_update.py").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "bc250_llm_mode/desktop_integration.py").read_text(
        encoding="utf-8"
    )
    assert "Toplevel" not in gui and "messagebox" not in gui
    assert "start_service" not in post and "ensure_server" not in post
    assert "application_current_link" in launcher
    assert "Autostart" not in launcher


def test_production_composition_has_no_mutable_or_package_index_update_source():
    app = (ROOT / "bc250_llm_mode/app.py").read_text(encoding="utf-8")
    update = (ROOT / "bc250_llm_mode/application_update.py").read_text(
        encoding="utf-8"
    )
    bundle = (ROOT / "bc250_llm_mode/application_bundle.py").read_text(
        encoding="utf-8"
    )
    combined = app + update + bundle
    assert "refs/heads/" not in combined
    assert "pip install --upgrade" not in combined
    assert "http://" not in combined and "https://" not in combined
    assert "EXPECTED_RELEASE_REPOSITORY" in app
    assert "UnavailableReleaseTrustAdapter" in update


def test_exp6_changelog_does_not_claim_external_qualification():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog.split("### Appliance experience EXP-6", 1)[1].split(
        "### Appliance experience EXP-5", 1
    )[0]
    assert "evidence remain release/owner-gated and are not claimed" in section
