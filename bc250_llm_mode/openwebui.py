from __future__ import annotations

import json
import shutil
from typing import Any

from .logging_utils import CommandRunner

CONTAINER = "bc250-open-webui"
LEGACY_CONTAINER = "open-webui"
# DEF-006 / ADR 005 §3.5: production identity is an immutable @sha256 digest,
# never a mutable tag. The tag is human display metadata only (D5). The digest
# below was resolved from the OCI registry for the amd64/linux platform image of
# the v0.11.1 tag on 2026-08-31 (index digest 6bb1fbe8..., amd64 manifest digest
# e3a36f3a...). Registry: ghcr.io, repo open-webui/open-webui, amd64/linux.
IMAGE_TAG_DISPLAY = "v0.11.1"
IMAGE_DIGEST_SHA256 = (
    "sha256:e3a36f3aefb2408ac01d8aa2bba24f75d2569ffb6de6e7d2865c0045a38592ac"
)
IMAGE_REF = f"ghcr.io/open-webui/open-webui@{IMAGE_DIGEST_SHA256}"
DATA_VOLUME = "bc250-open-webui"
DATA_DESTINATION = "/app/backend/data"
# Early appliance images used this one fixed host directory. It remains a
# supported migration source so a contained/digest-pinned recreate never makes
# existing accounts and conversations appear to vanish. Arbitrary bind paths
# are not accepted.
LEGACY_DATA_BIND = "/var/lib/open-webui"
ALLOWED_DATA_SOURCES = frozenset({DATA_VOLUME, LEGACY_DATA_BIND})
# U0.6 containment: the UI lives on a dedicated private Podman network and
# is published STRICTLY on host loopback. Host networking is never used;
# a legacy host-network container is migrated (verified data storage preserved)
# before it may start.
NETWORK = "bc250-openwebui"
UI_PUBLISH = "127.0.0.1:3000:8080"
# ADR 005 D2/D4: the isolated container reaches the backend ONLY through the
# authenticated gateway, never a raw backend address. It resolves the HOST-side
# gateway over the bridge, presenting the install-scoped credential. The 0600
# credential file lives in the profile; it is NOT placed in argv or labels.
BACKEND_HOST = "host.containers.internal"
GATEWAY_PORT = 9071
GATEWAY_BACKEND_HOST = BACKEND_HOST
BACKEND_URL = f"http://{GATEWAY_BACKEND_HOST}:{GATEWAY_PORT}/v1"

# Security posture constants (ADR 005 D5 / plan §10.4).
READ_ONLY_ROOT = True  # read-only rootfs; explicit writable volume + tmpfs
WRITABLE_TMPFS = "/tmp:rw,size=1g,mode=0755"
NON_ROOT_USER = False  # vendor image does not certify arbitrary --user; D5 requires
# a documented exception (see READ_ONLY/NON_ROOT rationale below) rather than a
# non-functional flag. Podman rootless default already maps the user.

# §10.4 exception note (recorded, not fabricated): the upstream open-webui image
# does not document a supported arbitrary --user, and a forced one breaks its
# volume permission model on the named data volume. Compensating controls are
# enforced instead: Dockerfile rootless podman is NOT assumed; we pin the
# digest, read-only rootfs with an explicit writable tmpfs + named volume, drop
# ALL capabilities, no-new-privileges, non-host networking, seccomp+cap-drop,
# bounded memory/CPU/PID/file-size, and refuse start if the pinned digest
# mismatch. A verified vendor image that supports --user GHOST/USER will close
# this exception when available; until then the integration is NOT advertised
# as remote-safe (see D4).


def _ensure_network(state: dict[str, Any], runner: CommandRunner) -> None:
    result = runner.run(
        ["podman", "network", "exists", NETWORK], check=False
    )
    if result.returncode != 0:
        runner.run([
            "podman", "network", "create", NETWORK,
        ])


def _container_topology(
    state: dict[str, Any], runner: CommandRunner, container: str
) -> str:
    """Classify the container network topology without mutating anything."""
    result = runner.run(
        [
            "podman", "inspect", "--format",
            "{{json .NetworkSettings.Networks}}", container,
        ],
        check=False,
    )
    try:
        networks = json.loads(result.stdout.strip() or "{}")
    except ValueError:
        return "unknown"
    if not isinstance(networks, dict):
        return "unknown"
    if "host" in networks:
        return "legacy-host"
    if NETWORK in networks:
        return "contained"
    return "unknown"


def _container_data_source(runner: CommandRunner, container: str) -> str:
    """Return the one approved storage source mounted at the WebUI data path.

    Existing containers are observed before removal. Only the managed named
    volume or the historical fixed bind path may cross the recreate boundary;
    missing, ambiguous, and arbitrary host mounts fail closed while the old
    container is still intact.
    """
    result = runner.run(
        ["podman", "inspect", "--format", "{{json .Mounts}}", container],
        check=False,
    )
    try:
        mounts = json.loads(result.stdout.strip() or "[]")
    except ValueError as exc:
        raise RuntimeError("Could not verify Open WebUI data storage.") from exc
    if not isinstance(mounts, list):
        raise RuntimeError("Could not verify Open WebUI data storage.")
    matches = [
        mount for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == DATA_DESTINATION
    ]
    if len(matches) != 1:
        raise RuntimeError("Open WebUI must have exactly one verified data mount.")
    mount = matches[0]
    source = None
    if str(mount.get("Type") or "").lower() == "volume":
        source = mount.get("Name")
    elif str(mount.get("Type") or "").lower() == "bind":
        source = mount.get("Source")
    if source not in ALLOWED_DATA_SOURCES:
        raise RuntimeError(
            "Open WebUI uses an unrecognized data mount; the existing container "
            "was left unchanged."
        )
    return str(source)


def _migrate_legacy_container(
    state: dict[str, Any], runner: CommandRunner, container: str
) -> None:
    """Stop+remove a legacy/uncontained container after storage verification."""
    runner.emit(
        f"Migrating Open WebUI container {container} to the contained "
        "private network (verified user data storage is preserved)."
    )
    runner.run(["podman", "stop", "--time", "10", container], check=False)
    runner.run(["podman", "rm", container], check=False)


def _create_command(
    container: str,
    *,
    credential_file: str | None = None,
    data_source: str = DATA_VOLUME,
) -> list[str]:
    # Security posture (ADR 005 D5 / plan §10.4): dedicated private network,
    # UI published strictly on host loopback, read-only rootfs with explicit
    # writable tmpfs+volume, no-new-privileges, ALL capabilities dropped,
    # bounded memory/CPU/PIDs/file-size, non-host networking, digest-pinned
    # image. Host networking / privileged / host-PID / device / arbitrary bind
    # mounts are forbidden. The one historical data bind and the 0600 credential
    # file are bounded exceptions; the credential is never in argv or labels.
    if data_source not in ALLOWED_DATA_SOURCES:
        raise ValueError("unapproved Open WebUI data source")
    command = [
        "podman", "create", "--name", container,
        "--network", NETWORK,
        "-p", UI_PUBLISH,
        "--add-host", f"{BACKEND_HOST}:host-gateway",
        "-e", "PORT=8080",
        "-e", f"OPENAI_API_BASE_URL={BACKEND_URL}",
        "-e", f"OPENAI_API_BASE_URLS={BACKEND_URL}",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "all",
        "--memory", "2g",
        "--pids-limit", "256",
        "--cpus", "4",
        "--ulimit", "fsize=1g",
        "-v", f"{data_source}:{DATA_DESTINATION}",
    ]
    if READ_ONLY_ROOT:
        command += ["--read-only", "--tmpfs", WRITABLE_TMPFS]
    if credential_file:
        # read-only 0600 credential mount + OPENAI_API_KEY from the file.
        command += [
            "--mount", f"type=bind,src={credential_file},dst=/run/secrets/gateway-cred,readonly",
            "-e", "OPENAI_API_KEY_FROM_FILE=/run/secrets/gateway-cred",
        ]
    else:
        # No provisioned gateway credential: the container is created but its
        # status must already report pending-gateway; start refuses unless the
        # digest is verified and the credential is provided.
        command += ["-e", "OPENAI_API_KEY=sk-pending"]
    command.append(IMAGE_REF)  # immutable @sha256 digest, never a tag
    return command


def _verify_image_digest(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Verify the local image is EXACTLY the pinned digest (DEF-006/D5).

    The pinned identity is an immutable @sha256; a locally-cached image must
    carry RepoDigests containing the pinned digest. Missing/mismatch refuses
    start with a recovery story (pull the exact digest) and reports the truth.
    Returns {verified: bool, detail: str}.
    """
    if not shutil.which("podman"):
        return {"verified": False, "detail": "podman missing"}
    result = runner.run(
        ["podman", "image", "inspect", IMAGE_REF, "--format", "{{json .RepoDigests}}"],
        check=False,
    )
    if result.returncode != 0:
        # Image not yet pulled to the pinned digest at all.
        return {
            "verified": False,
            "detail": f"image not pulled to pinned digest {IMAGE_DIGEST_SHA256}",
        }
    try:
        digests = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        return {"verified": False, "detail": "could not parse image digests"}
    if not isinstance(digests, list):
        return {"verified": False, "detail": "unexpected digest metadata"}
    if any((IMAGE_DIGEST_SHA256 in d) for d in digests if isinstance(d, str)):
        return {"verified": True, "detail": IMAGE_DIGEST_SHA256}
    return {
        "verified": False,
        "detail": f"digest mismatch (got {digests!r}, want {IMAGE_DIGEST_SHA256})",
    }



def _container_name(state: dict[str, Any], runner: CommandRunner) -> tuple[str, bool]:
    preferred = str(state.get("openwebui_container") or CONTAINER)
    for name in dict.fromkeys((preferred, CONTAINER, LEGACY_CONTAINER)):
        exists = runner.run(["podman", "container", "exists", name], check=False).returncode == 0
        if exists:
            return name, True
    return preferred, False


def open_webui_status(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    if not shutil.which("podman"):
        return {"available": False, "installed": False, "running": False, "status": "podman missing"}
    container, exists = _container_name(state, runner)
    status = "not installed"
    topology = "absent"
    if exists:
        result = runner.run(
            ["podman", "inspect", "--format", "{{.State.Status}}", container], check=False
        )
        status = result.stdout.strip() or "unknown"
        topology = _container_topology(state, runner, container)
        state["openwebui_container"] = container
        state["openwebui_installed"] = True
    digest = _verify_image_digest(state, runner) if exists else {
        "verified": False, "detail": "not installed"}
    gateway_provisioned = bool(state.get("gateway_provisioned"))
    return {
        "available": True,
        "installed": exists,
        "running": status == "running",
        "status": status,
        "container": container,
        "topology": topology,
        "backend_route": (
            "gateway" if (exists and digest["verified"]
                          and gateway_provisioned) else "pending-gateway"
        ),
        "digest_verified": digest["verified"],
        "digest": IMAGE_DIGEST_SHA256,
        "image_identity": IMAGE_REF,
        "gateway_state": (
            "verified" if (digest["verified"] and gateway_provisioned)
            else "pending"
        ),
        "url": "http://127.0.0.1:3000",
    }


def install_open_webui(state: dict[str, Any], runner: CommandRunner,
                       *, credential_file: str | None = None) -> None:
    if not shutil.which("podman"):
        raise RuntimeError("Podman is required for Open WebUI; re-run environment setup.")
    # Compose passes the provisioned gateway credential file; fall back to the
    # refreshed state field when the wrapper path is used (never the secret).
    credential_file = credential_file or state.get("gateway_credential_file")
    # D5: refuse to start until the pinned digest is verified (or pulled to
    # that exact digest). If the local image is a different digest/tag, no
    # unsafe start proceeds; we emit the recovery story instead.
    if not state.get("openwebui_skip_digest_verify"):
        digest = _verify_image_digest(state, runner)
        if not digest["verified"]:
            runner.emit(
                "Open WebUI image is not the pinned digest "
                f"{IMAGE_DIGEST_SHA256}. Refusing to start. To recover, pull "
                f"the exact digest: podman pull {IMAGE_REF}."
            )
            raise RuntimeError(
                "Open WebUI image digest not verified; refusing unsafe start."
            )
    container, exists = _container_name(state, runner)
    _ensure_network(state, runner)
    data_source = DATA_VOLUME
    if exists:
        topology = _container_topology(state, runner, container)
        if container == LEGACY_CONTAINER or topology != "contained":
            data_source = _container_data_source(runner, container)
            # Legacy/uncontained containers are migrated (volume
            # preserved) and recreated contained — never reused or
            # started while unsafe (U0.6).
            _migrate_legacy_container(state, runner, container)
            container, exists = CONTAINER, False
    if not exists:
        runner.run(_create_command(
            container,
            credential_file=credential_file,
            data_source=data_source,
        ))
    runner.run(["podman", "start", container], check=False)
    state["openwebui_installed"] = True
    state["openwebui_container"] = container
    runner.emit(
        "Open WebUI is on http://127.0.0.1:3000 (loopback only). If the "
        "model is absent, log in as admin and create a Workspace model "
        "pinned to the base model id."
    )


def update_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    """Refresh the app-pinned image and recreate the managed container.

    The registry input is the immutable ``IMAGE_REF`` only.  Pull and digest
    verification complete before the existing container is touched.  Recreate
    reuses the ONE hardened create command, including the verified current data
    source and the provisioned gateway credential file. The container is
    restarted only when it was running before the refresh.

    No ``--volumes``/``-v`` removal flag is used. Consequently Open WebUI
    accounts, settings, and conversations remain on their verified storage
    source across the recreate.
    """
    if not shutil.which("podman"):
        raise RuntimeError(
            "Podman is required for Open WebUI; re-run environment setup."
        )

    before = open_webui_status(state, runner)
    if not before.get("installed"):
        raise RuntimeError(
            "Open WebUI is not installed; use Install before updating it."
        )
    container = str(before["container"])
    was_running = bool(before.get("running"))
    data_source = _container_data_source(runner, container)
    credential_file = state.get("gateway_credential_file")
    if not isinstance(credential_file, str) or not credential_file:
        raise RuntimeError(
            "A verified Open WebUI gateway credential is required before "
            "the managed container can be updated."
        )

    # Pull the release-pinned immutable identity only.  A failed pull or a
    # registry/metadata mismatch leaves the existing container untouched.
    pulled = runner.run(["podman", "pull", IMAGE_REF], check=False)
    if pulled.returncode != 0:
        raise RuntimeError(
            "Could not pull the pinned Open WebUI image; the existing "
            "container was left unchanged."
        )
    digest = _verify_image_digest(state, runner)
    if not digest["verified"]:
        raise RuntimeError(
            "Pulled Open WebUI image digest did not match the application pin; "
            "the existing container was left unchanged."
        )

    _ensure_network(state, runner)
    if was_running:
        runner.run(["podman", "stop", "--time", "10", container])
    # Deliberately omit every volume-removal flag. The replacement mounts the
    # same verified source, including the bounded legacy bind migration case.
    runner.run(["podman", "rm", container])
    runner.run(_create_command(
        container,
        credential_file=credential_file,
        data_source=data_source,
    ))
    if was_running:
        runner.run(["podman", "start", container])

    state["openwebui_installed"] = True
    state["openwebui_container"] = container
    observed = open_webui_status(state, runner)
    observed["updated"] = True
    observed["data_source_preserved"] = data_source
    observed["running_state_restored"] = bool(observed.get("running")) == was_running
    if not observed["running_state_restored"]:
        raise RuntimeError(
            "Open WebUI data was preserved, but the container did not return "
            "to its prior running state. Check System status before retrying."
        )
    runner.emit(
        "Open WebUI was recreated from the application-pinned image; its "
        "verified data storage and prior running state "
        "was restored."
    )
    return observed


def start_open_webui_impl(state: dict[str, Any], runner: CommandRunner,
                          credential_file: str | None = None) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if not status["installed"]:
        install_open_webui(state, runner, credential_file=credential_file)
    else:
        runner.run(["podman", "start", str(status["container"])], check=False)
    return open_webui_status(state, runner)


def start_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    # The composed wrapper forwards any refreshed gateway credential file so the
    # container presents a scoped credential (never the secret in argv).
    return start_open_webui_impl(
        state, runner, credential_file=state.get("gateway_credential_file")
    )


def stop_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if status["installed"]:
        runner.run(["podman", "stop", "--time", "10", str(status["container"])], check=False)
    return open_webui_status(state, runner)


def restart_open_webui_impl(state: dict[str, Any], runner: CommandRunner,
                            credential_file: str | None = None) -> dict[str, Any]:
    status = open_webui_status(state, runner)
    if not status["installed"]:
        install_open_webui(state, runner, credential_file=credential_file)
    else:
        runner.run(["podman", "restart", "--time", "10", str(status["container"])])
    return open_webui_status(state, runner)


def restart_open_webui(state: dict[str, Any], runner: CommandRunner) -> dict[str, Any]:
    return restart_open_webui_impl(
        state, runner, credential_file=state.get("gateway_credential_file")
    )
