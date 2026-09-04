import json
import stat
import subprocess
from pathlib import Path

import pytest

from bc250_llm_mode.openwebui_runtime import (
    OPENWEBUI_ARCHITECTURE,
    OPENWEBUI_CONTAINER,
    OPENWEBUI_DATA_DESTINATION,
    OPENWEBUI_DATA_VOLUME,
    OPENWEBUI_IMAGE,
    OPENWEBUI_IMAGE_DIGEST,
    OPENWEBUI_LEGACY_CONTAINER,
    OPENWEBUI_LEGACY_DATA_BIND,
    OPENWEBUI_PROVIDER_ADAPTER_VERSION,
    OPENWEBUI_RECEIPT_FILENAME,
    OpenWebUIContainerSpec,
    OpenWebUIProviderAdapter,
    OpenWebUIRuntimeError,
    OpenWebUISecretService,
    OpenWebUITransaction,
    plan_provider_config,
    validate_openwebui_command,
    verify_private_secret_file,
)
from bc250_llm_mode.openwebui import _expected_model
from bc250_llm_mode.paths import AppPaths


def _secret(path: Path, value: bytes = b"x" * 32) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def _spec(tmp_path: Path, *, data_source: str = OPENWEBUI_DATA_VOLUME):
    return OpenWebUIContainerSpec(
        name="bc250-openwebui-candidate-0123456789ab",
        data_source=data_source,
        secret_key_file=str(_secret(tmp_path / "secret")),
        client_credential_file=str(_secret(tmp_path / "client")),
    )


def test_expected_model_uses_public_alias_for_local_installation():
    assert _expected_model({
        "current_model": "local-a3515b591cfc",
        "installed_models": [{
            "id": "local-a3515b591cfc",
            "display_name": "LFM2.5-2.6B-Q5_K_M",
        }],
    }) == "LFM2.5-2.6B-Q5_K_M"


def test_spec_is_pinned_loopback_bounded_and_secret_free(tmp_path):
    command = _spec(tmp_path).create_command()
    joined = " ".join(command)
    assert command[-1] == OPENWEBUI_IMAGE
    assert f"fsize={1_073_741_824}:{1_073_741_824}" in command
    assert command[command.index("--publish") + 1] == "127.0.0.1:3000:8080"
    assert command[command.index("--network") + 1] == "bc250-openwebui"
    assert "--read-only" in command
    assert command[command.index("--restart") + 1] == "no"
    assert "OPENAI_API_KEY" not in joined
    assert "OPENAI_API_KEY_FROM_FILE" not in joined
    assert "sk-" not in joined
    assert "9071" not in joined


def test_legacy_bind_and_secret_binds_use_private_selinux_relabel(tmp_path):
    command = _spec(tmp_path, data_source=OPENWEBUI_LEGACY_DATA_BIND).create_command()
    volumes = [
        command[index + 1]
        for index, value in enumerate(command[:-1]) if value == "--volume"
    ]
    assert f"{OPENWEBUI_LEGACY_DATA_BIND}:{OPENWEBUI_DATA_DESTINATION}:Z" in volumes
    assert sum(value.endswith(":ro,Z") for value in volumes) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__(value.index("bc250-openwebui"), "host"),
        lambda value: value.__setitem__(value.index("127.0.0.1:3000:8080"), "0.0.0.0:3000:8080"),
        lambda value: value.insert(-1, "--privileged"),
        lambda value: value.extend(["--security-opt", "seccomp=unconfined"]),
    ],
)
def test_spec_validator_rejects_unsafe_drift(tmp_path, mutation):
    command = _spec(tmp_path).create_command()
    mutation(command)
    with pytest.raises(ValueError):
        validate_openwebui_command(command)


def test_secret_service_is_atomic_private_and_stable(tmp_path):
    service = OpenWebUISecretService(AppPaths.temporary(tmp_path))
    first = service.ensure()
    value = first.read_bytes()
    second = service.ensure()
    assert first == second
    assert second.read_bytes() == value
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    assert len(value) == 48


def test_secret_validation_rejects_symlink_and_wrong_mode(tmp_path):
    source = _secret(tmp_path / "source")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(OpenWebUIRuntimeError, match="regular private"):
        verify_private_secret_file(link, label="Client credential")
    source.chmod(0o644)
    with pytest.raises(OpenWebUIRuntimeError, match="0600"):
        verify_private_secret_file(source, label="Client credential")


class StatefulRunner:
    def __init__(self, *, existing: str | None = None, running: bool = False,
                 data_source: str = OPENWEBUI_DATA_VOLUME, stop_rc: int = 0):
        self.containers = {}
        if existing:
            self.containers[existing] = "running" if running else "exited"
        self.data_source = data_source
        self.stop_rc = stop_rc
        self.commands = []
        self.kwargs = []

    def _result(self, command, returncode=0, stdout=""):
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    def emit(self, _message):
        return None

    def run(self, command, **kwargs):
        command = list(command)
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if command[:3] == ["podman", "container", "exists"]:
            return self._result(command, 0 if command[3] in self.containers else 1)
        if command[:3] == ["podman", "network", "exists"]:
            return self._result(command, 0)
        if command[:3] == ["podman", "image", "inspect"]:
            if command[-1] == "{{.Architecture}}":
                return self._result(command, 0, OPENWEBUI_ARCHITECTURE + "\n")
            return self._result(command, 0, json.dumps([OPENWEBUI_IMAGE]) + "\n")
        if command[:3] == ["podman", "inspect", "--format"]:
            container = command[-1]
            if command[3] == "{{.State.Status}}":
                value = self.containers.get(container)
                return self._result(command, 0 if value else 1, (value or "") + "\n")
            if command[3] == "{{json .Mounts}}":
                if self.data_source == OPENWEBUI_DATA_VOLUME:
                    mount = {"Type": "volume", "Name": self.data_source,
                             "Destination": OPENWEBUI_DATA_DESTINATION}
                else:
                    mount = {"Type": "bind", "Source": self.data_source,
                             "Destination": OPENWEBUI_DATA_DESTINATION}
                return self._result(command, 0, json.dumps([mount]) + "\n")
        if command[:2] == ["podman", "pull"]:
            return self._result(command)
        if command[:2] == ["podman", "create"]:
            name = command[command.index("--name") + 1]
            self.containers[name] = "created"
            return self._result(command, 0, name + "\n")
        if command[:2] == ["podman", "stop"]:
            self.containers[command[-1]] = "exited"
            return self._result(command, self.stop_rc)
        if command[:2] == ["podman", "start"]:
            self.containers[command[-1]] = "running"
            return self._result(command)
        if command[:2] == ["podman", "restart"]:
            self.containers[command[-1]] = "running"
            return self._result(command)
        if command[:2] == ["podman", "rename"]:
            self.containers[command[3]] = self.containers.pop(command[2])
            return self._result(command)
        if command[:2] == ["podman", "rm"]:
            self.containers.pop(command[-1], None)
            return self._result(command)
        return self._result(command)


class Provider:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def reconcile(self, _runner, _container):
        self.calls.append("reconcile")
        if self.fail_at == "reconcile":
            raise OpenWebUIRuntimeError("provider stale")
        return {"adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
                "credential_match": True}

    def verify_model(self, _runner, _container, model):
        self.calls.append("model")
        if self.fail_at == "model":
            raise OpenWebUIRuntimeError("model absent")
        return {"expected_model_visible": model == "qwen38-9b"}

    def verify_stream(self, _runner, _container, _model):
        self.calls.append("stream")
        if self.fail_at == "stream":
            raise OpenWebUIRuntimeError("stream failed")
        return {"stream_ready": True}


def _transaction(tmp_path, runner, provider):
    paths = AppPaths.temporary(tmp_path)
    credential = _secret(paths.app_dir / "credentials/openwebui")
    return OpenWebUITransaction(
        paths=paths,
        runner=runner,
        provider=provider,
        http_probe=lambda: None,
        transaction_id="0123456789ab",
        sleeper=lambda _seconds: None,
    ), credential


def test_transaction_keeps_old_until_candidate_fully_verifies(tmp_path):
    runner = StatefulRunner(existing=OPENWEBUI_LEGACY_CONTAINER, running=True,
                            data_source=OPENWEBUI_LEGACY_DATA_BIND, stop_rc=125)
    provider = Provider()
    transaction, credential = _transaction(tmp_path, runner, provider)
    result = transaction.converge(
        client_credential_file=credential, expected_model="qwen38-9b")
    assert result.provider_ready and result.expected_model_visible and result.stream_ready
    assert runner.containers == {OPENWEBUI_CONTAINER: "running"}
    assert provider.calls == ["reconcile", "model", "stream"]
    create = next(command for command in runner.commands if command[:2] == ["podman", "create"])
    rename_old = runner.commands.index([
        "podman", "rename", OPENWEBUI_LEGACY_CONTAINER,
        "bc250-openwebui-rollback-0123456789ab",
    ])
    rename_new = runner.commands.index([
        "podman", "rename", "bc250-openwebui-candidate-0123456789ab",
        OPENWEBUI_CONTAINER,
    ])
    remove_old = runner.commands.index([
        "podman", "rm", "bc250-openwebui-rollback-0123456789ab",
    ])
    assert runner.commands.index(create) < rename_old < rename_new < remove_old
    assert not any("--volumes" in command or "-v" in command[:2]
                   for command in runner.commands if command[:2] == ["podman", "rm"])
    receipt = json.loads(
        (AppPaths.temporary(tmp_path).app_dir / OPENWEBUI_RECEIPT_FILENAME)
        .read_text(encoding="utf-8"))
    assert receipt["expected_model"] == "qwen38-9b"
    assert receipt["provider_ready"] is True
    assert receipt["stream_ready"] is True
    assert "secret" not in json.dumps(receipt).lower()


@pytest.mark.parametrize("failure", ["reconcile", "model", "stream"])
def test_transaction_restores_old_name_state_and_data_on_verification_failure(tmp_path, failure):
    runner = StatefulRunner(existing=OPENWEBUI_CONTAINER, running=True)
    transaction, credential = _transaction(tmp_path, runner, Provider(failure))
    with pytest.raises(OpenWebUIRuntimeError):
        transaction.converge(
            client_credential_file=credential, expected_model="qwen38-9b")
    assert runner.containers == {OPENWEBUI_CONTAINER: "running"}
    assert not any("--volumes" in command for command in runner.commands)


def test_previously_stopped_container_is_verified_then_left_stopped(tmp_path):
    runner = StatefulRunner(existing=OPENWEBUI_CONTAINER, running=False)
    transaction, credential = _transaction(tmp_path, runner, Provider())
    result = transaction.converge(
        client_credential_file=credential, expected_model="qwen38-9b")
    assert result.prior_running is False
    assert runner.containers == {OPENWEBUI_CONTAINER: "exited"}


def test_unknown_data_mount_refuses_before_pull_or_stop(tmp_path):
    runner = StatefulRunner(existing=OPENWEBUI_CONTAINER, running=True,
                            data_source="/host/unknown")
    transaction, credential = _transaction(tmp_path, runner, Provider())
    with pytest.raises(OpenWebUIRuntimeError, match="unrecognized"):
        transaction.converge(
            client_credential_file=credential, expected_model="qwen38-9b")
    assert ["podman", "pull", OPENWEBUI_IMAGE] not in runner.commands
    assert not any(command[:2] == ["podman", "stop"] for command in runner.commands)


class ProviderRunner:
    def __init__(self):
        self.command = None
        self.kwargs = None

    def run(self, command, **kwargs):
        self.command = list(command)
        self.kwargs = kwargs
        return subprocess.CompletedProcess(
            command, 0,
            json.dumps({
                "adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
                "action": "updated", "provider_count": 3,
                "provider_index": 2, "credential_match": True,
            }), "",
        )


def test_provider_adapter_sends_fixed_secret_free_argv_and_suppresses_output():
    runner = ProviderRunner()
    result = OpenWebUIProviderAdapter().reconcile(runner, OPENWEBUI_CONTAINER)
    assert result["provider_count"] == 3
    assert runner.command == [
        "podman", "exec", "-i", OPENWEBUI_CONTAINER, "python", "-",
    ]
    assert runner.kwargs["emit_output"] is False
    assert "openai.api_base_urls" in runner.kwargs["input_text"]
    assert "Config.upsert" in runner.kwargs["input_text"]
    assert "DELETE" not in runner.kwargs["input_text"].upper()
    assert "UPDATE CONFIG" not in runner.kwargs["input_text"].upper()
    assert "Bearer " not in " ".join(runner.command)


def test_provider_plan_preserves_unrelated_providers_and_updates_only_owned_slot():
    current = {
        "openai.enable": False,
        "openai.api_base_urls": ["https://one.example/v1", "http://host.containers.internal:9071/v1"],
        "openai.api_keys": ["one-secret", "stale-secret"],
        "openai.api_configs": {
            "0": {"enable": True, "prefix_id": "one", "custom": "keep"},
            "1": {"custom": "owned-keep"},
            "https://outside.example/v1": {"opaque": [1, 2, 3]},
        },
    }
    updates, receipt = plan_provider_config(current, credential="new-secret")
    assert updates["openai.api_base_urls"] == current["openai.api_base_urls"]
    assert updates["openai.api_keys"] == ["one-secret", "new-secret"]
    assert updates["openai.api_configs"]["0"] == current["openai.api_configs"]["0"]
    assert updates["openai.api_configs"]["https://outside.example/v1"] == {
        "opaque": [1, 2, 3]}
    assert updates["openai.api_configs"]["1"]["custom"] == "owned-keep"
    assert receipt == {
        "adapter": OPENWEBUI_PROVIDER_ADAPTER_VERSION,
        "action": "updated", "provider_index": 1,
        "provider_count": 2, "credential_match": True,
    }
    assert "new-secret" not in json.dumps(receipt)


def test_provider_plan_appends_without_reindexing_and_refuses_duplicate_identity():
    updates, receipt = plan_provider_config(
        {
            "openai.api_base_urls": ["https://one.example/v1"],
            "openai.api_keys": ["one-secret"],
            "openai.api_configs": {"0": {"custom": "keep"}},
        }, credential="new-secret")
    assert updates["openai.api_base_urls"] == [
        "https://one.example/v1", "http://host.containers.internal:9071/v1"]
    assert updates["openai.api_keys"] == ["one-secret", "new-secret"]
    assert updates["openai.api_configs"]["0"] == {"custom": "keep"}
    assert receipt["action"] == "installed"

    with pytest.raises(OpenWebUIRuntimeError, match="ambiguous"):
        plan_provider_config({
            "openai.api_base_urls": [
                "http://host.containers.internal:9071/v1",
                "http://host.containers.internal:9071/v1/",
            ],
        }, credential="new-secret")


def test_release_identity_constants_match_pinned_amd64_manifest():
    assert OPENWEBUI_IMAGE.endswith(OPENWEBUI_IMAGE_DIGEST)
    assert OPENWEBUI_ARCHITECTURE == "amd64"
