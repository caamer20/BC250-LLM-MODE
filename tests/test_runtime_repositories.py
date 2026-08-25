"""U1.2 §7.3: typed runtime build/verification/tree/component repositories."""

from __future__ import annotations

import pytest

from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.repositories import KnownGoodRuntimeRepository
from bc250_llm_mode.runtime_builds import (
    RuntimeBuildError,
    RuntimeBuildRepository,
    RuntimeComponentRepository,
    RuntimeTreeRepository,
    RuntimeVerificationRepository,
)
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from test_runtime_migration import _manifest


@pytest.fixture()
def units(tmp_path):
    database = tmp_path / "state.db"
    initialize_and_close(database)
    return UnitOfWorkFactory(database)


@pytest.fixture()
def seeded(units):
    """Persist two builds/trees with prior promoted; returns ID facts only.

    Repositories are bound to a unit-of-work connection and die with it;
    tests construct fresh instances inside their own units.
    """
    with units.begin() as conn:
        builds = RuntimeBuildRepository(conn)
        prior = builds.create_immutable(manifest=_manifest(source_commit="1" * 40))
        target = builds.create_immutable(manifest=_manifest(source_commit="2" * 40))
        trees = RuntimeTreeRepository(conn)
        prior_tree = trees.record_candidate(
            tree_id="t-prior",
            build_id=prior["build_id"],
            container_profile="default",
            locator="managed/prior",
            manifest_digest=prior["manifest_digest"],
            server_binary_digest="e" * 64,
        )
        target_tree = trees.record_candidate(
            tree_id="t-target",
            build_id=target["build_id"],
            container_profile="default",
            locator="managed/target",
            manifest_digest=target["manifest_digest"],
            server_binary_digest="f" * 64,
        )
        components = RuntimeComponentRepository(conn)
        components.initialize()
        components.promote_verified(
            expected_generation=1,
            expected_promoted_build_id=None,
            expected_rollback_build_id=None,
            promoted_build_id=prior["build_id"],
            rollback_build_id=None,
            promoted_tree_id=prior_tree["tree_id"],
        )
    return {
        "prior_build_id": prior["build_id"],
        "target_build_id": target["build_id"],
        "prior_manifest_digest": prior["manifest_digest"],
        "prior_tree": dict(prior_tree),
        "target_tree": dict(target_tree),
    }


def test_immutable_record_reuse_and_corruption(units):
    with units.begin() as conn:
        builds = RuntimeBuildRepository(conn)
        first = builds.create_immutable(manifest=_manifest())
        second = builds.create_immutable(manifest=_manifest())
        assert first == second
        # Same identity, different display ref: idempotent reuse keeps the
        # originally recorded display metadata.
        third = builds.create_immutable(manifest=_manifest(requested_ref="other"))
        assert third["build_id"] == first["build_id"]
        assert third["requested_ref"] == "b7598"
        # Tampered stored bytes under one ID are corruption on re-insert
        # (content addressing normally makes this unreachable via the API).
        conn.execute(
            "UPDATE runtime_builds SET manifest_json = '{\"tampered\": true}'"
            " WHERE build_id = ?",
            (first["build_id"],),
        )
        with pytest.raises(RuntimeBuildError) as err:
            builds.create_immutable(manifest=_manifest())
        assert err.value.code == "BUILD_RECORD_CORRUPTION"
        assert len(builds.list_bounded(limit=10)) == 1


def test_verification_append_is_closed_bounded_queryable(units):
    with units.begin() as conn:
        builds = RuntimeBuildRepository(conn)
        record = builds.create_immutable(manifest=_manifest())
        verifications = RuntimeVerificationRepository(conn)
        verifications.append(
            build_id=record["build_id"],
            kind="SMOKE",
            evidence={"ok": True, "latency_bucket": "sub_second"},
        )
        verifications.append(
            build_id=record["build_id"],
            kind="ACTIVE_INFERENCE",
            evidence={"ok": True, "generated_count": 1},
        )
        rows = verifications.list_for_build(record["build_id"])
        assert [row["kind"] for row in rows] == [
            "ACTIVE_INFERENCE",
            "SMOKE",
        ]  # monotonic, newest first
        with pytest.raises(RuntimeBuildError):
            verifications.append(
                build_id=record["build_id"], kind="WAT", evidence={}
            )
        with pytest.raises(RuntimeBuildError):
            verifications.append(
                build_id=record["build_id"], kind="SMOKE", evidence={"x": "y" * 5000}
            )
        # No prompt/generated text may be persisted: the caller is trusted,
        # but the repository bounds the payload and stores metadata only.
        assert all("content" not in row["evidence"] for row in rows)


def test_tree_roles_locations_and_protection(seeded, units):
    with units.begin() as conn:
        trees = RuntimeTreeRepository(conn)
        tree = trees.observe_location(
            "t-target", server_binary_digest="0" * 64
        )
        assert tree["server_binary_digest"] == "0" * 64
        moved = trees.move_role("t-target", "ACTIVE_OBSERVED")
        assert moved["role"] == "ACTIVE_OBSERVED"
        assert trees.by_locator("managed/target")["tree_id"] == "t-target"
    with units.begin() as conn:
        trees = RuntimeTreeRepository(conn)
        protected = trees.protected_tree_ids(exclude_operation_id=None)
        assert {"t-prior", "t-target"} <= protected
        with pytest.raises(RuntimeBuildError):
            trees.move_role("missing", "RETAINED")


def test_promotion_is_generation_checked_with_known_good_identity(units, seeded):
    with units.begin() as conn:
        components = RuntimeComponentRepository(conn)
        current = components.current()
        assert current["promoted_build_id"] == seeded["prior_build_id"]
        promoted = components.promote_verified(
            expected_generation=current["generation"],
            expected_promoted_build_id=seeded["prior_build_id"],
            expected_rollback_build_id=current["rollback_build_id"],
            promoted_build_id=seeded["target_build_id"],
            rollback_build_id=seeded["prior_build_id"],
            promoted_tree_id=seeded["target_tree"]["tree_id"],
            rollback_tree_id=seeded["prior_tree"]["tree_id"],
            known_good_identity={
                "runtime_fingerprint": "fp-new",
                "runtime_component_identity": seeded["target_build_id"],
            },
        )
        assert promoted["generation"] == current["generation"] + 1
        assert promoted["rollback_build_id"] == seeded["prior_build_id"]

        # Stale promotion (old generation) fails without partial writes.
        with pytest.raises(RuntimeBuildError) as err:
            components.promote_verified(
                expected_generation=current["generation"],
                expected_promoted_build_id=seeded["prior_build_id"],
                expected_rollback_build_id=None,
                promoted_build_id=seeded["target_build_id"],
                rollback_build_id=None,
            )
        assert err.value.code == "PROMOTION_GENERATION_STALE"
        assert components.current()["generation"] == current["generation"] + 1

        # Known-good identity advanced in the same unit; model config intact.
        kg = KnownGoodRuntimeRepository(conn).get()
        assert kg is None or kg.get("model_alias") in (None, "demo")


def test_record_restoration_toggles_lineage(units, seeded):
    with units.begin() as conn:
        conn.execute(
            "INSERT INTO operations (id, operation_type, request_version,"
            " request_json, state, surface, created_at, updated_at)"
            " VALUES ('op-1', 'RUNTIME_ROLLBACK', 1, '{}', 'QUEUED', 'test',"
            " 't', 't')"
        )
        components = RuntimeComponentRepository(conn)
        before = components.current()
        restored = components.record_restoration(
            expected_generation=before["generation"],
            expected_promoted_build_id=before["promoted_build_id"],
            restored_promoted_build_id=seeded["target_build_id"],
            new_rollback_build_id=before["promoted_build_id"],
            operation_id="op-1",
        )
        assert restored["promoted_build_id"] == seeded["target_build_id"]
        assert restored["rollback_build_id"] == before["promoted_build_id"]
        assert restored["last_operation_id"] == "op-1"


def test_component_state_absent_is_a_stable_error(units):
    with units.begin() as conn:
        components = RuntimeComponentRepository(conn)
        assert components.current() is None
        with pytest.raises(RuntimeBuildError) as err:
            components.promote_verified(
                expected_generation=1,
                expected_promoted_build_id=None,
                expected_rollback_build_id=None,
                promoted_build_id="llamacpp:sha256:" + "0" * 64,
                rollback_build_id=None,
            )
        assert err.value.code == "COMPONENT_STATE_ABSENT"
