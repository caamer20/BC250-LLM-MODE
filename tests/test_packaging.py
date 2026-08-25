"""Packaging smoke checks: the installed/source artifact is complete."""

from pathlib import Path

import bc250_llm_mode


def test_pyproject_declares_entry_point_and_metadata():
    text = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'bc250-llm-mode = "bc250_llm_mode.__main__:cli"' in text
    assert "requires-python" in text
    assert 'readme = "README.md"' in text
    for dependency in ("gguf", "httpx", "prompt-toolkit", "rich"):
        assert dependency in text, f"missing declared dependency: {dependency}"


def test_public_import_surface_is_stable():
    assert bc250_llm_mode.__version__
    from bc250_llm_mode.__main__ import cli  # noqa: F401
    from bc250_llm_mode.gui import Wizard, run_gui  # noqa: F401
    from bc250_llm_mode.paths import AppPaths  # noqa: F401


def test_every_importable_production_package_is_declared():
    """U0.5: the explicit package list cannot silently rot — every
    ``bc250_llm_mode`` subpackage with an ``__init__.py`` must be declared
    in pyproject, and every declared name must exist on disk."""
    import tomllib

    root = Path(__file__).parent.parent
    actual = {
        rel.parent.relative_to(root).as_posix().replace("/", ".")
        for rel in (root / "bc250_llm_mode").rglob("__init__.py")
    }
    declared = set(
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
            "tool"
        ]["setuptools"]["packages"]
    )
    missing = actual - declared
    stale = declared - actual
    assert not missing, f"importable packages missing from pyproject: {sorted(missing)}"
    assert not stale, f"declared packages that do not exist: {sorted(stale)}"


import pytest
import sys


@pytest.mark.slow
def test_clean_wheel_smoke_includes_operations(tmp_path):
    """U0.5 clean-wheel gate: build a wheel, install it WITHOUT the source
    root on sys.path, then import composition/operations/adapters,
    initialize a temporary schema, register MODEL_ACTIVATE v1, and execute
    a no-host operation path end to end.

    Marked ``slow``: the nested wheel build exceeds interactive suite time
    budgets; the gate runs explicitly in the session verification battery
    (see AGENTS.md §Verification).
    """
    import subprocess
    import sys

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(wheel_dir), str(Path(__file__).parent.parent)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    wheels = list(wheel_dir.glob("*.whl"))
    assert wheels

    target = tmp_path / "site"
    target.mkdir()
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(target), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr[-2000:]

    smoke = tmp_path / "smoke.py"
    smoke.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(target)!r})\n"
        "import tempfile\n"
        "from pathlib import Path\n"
        "root = Path(tempfile.mkdtemp())\n"
        "# Composition-adjacent modules import WITHOUT the source tree.\n"
        "from bc250_llm_mode.db import initialize_and_close\n"
        "from bc250_llm_mode.operations.activation import (\n"
        "    build_activation_workflow,\n"
        ")\n"
        "from bc250_llm_mode.operations.engine import ExecutionEngine\n"
        "from bc250_llm_mode.operations.model import OperationState\n"
        "from bc250_llm_mode.operations.repositories import OperationRepository\n"
        "from bc250_llm_mode.operations.workflow import (\n"
        "    EffectContext, ProbeResult, StepDefinition, WorkflowDefinition,\n"
        "    WorkflowRegistry, EnqueueService,\n"
        ")\n"
        "from bc250_llm_mode.unit_of_work import UnitOfWorkFactory\n"
        "db = root / 'state.db'\n"
        "initialize_and_close(db)\n"
        "units = UnitOfWorkFactory(db)\n"
        "class R:\n"
        "    model_alias = 'x'\n"
        "def decode(payload):\n"
        "    from dataclasses import dataclass\n"
        "    @dataclass(frozen=True)\n"
        "    class Req:\n"
        "        pass\n"
        "    return Req()\n"
        "def step(**kw):\n"
        "    return StepDefinition(\n"
        "        step_key='noop', phase='prepare', sequence=1,\n"
        "        derive_input=lambda *, request, prior: {},\n"
        "        probe=lambda ctx: ProbeResult(\n"
        "            __import__('bc250_llm_mode.operations.recovery', fromlist=['RecoveryClass']).RecoveryClass.ABSENT, 'NONE'),\n"
        "        execute=lambda ctx: {}, verify=lambda ctx: {}, **kw)\n"
        "wf = WorkflowDefinition(\n"
        "    operation_type=__import__('bc250_llm_mode.operations.model', fromlist=['OperationType']).OperationType.MODEL_ACTIVATE,\n"
        "    request_version=1, recovery_policy_version=1,\n"
        "    decode_request=decode, steps=(step(),), summary=lambda r: 'noop')\n"
        "registry = WorkflowRegistry(); registry.register(wf)\n"
        "record = EnqueueService(units, registry.freeze(), clock=lambda: '2026-01-01T00:00:00Z', uuid_factory=lambda: 'op-1').enqueue(\n"
        "    operation_type='MODEL_ACTIVATE', payload={}, surface='smoke')\n"
        "out = ExecutionEngine(units, registry.freeze(), clock=lambda: '2026-01-01T00:00:00Z', uuid_factory=lambda: 'e1').execute_one(record.id)\n"
        "assert out.reason_code == 'SUCCEEDED', out\n"
        "print('SMOKE_OK')\n",
        encoding="utf-8",
    )
    run = subprocess.run(
        [sys.executable, str(smoke)], capture_output=True, text=True,
        cwd=str(tmp_path),  # repo root NOT importable
    )
    assert "SMOKE_OK" in run.stdout, (
        f"clean-wheel smoke failed:\n{run.stdout[-1500:]}\n{run.stderr[-2500:]}"
    )


def test_documented_docs_exist():
    root = Path(__file__).parent.parent
    for name in ("README.md", "ARCHITECTURE.md", "CHANGELOG.md", "AGENTS.md"):
        assert (root / name).is_file(), name


@pytest.mark.slow
def test_clean_wheel_executes_runtime_workflows_and_migration_005(tmp_path):
    """U1.2 §16.7: the installed wheel initializes schema v5, registers
    RUNTIME_UPDATE v1 / RUNTIME_ROLLBACK v1, executes a no-host happy
    update AND rollback through the shared engine, and verifies the fixed
    exchange-helper resource is present and digestable."""
    import subprocess

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(wheel_dir),
         str(Path(__file__).parent.parent)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    wheels = list(wheel_dir.glob("*.whl"))
    assert wheels
    target = tmp_path / "site2"
    target.mkdir()
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(target), str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stderr[-2000:]

    smoke = tmp_path / "smoke_runtime.py"
    smoke.write_text(_RUNTIME_SMOKE_SOURCE.replace(
        "__TARGET__", repr(str(target))
    ), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(smoke)], capture_output=True, text=True,
        cwd=str(tmp_path),
    )
    assert "RUNTIME_SMOKE_OK" in run.stdout, (
        f"runtime clean-wheel smoke failed:\n{run.stdout[-1500:]}\n"
        f"{run.stderr[-2500:]}"
    )


_RUNTIME_SMOKE_SOURCE = '''
import sys
sys.path.insert(0, __TARGET__)
import hashlib, json, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp())
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from bc250_llm_mode.operations.engine import ExecutionEngine
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import (
    build_runtime_update_workflow, build_runtime_rollback_workflow,
)
from bc250_llm_mode.operations.repositories import OperationRepository
from bc250_llm_mode.operations.workflow import (
    EnqueueService, ProbeResult, WorkflowRegistry,
)
from bc250_llm_mode.runtime_builds import (
    derive_build_id, RuntimeBuildRepository, RuntimeComponentRepository,
    RuntimeTreeRepository, RuntimeVerificationRepository,
)
from bc250_llm_mode.runtime_exchange_helper import HELPER_DIGEST, HELPER_SOURCE

assert hashlib.sha256(HELPER_SOURCE.encode()).hexdigest() == HELPER_DIGEST

db = root / "state.db"
initialize_and_close(db)  # ordered migrations include 005 (schema v5)
units = UnitOfWorkFactory(db)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def dig(data):
    return hashlib.sha256(data).hexdigest()


class MiniHost:
    # Self-contained no-host port over durable files + repositories.

    def __init__(self, base):
        self.base = base
        self._seen = set()
        self.commit = COMMIT_A
        (self.base / "managed").mkdir(parents=True, exist_ok=True)
        self._write(self.base / "service.json", {"running": False})

    def _write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, sort_keys=True))

    def _read(self, path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def active_id(self):
        rec = self._read(self.base / "active/manifest.json")
        return (rec or {}).get("build_id")

    def _classify(self, snap, target):
        act = self.active_id()
        if act == target:
            prior = self._read(self.base / "managed/prior/manifest.json")
            pid = (prior or {}).get("build_id")
            if snap.active_build_id is None or pid == snap.active_build_id:
                return ProbeResult(RecoveryClass.COMPLETE, "SWAPPED")
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "PRIOR_UNPROVEN")
        if act == snap.active_build_id:
            for tree in (self.base / "managed").glob("candidate-*"):
                rec = self._read(tree / "manifest.json")
                if rec and rec.get("build_id") == target:
                    return ProbeResult(RecoveryClass.ABSENT, "NOT_LANDED")
            return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "TARGET_UNPROVABLE")
        return ProbeResult(RecoveryClass.UNCERTAIN_MANUAL, "UNKNOWN")

    def resolve_source(self, request):
        from bc250_llm_mode.operations.runtime_lifecycle import ResolvedRuntimeSourceV1
        return ResolvedRuntimeSourceV1("b7598", self.commit, "PIN")

    def observe_source_resolution(self, request, evidence):
        return ProbeResult(RecoveryClass.COMPLETE, "SOURCE_REF_RESOLVED")

    def observe_noop(self, request):
        return False

    def preflight_build(self, request):
        from bc250_llm_mode.operations.runtime_lifecycle import BuildPreflightEvidenceV1
        return BuildPreflightEvidenceV1(True, True, 0, 1, True, True, True)

    def observe_preflight(self, request, evidence):
        return ProbeResult(RecoveryClass.COMPLETE, "PREFLIGHT_OK")

    def fetch_exact_commit(self, request, commit, pulse):
        from bc250_llm_mode.operations.runtime_lifecycle import FetchEvidenceV1
        return FetchEvidenceV1(commit, "sources/x", "CREATED", True)

    def probe_checkout(self, commit):
        return ProbeResult(RecoveryClass.COMPLETE, "CHECKOUT_PRESENT")

    def configure_build(self, request, commit, pulse):
        n = len(list((self.base / "managed").glob("candidate-*"))) + 1
        from bc250_llm_mode.operations.runtime_lifecycle import BuildEnvironmentEvidenceV1
        locator = "managed/candidate-%03d" % n
        (self.base / locator).mkdir(parents=True, exist_ok=True)
        return BuildEnvironmentEvidenceV1(
            1, dig(b"recipe"), "Ninja", [], ["llama-server"], "bounded-2",
            dig(b"i"), dig(b"d"), {"cc": dig(b"c")}, "x86_64", locator)

    def probe_build_environment(self, evidence):
        return ProbeResult(RecoveryClass.COMPLETE, "ENV_FROZEN")

    def compile_candidate(self, environment, pulse):
        binary = self.base / environment.build_dir_locator / "build/bin/llama-server"
        binary.parent.mkdir(parents=True, exist_ok=True)
        body = environment.build_dir_locator.encode()
        binary.write_bytes(body)
        from bc250_llm_mode.operations.runtime_lifecycle import CandidateBuildEvidenceV1
        return CandidateBuildEvidenceV1(environment.build_dir_locator, [{
            "path": "build/bin/llama-server", "size": len(body), "mode": "755",
            "sha256": dig(body), "version_output_digest": dig(b"v" + body)}])

    def probe_compilation(self, environment):
        p = self.base / environment.build_dir_locator / "build/bin/llama-server"
        body = p.read_bytes()
        return ProbeResult(RecoveryClass.COMPLETE, "OK", output={"binaries": [{
            "path": "build/bin/llama-server", "size": len(body), "mode": "755",
            "sha256": dig(body), "version_output_digest": dig(b"v" + body)}]})

    def smoke_and_register_candidate(self, request, commit, env, cand, opid):
        manifest = {
            "schema_version": 1, "component": "llamacpp",
            "upstream_repository": "https://github.com/ggml-org/llama.cpp",
            "requested_ref": None, "source_commit": commit,
            "source_checkout_verified": True, "recipe_version": 1,
            "recipe_digest": dig(b"recipe"), "cmake_generator": "Ninja",
            "cmake_options": [], "cmake_targets": ["llama-server"],
            "build_parallelism": {"policy": "bounded-2"},
            "container_image_id": dig(b"i"),
            "container_image_digest": dig(b"d"),
            "toolchain": {"cc": dig(b"c")}, "target_arch": "x86_64",
            "binaries": cand.binaries, "smoke_contract_version": 1,
        }
        bid, mdg = derive_build_id(manifest)
        self._write(self.base / env.build_dir_locator / "manifest.json",
                    {"build_id": bid, "manifest_digest": mdg,
                     "manifest": manifest})
        tree_id = "tree-" + opid[:24]
        with units.begin() as conn:
            RuntimeBuildRepository(conn).create_immutable(manifest=manifest)
            RuntimeVerificationRepository(conn).append(build_id=bid, kind="SMOKE",
                                                       evidence={"ok": True})
            RuntimeTreeRepository(conn).record_candidate(
                tree_id=tree_id, build_id=bid, container_profile="smoke",
                locator=env.build_dir_locator, manifest_digest=mdg,
                server_binary_digest=cand.binaries[0]["sha256"])
        from bc250_llm_mode.operations.runtime_lifecycle import SmokeEvidenceV1
        return SmokeEvidenceV1(bid, mdg, 1, True, tree_id, env.build_dir_locator)

    def observe_candidate_manifest(self, smoke):
        rec = self._read(self.base / smoke.locator / "manifest.json")
        if rec and rec.get("build_id") == smoke.build_id:
            return ProbeResult(RecoveryClass.COMPLETE, "SMOKE_OK")
        return ProbeResult(RecoveryClass.ABSENT, "MISSING")

    def capture_activation_boundary(self, request, target):
        from bc250_llm_mode.operations.runtime_lifecycle import PriorRuntimeSnapshotV1
        with units.read() as conn:
            comp = RuntimeComponentRepository(conn).current()
        act = self._read(self.base / "active/manifest.json")
        return PriorRuntimeSnapshotV1(
            service_state="STOPPED",
            active_build_id=(act or {}).get("build_id"),
            promoted_build_id=(comp or {}).get("promoted_build_id"),
            rollback_build_id=(comp or {}).get("rollback_build_id"),
            generation=(comp or {}).get("generation"))

    def verify_activation_boundary(self, request, snapshot, target):
        return None

    def exchange_active_tree(self, snap, smoke, eid, *, mode):
        import shutil
        if eid not in self._seen:
            self._seen.add(eid)
            active = self.base / "active"
            staged = self.base / smoke.locator
            prior = self.base / "managed/prior"
            if snap.active_build_id is None:
                shutil.move(str(staged), str(active))
            else:
                park = self.base / "managed/.park"
                shutil.move(str(active), str(park))
                shutil.move(str(staged), str(active))
                if prior.exists():
                    import shutil as s2
                    s2.rmtree(prior)
                shutil.move(str(park), str(prior))
        from bc250_llm_mode.operations.runtime_lifecycle import TreeExchangeEvidenceV1
        return TreeExchangeEvidenceV1("EXCHANGED", True,
                                      active_build_id_after=self.active_id() or "")

    def probe_exchange(self, snap, target, *, mode):
        return self._classify(snap, target)

    def verify_exchange(self, snap, target, *, mode):
        r = self._classify(snap, target)
        assert r.classification is RecoveryClass.COMPLETE, r.reason_code

    def publish_handoff_v2(self, snap, target, opid, *, mode):
        from bc250_llm_mode.operations.runtime_lifecycle import HandoffComponentEvidenceV1
        self._write(self.base / "handoff.json", {"component": target})
        return HandoffComponentEvidenceV1("fp", 2, target, "0" * 64, "0" * 64, opid)

    def observe_handoff_v2(self, snap, target, *, mode):
        h = self._read(self.base / "handoff.json") or {}
        if h.get("component") == target:
            return ProbeResult(RecoveryClass.COMPLETE, "HANDOFF_OK")
        return ProbeResult(RecoveryClass.ABSENT, "NO_HANDOFF")

    def restart_for_runtime_change(self, snap, target, opid, *, mode):
        from bc250_llm_mode.operations.runtime_lifecycle import ServiceRestartEvidenceV1
        self._write(self.base / "service.json", {"running": True, "build": target})
        return ServiceRestartEvidenceV1(True, False, "n1", True)

    def observe_invocation(self, snap, target, *, mode):
        svc = self._read(self.base / "service.json") or {}
        if svc.get("running") and svc.get("build") == target:
            return ProbeResult(RecoveryClass.COMPLETE, "INVOKED")
        return ProbeResult(RecoveryClass.REVERTIBLE, "INACTIVE")

    def verify_runtime_identity(self, snap, target):
        from bc250_llm_mode.operations.runtime_lifecycle import RuntimeIdentityEvidenceV1
        return RuntimeIdentityEvidenceV1(True, True, True, True, True, True, "")

    def verify_runtime_inference(self, target):
        from bc250_llm_mode.operations.runtime_lifecycle import RuntimeInferenceEvidenceV1
        return RuntimeInferenceEvidenceV1(True, 1, "sub_second")

    def promote_verified_runtime(self, snap, target, smoke, opid, *, mode):
        from bc250_llm_mode.operations.runtime_lifecycle import RuntimePromotionEvidenceV1
        with units.begin() as conn:
            c = RuntimeComponentRepository(conn)
            cur = c.current() or c.initialize()
            if mode == "update":
                out = c.promote_verified(
                    expected_generation=int(cur["generation"]),
                    expected_promoted_build_id=cur["promoted_build_id"],
                    expected_rollback_build_id=cur["rollback_build_id"],
                    promoted_build_id=target,
                    rollback_build_id=snap.promoted_build_id,
                    known_good_identity={"runtime_fingerprint": "fp",
                                         "runtime_component_identity": target})
            else:
                out = c.record_restoration(
                    expected_generation=int(cur["generation"]),
                    expected_promoted_build_id=cur["promoted_build_id"],
                    expected_rollback_build_id=cur.get("rollback_build_id"),
                    restored_promoted_build_id=target,
                    new_rollback_build_id=cur["promoted_build_id"])
        return RuntimePromotionEvidenceV1(int(out["generation"]), target,
                                          out.get("rollback_build_id"))

    def observe_promotion(self, snap, target, *, mode):
        with units.read() as conn:
            cur = RuntimeComponentRepository(conn).current()
        if cur and cur.get("promoted_build_id") == target:
            return ProbeResult(RecoveryClass.COMPLETE, "PROMOTED")
        return ProbeResult(RecoveryClass.REVERTIBLE, "NOT_APPLIED")

    def restore_prior_runtime(self, snap, rid, *, mode):
        import shutil
        if snap.active_build_id and self.active_id() != snap.active_build_id:
            prior = self.base / "managed/prior"
            park = self.base / "managed/.park2"
            shutil.move(str(self.base / "active"), str(park))
            shutil.move(str(prior), str(self.base / "active"))
            shutil.move(str(park), str(prior))
        from bc250_llm_mode.operations.runtime_lifecycle import RuntimeRestorationEvidenceV1
        return RuntimeRestorationEvidenceV1(True, ["RESTORED"], "STOPPED")

    def observe_restoration(self, snap, *, mode):
        if self.active_id() == snap.active_build_id:
            return ProbeResult(RecoveryClass.COMPLETE, "RESTORED")
        return ProbeResult(RecoveryClass.REVERTIBLE, "PENDING")

    def finalize_trees(self, snap, target, promo, exch, *, mode):
        from bc250_llm_mode.operations.runtime_lifecycle import RuntimeCleanupEvidenceV1
        return RuntimeCleanupEvidenceV1([], [], [])

    def observe_finalization(self, snap, target, *, mode):
        return ProbeResult(RecoveryClass.COMPLETE, "FINALIZED")

    def resolve_rollback_target(self, request):
        from bc250_llm_mode.operations.runtime_lifecycle import RollbackTargetEvidenceV1
        with units.read() as conn:
            cur = RuntimeComponentRepository(conn).current()
        return RollbackTargetEvidenceV1(cur["rollback_build_id"],
                                        cur["promoted_build_id"],
                                        int(cur["generation"]), "tree-rb",
                                        "managed/prior", "0" * 64, "0" * 64)

    def observe_rollback_target(self, request, evidence):
        return ProbeResult(RecoveryClass.COMPLETE, "TARGET_OK")

    def preflight_rollback(self, request, target):
        return self.preflight_build(request)

    def observe_preflight_rollback(self, request, target, evidence):
        return ProbeResult(RecoveryClass.COMPLETE, "RB_PREFLIGHT_OK")

    def smoke_rollback_target(self, target):
        from bc250_llm_mode.operations.runtime_lifecycle import SmokeEvidenceV1
        return SmokeEvidenceV1(target.target_build_id, target.manifest_digest,
                               1, True, target.target_tree_id,
                               target.target_locator)

    def observe_rollback_manifest(self, target):
        return ProbeResult(RecoveryClass.COMPLETE, "RB_MANIFEST_OK")


host = MiniHost(root / "rt")
registry = WorkflowRegistry()
registry.register(build_runtime_update_workflow(host))
registry.register(build_runtime_rollback_workflow(host))
frozen = registry.freeze()
import itertools

ids = iter("op-%03d" % i for i in range(1, 50))
effect_ids = iter("eff-%04d" % i for i in itertools.count(1))
enqueue = EnqueueService(units, frozen, clock=lambda: "2026-01-01T00:00:00Z",
                         uuid_factory=lambda: next(ids))
engine_factory = lambda: ExecutionEngine(
    units, frozen, clock=lambda: "2026-01-01T00:00:00Z",
    uuid_factory=lambda: next(effect_ids))

rec = enqueue.enqueue(operation_type="RUNTIME_UPDATE",
                      payload={"requested_by": "cli"}, surface="smoke")
engine_factory().execute_one(rec.id)
with units.begin() as conn:
    row = OperationRepository(conn).require(rec.id)
assert row.state.value == "SUCCEEDED", row.state.value
assert row.result_code == "RUNTIME_PROMOTED", row.result_code

# A second update gives us a retained prior, which rollback needs.
host.commit = COMMIT_B  # a genuinely different upstream revision
rec_b = enqueue.enqueue(operation_type="RUNTIME_UPDATE",
                        payload={"requested_by": "cli"}, surface="smoke")
engine_factory().execute_one(rec_b.id)
with units.begin() as conn:
    row_b = OperationRepository(conn).require(rec_b.id)
assert row_b.result_code == "RUNTIME_PROMOTED", row_b.result_code

with units.read() as conn:
    comp = RuntimeComponentRepository(conn).current()
rec2 = enqueue.enqueue(operation_type="RUNTIME_ROLLBACK", payload={
    "requested_by": "cli",
    "expected_active_build_id": comp["promoted_build_id"],
    "target_build_id": comp["rollback_build_id"]}, surface="smoke")
engine_factory().execute_one(rec2.id)
with units.begin() as conn:
    row2 = OperationRepository(conn).require(rec2.id)
assert row2.state.value == "SUCCEEDED", row2.state.value
assert row2.result_code == "RUNTIME_RESTORED", row2.result_code
print("RUNTIME_SMOKE_OK")
'''
