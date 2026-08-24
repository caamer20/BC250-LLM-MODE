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


def test_clean_wheel_smoke_includes_operations(tmp_path):
    """U0.5 clean-wheel gate: build a wheel, install it WITHOUT the source
    root on sys.path, then import composition/operations/adapters,
    initialize a temporary schema, register MODEL_ACTIVATE v1, and execute
    a no-host operation path end to end."""
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
