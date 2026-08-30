from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from bc250_llm_mode.application_update import (
    ApplicationReleaseVerifier,
    ApplicationUpdateQueryService,
    InstalledApplication,
    ReleaseMember,
    ReleaseSetMaterial,
    UnavailableApplicationReleaseChannel,
    UpdateCode,
    UpdateOutcome,
    VerifiedApplicationRelease,
)
from bc250_llm_mode.release_artifacts import Artifact, ArtifactInventory


REPOSITORY = "caamer20/BC250-LLM-MODE"
COMMIT = "a" * 40
POLICY = "sha256:" + "b" * 64
PLATFORM = "bazzite"


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _TestTrust:
    available = True
    trust_root_id = "test-only-root"

    def verify_signature(self, canonical_envelope, signature, mechanism):
        return signature == b"signed" and mechanism == "test-only"

    def verify_provenance(self, provenance, **bindings):
        return provenance == {"test_fixture": "verified"}

    def verify_evidence(self, evidence, **bindings):
        return (
            evidence == {"test_fixture": "verified"}
            and bindings["platform_profile"] == PLATFORM
        )


class _Channel:
    available = True
    channel_id = "test-only"

    def __init__(self, *materials):
        self._materials = materials

    def candidates(self):
        return self._materials


def _member(name: str, role: str, payload: bytes, media="application/json"):
    return ReleaseMember(name, role, _digest(payload), len(payload), media)


def _release_material(
    notes: str = "Security and reliability fixes.",
    *,
    wheel_digest: str = "1" * 64,
    wheel_size: int = 1000,
):
    version = "1.0.0"
    wheel = ReleaseMember(
        "bc250_llm_mode-1.0.0-py3-none-any.whl", "python-wheel",
        wheel_digest, wheel_size, "application/vnd.pypi.wheel.v1",
    )
    sdist = ReleaseMember(
        "bc250_llm_mode-1.0.0.tar.gz", "python-sdist",
        "2" * 64, 2000, "application/gzip",
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {"component": {
            "name": "bc250-llm-mode", "version": version,
            "hashes": [{"alg": "SHA-256", "content": wheel.sha256}],
        }},
        "components": [],
    }
    sbom_member = _member("sbom.cdx.json", "cyclonedx-sbom", _canonical(sbom))
    checksums = (
        f"{wheel.sha256}  {wheel.name}\n"
        f"{sdist.sha256}  {sdist.name}\n"
        f"{sbom_member.sha256}  {sbom_member.name}\n"
    )
    checksums_member = _member(
        "checksums.sha256", "checksums", checksums.encode(), "text/plain"
    )
    inventory_obj = ArtifactInventory((
        Artifact(**wheel.to_dict()),
        Artifact(**sdist.to_dict()),
        Artifact(**checksums_member.to_dict()),
        Artifact(**sbom_member.to_dict()),
    ))
    inventory = inventory_obj.to_dict()
    inventory_digest = inventory_obj.inventory_digest()
    decision = {
        "decision_schema_version": 3,
        "eligible_for_rc": True,
        "eligible_for_1_0_0": True,
        "blocking_codes": [],
        "blocking_explanations": [],
        "accepted_limitations": ["model-conversion"],
        "candidate_version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "repository": REPOSITORY,
        "inventory_digest": inventory_digest,
        "evidence_used": ["test-only-evidence"],
        "evidence_rejected": [],
        "policy_digest": POLICY,
    }
    manifest = {
        "manifest_schema_version": 3,
        "release_status": "QUALIFIED",
        "qualification_level": "final",
        "version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "repository": REPOSITORY,
        "policy_digest": POLICY,
        "inventory": inventory,
        "inventory_digest": inventory_digest,
        "sbom_digest": "sha256:" + sbom_member.sha256,
        "eligible_for_rc": True,
        "eligible_for_1_0_0": True,
        "blocking_codes": [],
        "evidence_used": ["test-only-evidence"],
    }
    manifest["manifest_digest"] = "sha256:" + _digest(_canonical(manifest))
    evidence = {"test_fixture": "verified"}
    provenance = {"test_fixture": "verified"}
    compatibility = {
        "schema_version": 1,
        "package_name": "bc250-llm-mode",
        "source_schema": 13,
        "minimum_readable_schema": 13,
        "maximum_readable_schema": 14,
        "target_schema": 14,
        "supported_platforms": ["bazzite", "arch"],
    }
    signature = b"signed"
    members = (
        wheel,
        sdist,
        checksums_member,
        sbom_member,
        _member("inventory.json", "artifact-inventory", _canonical(inventory)),
        _member("release-manifest.json", "release-manifest", _canonical(manifest)),
        _member("release-decision.json", "release-decision", _canonical(decision)),
        _member("verified-evidence.json", "verified-evidence", _canonical(evidence)),
        _member("provenance.json", "build-provenance", _canonical(provenance)),
        _member("compatibility.json", "database-compatibility", _canonical(compatibility)),
        _member("release-notes.txt", "release-notes", notes.encode(), "text/plain"),
        _member("release-signature.sig", "release-signature", signature,
                "application/octet-stream"),
    )
    envelope = {
        "format_version": 1,
        "verifier_policy_version": 1,
        "repository": REPOSITORY,
        "version": version,
        "source_commit": COMMIT,
        "source_ref": f"refs/tags/v{version}",
        "published_at": "2026-08-30T12:00:00Z",
        "trust_root_id": "test-only-root",
        "signature_mechanism": "test-only",
        "members": [
            item.to_dict() for item in sorted(
                (item for item in members if item.role != "release-signature"),
                key=lambda item: (item.role, item.name),
            )
        ],
    }
    return ReleaseSetMaterial(
        envelope=envelope,
        decision=decision,
        manifest=manifest,
        inventory=inventory,
        checksums=checksums,
        sbom=sbom,
        evidence=evidence,
        provenance=provenance,
        compatibility=compatibility,
        release_notes=notes,
        signature=signature,
        members=members,
    )


def _verifier():
    return ApplicationReleaseVerifier(
        expected_repository=REPOSITORY, trust=_TestTrust()
    )


def _installed():
    return InstalledApplication(
        "0.9.0.dev0", "c" * 40, "d" * 64, "current", 13,
        current_installation_id="old-release", previous_installation_id="older",
        pointer_generation=7, revision=4,
    )


def test_production_default_is_honestly_unavailable():
    verifier = ApplicationReleaseVerifier(expected_repository=REPOSITORY)
    result = verifier.verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    )
    assert not result.verified
    assert result.reason_code is UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE

    service = ApplicationUpdateQueryService(
        installed=_installed(), verifier=verifier, platform_profile=PLATFORM,
        channel=UnavailableApplicationReleaseChannel(),
    )
    assert service.status().reason_code is UpdateCode.SIGNED_UPDATE_CHANNEL_UNAVAILABLE
    assert service.check().release is None
    assert service.preview("1.0.0").outcome is UpdateOutcome.UNAVAILABLE


def test_complete_evaluator_bound_release_verifies():
    result = _verifier().verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    )
    assert result.verified
    assert result.release.version == "1.0.0"
    assert result.release.source_ref == "refs/tags/v1.0.0"
    assert result.release.source_schema == 13
    assert result.release.target_schema == 14


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("repository", "attacker/project", UpdateCode.UNTRUSTED_REPOSITORY),
        ("source_ref", "refs/heads/main", UpdateCode.IMMUTABLE_REF_REQUIRED),
        ("trust_root_id", "unknown", UpdateCode.SIGNATURE_INVALID),
    ],
)
def test_envelope_trust_bindings_fail_closed(field, value, code):
    material = _release_material()
    material = replace(material, envelope={**material.envelope, field: value})
    result = _verifier().verify(
        material, installed_schema=13, platform_profile=PLATFORM
    )
    assert result.reason_code is code
    assert result.release is None


def test_decision_manifest_inventory_and_member_tamper_are_rejected():
    material = _release_material()
    bad_decision = replace(
        material,
        decision={**material.decision, "eligible_for_1_0_0": False},
    )
    assert _verifier().verify(
        bad_decision, installed_schema=13, platform_profile=PLATFORM
    ).reason_code is UpdateCode.BUNDLE_MEMBER_REFUSED

    bad_member = list(material.members)
    bad_member[0] = replace(bad_member[0], sha256="f" * 64)
    assert _verifier().verify(
        replace(material, members=tuple(bad_member)),
        installed_schema=13, platform_profile=PLATFORM,
    ).reason_code is UpdateCode.BUNDLE_MEMBER_REFUSED


def test_compatibility_platform_and_plain_text_note_gates():
    material = _release_material()
    assert _verifier().verify(
        material, installed_schema=12, platform_profile=PLATFORM
    ).reason_code is UpdateCode.DATABASE_INCOMPATIBLE
    assert _verifier().verify(
        material, installed_schema=13, platform_profile="debian"
    ).reason_code is UpdateCode.PLATFORM_EVIDENCE_MISSING

    unsafe = _release_material("looks fine\x1b[31mnot fine")
    assert _verifier().verify(
        unsafe, installed_schema=13, platform_profile=PLATFORM
    ).reason_code is UpdateCode.NOTES_INVALID


def test_verified_release_cannot_be_forged_by_a_caller():
    with pytest.raises(ValueError):
        VerifiedApplicationRelease(
            "1.0.0", COMMIT, "refs/tags/v1.0.0", REPOSITORY,
            "1" * 64, "sha256:" + "2" * 64, "sha256:" + "3" * 64,
            "4" * 64, "2026-08-30T12:00:00Z", "notes",
            13, 13, 14, 14, PLATFORM, 10, 12, "root",
        )
    genuine = _verifier().verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    ).release
    with pytest.raises(ValueError):
        replace(genuine, repository="attacker/project")


def test_preview_is_revision_bound_literal_and_space_checked():
    notes = "# Literal heading\n<a href='https://bad'>not rendered</a>"
    material = _release_material(notes)
    fixed_now = lambda: datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    service = ApplicationUpdateQueryService(
        installed=_installed(), verifier=_verifier(), platform_profile=PLATFORM,
        channel=_Channel(material), free_bytes=2 * 1024 * 1024 * 1024,
        now=fixed_now,
    )
    checked = service.check()
    assert checked.release.version == "1.0.0"
    preview = service.preview("1.0.0")
    assert preview.outcome is UpdateOutcome.READY
    assert preview.release_notes_plain_text == notes
    assert preview.expected_installation_revision == 4
    assert preview.rollback_installation_id == "old-release"
    assert preview.profile_restore_on_rollback is True
    assert len(preview.preview_digest) == len(preview.confirmation_token) == 64

    too_small = ApplicationUpdateQueryService(
        installed=_installed(), verifier=_verifier(), platform_profile=PLATFORM,
        channel=_Channel(material), free_bytes=1,
    ).preview("1.0.0")
    assert too_small.reason_code is UpdateCode.INSUFFICIENT_SPACE
    assert too_small.confirmation_token is None


def _create_v13_database(path, monkeypatch):
    from bc250_llm_mode import db

    migrations = db.MIGRATIONS
    version = db.SCHEMA_VERSION
    monkeypatch.setattr(db, "MIGRATIONS", tuple(
        item for item in migrations if item[0] <= 13
    ))
    monkeypatch.setattr(db, "SCHEMA_VERSION", 13)
    conn = db.open_database(path, mode="migration")
    try:
        assert db.initialize(conn) == 13
        conn.execute("CREATE TABLE preserved_exp6_marker(value TEXT)")
        conn.execute("INSERT INTO preserved_exp6_marker VALUES ('keep-me')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    monkeypatch.setattr(db, "SCHEMA_VERSION", version)


def test_migration_014_is_atomic_preserves_v13_and_seeds_no_fake_install(tmp_path, monkeypatch):
    import sqlite3

    from bc250_llm_mode import db

    path = tmp_path / "v13.db"
    _create_v13_database(path, monkeypatch)
    conn = db.open_database(path, mode="migration")
    try:
        assert db.initialize(conn) == db.SCHEMA_VERSION == 14
        assert conn.execute(
            "SELECT value FROM preserved_exp6_marker"
        ).fetchone()[0] == "keep-me"
        assert conn.execute(
            "SELECT COUNT(*) FROM application_installations"
        ).fetchone()[0] == 0
        state = conn.execute(
            "SELECT current_installation_id, previous_installation_id, "
            "pointer_generation FROM application_installation_state WHERE id=1"
        ).fetchone()
        assert tuple(state) == (None, None, 0)
    finally:
        conn.close()

    atomic = tmp_path / "atomic.db"
    _create_v13_database(atomic, monkeypatch)
    migration = next(item for item in db.MIGRATIONS if item[0] == 14)
    broken = (14, migration[1], migration[2][:-1] + (
        "INSERT INTO absent_table VALUES (1)",
    ))
    monkeypatch.setattr(
        db, "MIGRATIONS",
        tuple(item for item in db.MIGRATIONS if item[0] < 14) + (broken,),
    )
    conn = db.open_database(atomic, mode="migration")
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.initialize(conn)
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "application_installations" not in tables
        assert "application_installation_state" not in tables
        assert "application_update_imports" not in tables
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 13
    finally:
        conn.close()


def test_installation_and_import_repositories_are_typed_and_revision_fenced(tmp_path):
    from bc250_llm_mode.application_installation import (
        ApplicationInstallationError,
        ApplicationInstallationRepository,
        ApplicationUpdateImportRepository,
        ImportSource,
        ImportState,
        InstallationState,
        SmokeState,
    )
    from bc250_llm_mode.db import initialize_and_close, open_database

    database = tmp_path / "state.db"
    initialize_and_close(database)
    release = _verifier().verify(
        _release_material(), installed_schema=13, platform_profile=PLATFORM
    ).release
    conn = open_database(database, mode="write")
    try:
        installations = ApplicationInstallationRepository(
            conn, clock=lambda: "2026-08-30T12:00:00Z"
        )
        row = installations.insert_staged(
            release, created_by_operation_id=None
        )
        assert row.installation_id == release.release_set_digest
        assert row.state is InstallationState.STAGED
        assert row.smoke_state is SmokeState.PENDING
        smoked = installations.mark_smoke(
            row.installation_id, state=SmokeState.PASSED,
            expected_revision=row.revision,
        )
        assert smoked.smoke_state is SmokeState.PASSED
        with pytest.raises(ApplicationInstallationError, match="CAS fence"):
            installations.mark_smoke(
                row.installation_id, state=SmokeState.FAILED,
                expected_revision=row.revision,
            )
        current = installations.transition(
            row.installation_id, target=InstallationState.CURRENT,
            expected_state=InstallationState.STAGED,
            expected_revision=smoked.revision,
        )
        assert current.state is InstallationState.CURRENT
        with pytest.raises(ApplicationInstallationError, match="invalid"):
            installations.transition(
                row.installation_id, target=InstallationState.QUARANTINED,
                expected_state=InstallationState.CURRENT,
                expected_revision=current.revision,
            )

        imports = ApplicationUpdateImportRepository(
            conn, clock=lambda: "2026-08-30T12:00:00Z"
        )
        imported = imports.record_verified(
            release, source_class=ImportSource.OFFLINE
        )
        assert imported.state is ImportState.VERIFIED
        consumed = imports.transition(
            release.release_set_digest,
            target=ImportState.CONSUMED,
            expected_revision=imported.revision,
        )
        assert consumed.state is ImportState.CONSUMED
        with pytest.raises(ApplicationInstallationError, match="CAS fence"):
            imports.transition(
                release.release_set_digest,
                target=ImportState.QUARANTINED,
                expected_revision=imported.revision,
            )
        conn.commit()
    finally:
        conn.close()


class _FakeInstaller:
    def __init__(self):
        self.install_calls = 0
        self.probe_calls = 0

    def install(self, *, venv_dir, wheel_path, release):
        self.install_calls += 1
        venv_dir.mkdir(parents=True)
        (venv_dir / "installed.marker").write_text(
            release.release_set_digest, encoding="utf-8"
        )
        return ("EXACT_WHEEL_INSTALLED", "PIP_CHECK_PASSED")

    def probe(self, *, venv_dir, release):
        self.probe_calls += 1
        marker = venv_dir / "installed.marker"
        ok = marker.is_file() and marker.read_text(
            encoding="utf-8"
        ) == release.release_set_digest
        return ok, (("PACKAGE_IMPORT_SMOKE_PASSED",) if ok else ("SMOKE_FAILED",))


def test_isolated_staging_is_idempotent_and_never_switches_pointer(tmp_path):
    from bc250_llm_mode.application_staging import (
        ApplicationInstallationStager,
        ApplicationStageRequest,
    )
    from bc250_llm_mode.paths import AppPaths

    wheel_bytes = b"exact signed wheel bytes"
    wheel_digest = hashlib.sha256(wheel_bytes).hexdigest()
    material = _release_material(
        wheel_digest=wheel_digest, wheel_size=len(wheel_bytes)
    )
    release = _verifier().verify(
        material, installed_schema=13, platform_profile=PLATFORM
    ).release
    bundle = tmp_path / "held-bundle"
    bundle.mkdir()
    wheel_name = material.members[0].name
    (bundle / wheel_name).write_bytes(wheel_bytes)
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    installer = _FakeInstaller()
    stager = ApplicationInstallationStager(paths, installer=installer)
    request = ApplicationStageRequest(
        operation_id="update-op-1", release=release,
        bundle_root=bundle.resolve(), wheel_name=wheel_name,
        wheel_size=len(wheel_bytes),
    )
    first = stager.stage(request)
    second = stager.stage(request)
    assert first.stage_identity == second.stage_identity
    assert first.receipt_digest == second.receipt_digest
    assert second.already_complete is True
    assert installer.install_calls == 1
    assert not paths.application_current_link.exists()
    assert not paths.application_previous_link.exists()
    receipt = (
        paths.application_release_staging_dir / first.stage_identity /
        ".bc250-release.json"
    ).read_text(encoding="utf-8")
    assert str(bundle) not in receipt


def test_staging_rejects_mutated_or_linked_wheel_and_keeps_running_tree_untouched(tmp_path):
    from bc250_llm_mode.application_staging import (
        ApplicationInstallationStager,
        ApplicationStageRequest,
        ApplicationStagingError,
    )
    from bc250_llm_mode.paths import AppPaths

    wheel_bytes = b"wheel"
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    material = _release_material(wheel_digest=digest, wheel_size=len(wheel_bytes))
    release = _verifier().verify(
        material, installed_schema=13, platform_profile=PLATFORM
    ).release
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    name = material.members[0].name
    wheel = bundle / name
    wheel.write_bytes(b"tampered")
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    stager = ApplicationInstallationStager(paths, installer=_FakeInstaller())
    request = ApplicationStageRequest(
        "update-op-2", release, bundle.resolve(), name, len(wheel_bytes)
    )
    with pytest.raises(ApplicationStagingError) as failure:
        stager.stage(request)
    assert failure.value.code == "BUNDLE_MEMBER_REFUSED"
    assert list(paths.application_release_staging_dir.iterdir()) == []

    wheel.unlink()
    target = bundle / "real.whl"
    target.write_bytes(wheel_bytes)
    wheel.symlink_to(target.name)
    with pytest.raises(ApplicationStagingError) as failure:
        stager.stage(request)
    assert failure.value.code == "BUNDLE_MEMBER_REFUSED"


def _write_release_tree(path, identity):
    path.mkdir(parents=True)
    (path / "venv").mkdir()
    (path / ".bc250-release.json").write_text(json.dumps({
        "schema_version": 1,
        "operation_id": "fixture",
        "stage_identity": path.name,
        "release_set_digest": identity,
        "wheel_digest": "f" * 64,
        "version": "1.0.0",
        "smoke_codes": ["PACKAGE_IMPORT_SMOKE_PASSED"],
    }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


class _PointerDeath(BaseException):
    pass


def test_pointer_publication_recovers_death_between_ordered_atomic_replaces(tmp_path):
    from bc250_llm_mode.application_pointer_helper import observe_pointers
    from bc250_llm_mode.application_publisher import (
        ApplicationPointerPublisher,
        PointerPublishRequest,
    )
    from bc250_llm_mode.paths import AppPaths

    old_current, old_previous, candidate = "a" * 64, "b" * 64, "c" * 64
    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    _write_release_tree(paths.application_releases_dir / old_current, old_current)
    _write_release_tree(paths.application_releases_dir / old_previous, old_previous)
    stage_identity = "update-op-pointer-candidate"
    _write_release_tree(
        paths.application_release_staging_dir / stage_identity, candidate
    )
    paths.application_current_link.symlink_to(f"releases/{old_current}")
    paths.application_previous_link.symlink_to(f"releases/{old_previous}")
    request = PointerPublishRequest(
        operation_id="update-op-pointer",
        candidate_installation_id=candidate,
        expected_current_installation_id=old_current,
        expected_previous_installation_id=old_previous,
        expected_pointer_generation=4,
        stage_identity=stage_identity,
    )

    def die(point):
        if point == "after_previous_replace":
            raise _PointerDeath(point)

    with pytest.raises(_PointerDeath):
        ApplicationPointerPublisher(paths, crash_hook=die).publish(request)
    prepared = observe_pointers(paths.app_dir)
    assert prepared.current == old_current
    assert prepared.previous == old_current
    assert (paths.application_releases_dir / candidate).is_dir()

    result = ApplicationPointerPublisher(paths).publish(request)
    assert result.recovered is True
    assert result.pointer_generation == 5
    published = observe_pointers(paths.app_dir)
    assert (published.current, published.previous) == (candidate, old_current)
    assert not paths.application_release_staging_dir.joinpath(stage_identity).exists()

    restored = ApplicationPointerPublisher(paths).restore(request)
    assert restored.pointer_generation == 6
    after_rollback = observe_pointers(paths.app_dir)
    assert (after_rollback.current, after_rollback.previous) == (
        old_current, candidate
    )


def test_pointer_helper_digest_hostility_and_unknown_pointer_refuse(tmp_path):
    from bc250_llm_mode.application_pointer_helper import (
        POINTER_HELPER_DIGEST,
        POINTER_HELPER_SOURCE,
        PointerRefusal,
        observe_pointers,
        verify_pointer_helper_digest,
    )
    from bc250_llm_mode.paths import AppPaths

    assert hashlib.sha256(POINTER_HELPER_SOURCE.encode()).hexdigest() == (
        POINTER_HELPER_DIGEST
    )
    verify_pointer_helper_digest(POINTER_HELPER_DIGEST)
    with pytest.raises(PointerRefusal, match="POINTER_HELPER_DIGEST_MISMATCH"):
        verify_pointer_helper_digest("0" * 64)

    paths = AppPaths.temporary(tmp_path / "profile")
    paths.ensure_directories()
    paths.application_current_link.write_text("not a link", encoding="utf-8")
    with pytest.raises(PointerRefusal) as failure:
        observe_pointers(paths.app_dir)
    assert failure.value.code == "POINTER_NOT_SYMLINK"


class _UpdateFakeHost:
    def __init__(self):
        self.effects = {}
        self.restored = set()

    def _effect(self, key):
        if not self.effects.get(key):
            self.effects[key] = 1
        return {"identity": key}

    def _probe(self, key):
        from bc250_llm_mode.operations.recovery import RecoveryClass
        from bc250_llm_mode.operations.workflow import ProbeResult

        return ProbeResult(
            RecoveryClass.COMPLETE if self.effects.get(key)
            else RecoveryClass.ABSENT,
            f"{key.upper()}_" + ("COMPLETE" if self.effects.get(key) else "ABSENT"),
            {key: {"identity": key}} if self.effects.get(key) else None,
        )

    def verify_release(self, ctx): return self._effect("release")
    def probe_release(self, ctx): return self._probe("release")
    def stage_candidate(self, ctx): return self._effect("stage")
    def probe_staged(self, ctx): return self._probe("stage")
    def ensure_backup(self, ctx): return self._effect("backup")
    def probe_backup(self, ctx): return self._probe("backup")
    def publish_pointer(self, ctx): return self._effect("pointer")
    def probe_pointer(self, ctx): return self._probe("pointer")
    def launch_post_update(self, ctx): return self._effect("ack")
    def probe_acknowledgment(self, ctx): return self._probe("ack")
    def verify_health(self, ctx): return self._effect("health")
    def probe_health(self, ctx): return self._probe("health")
    def record_installation(self, ctx):
        self._effect("record")
        return {"pointer_generation": 2}
    def probe_recorded(self, ctx): return self._probe("record")

    def discard_stage(self, ctx):
        self.restored.add("stage")
        return {"retained": True}

    def probe_stage_discarded(self, ctx):
        from bc250_llm_mode.operations.recovery import RecoveryClass
        from bc250_llm_mode.operations.workflow import ProbeResult
        return ProbeResult(
            RecoveryClass.COMPLETE if "stage" in self.restored else RecoveryClass.ABSENT,
            "STAGE_RETAINED" if "stage" in self.restored else "STAGE_ACTIVE",
        )

    def restore_pointer(self, ctx):
        self.restored.add("pointer")
        return {"restored": True}

    def probe_pointer_restored(self, ctx):
        from bc250_llm_mode.operations.recovery import RecoveryClass
        from bc250_llm_mode.operations.workflow import ProbeResult
        return ProbeResult(
            RecoveryClass.COMPLETE if "pointer" in self.restored else RecoveryClass.ABSENT,
            "POINTER_RESTORED" if "pointer" in self.restored else "POINTER_ACTIVE",
        )

    def restore_profile(self, ctx):
        self.restored.add("profile")
        return {"restored": True}

    def probe_profile_restored(self, ctx):
        from bc250_llm_mode.operations.recovery import RecoveryClass
        from bc250_llm_mode.operations.workflow import ProbeResult
        return ProbeResult(
            RecoveryClass.COMPLETE if "profile" in self.restored else RecoveryClass.ABSENT,
            "PROFILE_RESTORED" if "profile" in self.restored else "PROFILE_ACTIVE",
        )


class _UpdateClock:
    def __init__(self):
        self.second = 0

    def now(self):
        return f"2026-08-30T12:{self.second // 60:02d}:{self.second % 60:02d}Z"

    def advance(self, seconds):
        self.second += seconds


def _update_payload():
    return {
        "mode": "APPLY",
        "release_set_digest": "c" * 64,
        "expected_current_installation_id": "a" * 64,
        "expected_previous_installation_id": "b" * 64,
        "expected_pointer_generation": 1,
        "preview_digest": "d" * 64,
        "confirmation_digest": "e" * 64,
        "requested_by": "test",
    }


def test_application_update_workflow_recovers_death_after_pointer_effect_once(tmp_path):
    from bc250_llm_mode.db import initialize_and_close
    from bc250_llm_mode.operations.application_update import (
        UPDATE_RESOURCES,
        build_application_update_workflow,
    )
    from bc250_llm_mode.operations.engine import ExecutionEngine
    from bc250_llm_mode.operations.model import OperationState, OperationType
    from bc250_llm_mode.operations.repositories import OperationRepository
    from bc250_llm_mode.operations.workflow import EnqueueService, WorkflowRegistry
    from bc250_llm_mode.unit_of_work import UnitOfWorkFactory

    database = tmp_path / "operations.db"
    initialize_and_close(database)
    units = UnitOfWorkFactory(database)
    host = _UpdateFakeHost()
    definition = build_application_update_workflow(host)
    assert definition.all_resources() == UPDATE_RESOURCES
    assert definition.phase_scoped_resources is True
    registry = WorkflowRegistry()
    registry.register(definition)
    registry = registry.freeze()
    clock = _UpdateClock()
    ids = iter(f"effect-{index}" for index in range(100))
    enqueue = EnqueueService(
        units, registry, clock=clock.now, uuid_factory=lambda: next(ids)
    )
    operation = enqueue.enqueue(
        operation_type=OperationType.APPLICATION_UPDATE,
        payload=_update_payload(), surface="test", operation_id="app-update-1",
    )

    class _Death(BaseException): pass
    fired = False

    def crash(step, point):
        nonlocal fired
        if not fired and step == "publish_pointers" and point == "before_step_checkpoint":
            fired = True
            raise _Death()

    worker_a = ExecutionEngine(
        units, registry, clock=clock.now, uuid_factory=lambda: next(ids),
        worker_id="worker-a", lease_ttl_seconds=60, crash_hook=crash,
    )
    with pytest.raises(_Death):
        worker_a.execute_one(operation.id)
    assert host.effects["pointer"] == 1
    with units.read() as conn:
        row = OperationRepository(conn).require(operation.id)
        assert row.state in {OperationState.RUNNING, OperationState.COMMITTING}

    clock.advance(120)
    worker_b = ExecutionEngine(
        units, registry, clock=clock.now, uuid_factory=lambda: next(ids),
        worker_id="worker-b", lease_ttl_seconds=60,
    )
    outcome = worker_b.execute_one(operation.id)
    assert outcome.kind == "COMPLETED"
    assert host.effects["pointer"] == 1
    with units.read() as conn:
        final = OperationRepository(conn).require(operation.id)
        assert final.state is OperationState.SUCCEEDED
        assert final.result_code == "APPLICATION_UPDATED"
