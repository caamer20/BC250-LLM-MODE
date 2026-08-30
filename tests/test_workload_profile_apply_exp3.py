"""EXP-3 profile preview, binding, activation, and rollback qualification."""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from bc250_llm_mode.activation_adapter import ArtifactRejected
from bc250_llm_mode.activation_command import ActivationCommandService
from bc250_llm_mode import __main__ as entry
from bc250_llm_mode.app import Application
from bc250_llm_mode.operations.activation import ModelActivateRequestV1
from bc250_llm_mode.operations.workflow import EnqueueService
from bc250_llm_mode.repositories import SettingsRepository, ThermalStateRepository
from bc250_llm_mode.workload_profiles import (
    ProfileBinding,
    WorkloadProfileCommandError,
    WorkloadProfileCommandService,
    WorkloadProfileRepository,
    decode_profile_binding,
)

from test_activation_adapter import _engine, _enqueue, world  # noqa: F401


CUSTOM = "a" * 32


class RecordingActivation:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def activate(self, payload: dict):
        self.payloads.append(dict(payload))
        return {"accepted": True}


def _custom(world, **overrides):
    values = {
        "profile_id": CUSTOM,
        "name": "Eight-user test",
        "context_per_slot": 128000,
        "slots": 8,
        "kv_cache_type": "q8_0",
        "batch_size": 512,
        "ubatch_size": 128,
        "flash_attention": "auto",
        "optimization_preset_id": "balanced",
        "thermal_policy": "standard",
        "idle_policy": "KEEP_LOADED",
        "stop_after_minutes": None,
    }
    values.update(overrides)
    with world.units.begin() as conn:
        return WorkloadProfileRepository(conn).create_custom(**values)


def _command_service(world, activation):
    return WorkloadProfileCommandService(
        world.units,
        query=world.profile_query,
        activation=activation,
        id_provider=lambda: "b" * 32,
    )


def test_builtin_preview_is_pure_bounded_and_accounts_for_all_slots(world):
    with world.units.read() as conn:
        before = "\n".join(conn.iterdump())

    previews = {
        name: world.profile_query.preview(f"builtin-{name}")
        for name in ("interactive", "long-context", "shared", "cool", "throughput")
    }

    with world.units.read() as conn:
        after = "\n".join(conn.iterdump())
    assert after == before
    assert previews["interactive"]["slots"] == 1
    assert previews["shared"]["slots"] == 2
    assert previews["shared"]["total_context"] == (
        previews["shared"]["context_per_slot"] * 2
    )
    assert previews["long-context"]["fit_verdict"] in {"FITS", "TIGHT"}
    assert previews["long-context"]["context_per_slot"] <= 128000
    assert previews["cool"]["optimization_preset_id"] == "cool-quiet"
    assert all(item["model_verification"] == "VERIFIED" for item in previews.values())
    assert all(item["ready_to_apply"] for item in previews.values())

    compared = world.profile_query.compare(
        ("builtin-interactive", "builtin-shared", "builtin-cool")
    )
    assert len(compared) == 3
    with pytest.raises(WorkloadProfileCommandError) as too_many:
        world.profile_query.compare(tuple(previews))
    assert too_many.value.code == "PROFILE_COMPARE_BOUNDS"


def test_preview_refuses_no_fit_unverified_thermal_and_recovery(world):
    _custom(world)
    no_fit = world.profile_query.preview(CUSTOM, model_alias=world.model_c.id)
    assert no_fit["fit_verdict"] == "NO-FIT"
    assert no_fit["refusal_code"] == "FIT_NO_FIT"
    assert no_fit["ready_to_apply"] is False

    with world.units.begin() as conn:
        conn.execute(
            "UPDATE model_artifacts SET trust_state = 'UNVERIFIED' "
            "WHERE catalog_id = ?",
            (world.model_a.id,),
        )
    unverified = world.profile_query.preview("builtin-interactive")
    assert unverified["refusal_code"] == "MODEL_UNVERIFIED"

    with world.units.begin() as conn:
        conn.execute(
            "UPDATE model_artifacts SET trust_state = 'VERIFIED' "
            "WHERE catalog_id = ?",
            (world.model_a.id,),
        )
        ThermalStateRepository(conn).set("stopped", None)
    thermal = world.profile_query.preview("builtin-interactive")
    assert thermal["refusal_code"] == "THERMAL_LATCH_STOPPED"

    with world.units.begin() as conn:
        ThermalStateRepository(conn).set("nominal", None)
        SettingsRepository(conn).set_many(
            {"recovery_required": {"code": "candidate-uncertain"}}
        )
    recovery = world.profile_query.preview("builtin-interactive")
    assert recovery["refusal_code"] == "RECOVERY_REQUIRED"


def test_apply_requires_exact_preview_revision_fingerprint_and_tight_confirmation(world):
    _custom(world)
    preview = world.profile_query.preview(CUSTOM)
    assert preview["fit_verdict"] == "TIGHT"
    assert preview["tight_confirmation_required"] is True
    activation = RecordingActivation()
    commands = _command_service(world, activation)

    with pytest.raises(WorkloadProfileCommandError) as unconfirmed:
        commands.apply(
            CUSTOM,
            expected_profile_revision=preview["profile_revision"],
            preview_fingerprint=preview["profile_fingerprint"],
        )
    assert unconfirmed.value.code == "TIGHT_CONFIRMATION_REQUIRED"
    assert not activation.payloads

    result = commands.apply(
        CUSTOM,
        expected_profile_revision=preview["profile_revision"],
        preview_fingerprint=preview["profile_fingerprint"],
        accept_tight=True,
    )
    assert result == {"accepted": True}
    binding = decode_profile_binding(activation.payloads[-1]["profile_id"])
    assert binding == ProfileBinding(
        CUSTOM,
        preview["profile_revision"],
        preview["profile_fingerprint"],
        tight_confirmed=True,
    )

    with world.units.begin() as conn:
        WorkloadProfileRepository(conn).replace_custom(
            CUSTOM,
            expected_revision=1,
            name="Edited after preview",
            context_per_slot=64000,
            slots=4,
            kv_cache_type="q8_0",
            batch_size=512,
            ubatch_size=128,
            flash_attention="auto",
            optimization_preset_id="balanced",
            thermal_policy="standard",
            idle_policy="KEEP_LOADED",
            stop_after_minutes=None,
        )
    with pytest.raises(WorkloadProfileCommandError) as stale:
        commands.apply(
            CUSTOM,
            expected_profile_revision=1,
            preview_fingerprint=preview["profile_fingerprint"],
            accept_tight=True,
        )
    assert stale.value.code == "PROFILE_REVISION_CONFLICT"


def test_adapter_revalidates_confirmation_and_checkpoints_exact_profile(world):
    _custom(world)
    preview = world.profile_query.preview(CUSTOM)
    request = ModelActivateRequestV1(
        model_alias=world.model_a.id,
        context_per_slot=preview["context_per_slot"],
        parallel_slots=preview["slots"],
        profile_id=preview["profile_binding"],
        expected_runtime_revision=preview["runtime_revision"],
    )
    with pytest.raises(ArtifactRejected) as unconfirmed:
        world.adapter.resolve_candidate(request)
    assert unconfirmed.value.code == "TIGHT_CONFIRMATION_REQUIRED"

    confirmed = replace(
        request,
        profile_id=ProfileBinding(
            CUSTOM,
            preview["profile_revision"],
            preview["profile_fingerprint"],
            tight_confirmed=True,
        ).encode(),
    )
    candidate = world.adapter.resolve_candidate(confirmed)
    with world.units.begin() as conn:
        WorkloadProfileRepository(conn).replace_custom(
            CUSTOM,
            expected_revision=1,
            name="Changed after resolve",
            context_per_slot=4096,
            slots=1,
            kv_cache_type="q4_0",
            batch_size=256,
            ubatch_size=128,
            flash_attention="off",
            optimization_preset_id="cool-quiet",
            thermal_policy="cool",
            idle_policy="STOP_ON_DESKTOP",
            stop_after_minutes=None,
        )
    evidence = world.adapter.commit_candidate(confirmed, candidate, "effect-profile")
    current = world.runtime.current()
    assert evidence.profile_revision == 1
    assert current["profile_id"] == CUSTOM
    assert current["profile_revision"] == 1
    assert current["profile_fingerprint"] == preview["profile_fingerprint"]
    assert current["context"] == 128000
    assert current["slots"] == 8


def test_full_profile_activation_promotes_identity_and_failure_restores_it(world):
    enqueue = EnqueueService(
        world.units,
        world.registry,
        clock=lambda: "2026-08-29T19:00:00Z",
        uuid_factory=lambda: "op-profile-success",
    )
    activation = ActivationCommandService(
        units=world.units,
        enqueue=enqueue,
        engine_factory=lambda: _engine(world),
    )
    commands = _command_service(world, activation)
    preview = world.profile_query.preview("builtin-interactive")
    outcome = commands.apply(
        "builtin-interactive",
        expected_profile_revision=preview["profile_revision"],
        preview_fingerprint=preview["profile_fingerprint"],
    )
    assert outcome.status == "SUCCEEDED"
    current = world.runtime.current()
    known_good = world.runtime.known_good()
    for row in (current, known_good):
        assert row["profile_id"] == "builtin-interactive"
        assert row["profile_revision"] == 1
        assert row["profile_fingerprint"] == preview["profile_fingerprint"]

    record = _enqueue(world, world.model_b.id, operation_id="op-profile-rollback")
    world.server.health_override = {
        "healthy": True,
        "model_id": world.model_a.id,
        "n_ctx": current["context"] * current["slots"],
        "parallel_slots": current["slots"],
    }
    failed = _engine(world, worker_id="worker-b").execute_one(record.id)
    assert failed.reason_code == "FAILED_ROLLED_BACK"
    restored = world.runtime.current()
    assert restored["model_alias"] == world.model_a.id
    assert restored["profile_id"] == "builtin-interactive"
    assert restored["profile_revision"] == 1
    assert restored["profile_fingerprint"] == preview["profile_fingerprint"]


def test_profiles_cli_parser_and_read_only_dispatch(tmp_path, monkeypatch, capsys):
    parse = entry._parser().parse_args
    assert parse(("profiles", "list")).profile_action == "list"
    assert parse(("profiles", "show", "builtin-cool")).profile_id == "builtin-cool"
    preview_args = parse((
        "profiles", "preview", "builtin-interactive", "builtin-shared",
        "--model", "lfm25-26b",
    ))
    assert preview_args.profile_id == ["builtin-interactive", "builtin-shared"]
    create_args = parse((
        "profiles", "create", "--name", "Team", "--ctx", "8192",
        "--slots", "3", "--idle", "STOP_AFTER", "--stop-after", "30",
    ))
    assert create_args.context_per_slot == 8192 and create_args.slots == 3
    assert parse((
        "profiles", "apply", "builtin-interactive", "--revision", "1",
        "--fingerprint", "f" * 64,
    )).fingerprint == "f" * 64

    query = SimpleNamespace(
        list=lambda **_kwargs: ({"profile_id": "builtin-interactive"},),
        show=lambda profile_id: {"profile_id": profile_id},
        preview=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("comparison should use the bounded compare API")
        ),
        compare=lambda profile_ids, **_kwargs: tuple(
            {"profile_id": item} for item in profile_ids
        ),
    )
    application = SimpleNamespace(
        operational=True,
        read_model=lambda: {
            "disclaimer_ack": True,
            "logs_dir": str(tmp_path / "logs"),
        },
        workload_profiles=query,
        workload_profile_commands=SimpleNamespace(),
    )
    monkeypatch.setattr(
        Application, "compose", classmethod(lambda cls, *args, **kwargs: application)
    )
    assert entry.cli((
        "profiles", "preview", "builtin-interactive", "builtin-shared"
    )) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["profile_id"] for item in payload] == [
        "builtin-interactive", "builtin-shared"
    ]
