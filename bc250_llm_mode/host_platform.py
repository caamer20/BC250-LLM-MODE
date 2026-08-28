"""Capability-driven Linux host integration.

The inference appliance is intentionally distribution-neutral above this
boundary.  This module performs bounded, read-only host detection and exposes
closed package/boot/service contracts for the few operations that genuinely
depend on the host distribution.

Distribution identity is diagnostic input, not permission to execute an
arbitrary command.  Every command returned here is an argv tuple selected from
closed enums and fixed package maps.  Callers remain responsible for the
existing privilege and command-audit boundary.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

HOST_PLATFORM_SCHEMA_VERSION = 1
MAX_OS_RELEASE_BYTES = 64 * 1024


class DistroFamily(str, Enum):
    BAZZITE = "bazzite"
    ARCH = "arch"
    FEDORA = "fedora"
    DEBIAN = "debian"
    SUSE = "suse"
    UNKNOWN = "unknown"


class PackageManager(str, Enum):
    RPM_OSTREE = "rpm-ostree"
    PACMAN = "pacman"
    DNF = "dnf"
    APT = "apt-get"
    ZYPPER = "zypper"
    NONE = "none"


class BootManager(str, Enum):
    RPM_OSTREE = "rpm-ostree"
    SYSTEMD_BOOT = "systemd-boot"
    GRUB = "grub"
    LIMINE = "limine"
    REFIND = "refind"
    UNKNOWN = "unknown"


class IntegrationTier(str, Enum):
    NATIVE = "native"
    COMPATIBLE = "compatible-unqualified"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PackageInstallPlan:
    """A closed, previewable package-manager action."""

    purpose: str
    manager: PackageManager
    packages: tuple[str, ...]
    argv: tuple[str, ...]
    requires_reboot: bool
    automatic: bool
    guidance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "manager": self.manager.value,
            "packages": list(self.packages),
            "argv": list(self.argv),
            "requires_reboot": self.requires_reboot,
            "automatic": self.automatic,
            "guidance": self.guidance,
        }


@dataclass(frozen=True)
class HostPlatform:
    schema_version: int
    distro_id: str
    distro_name: str
    distro_version: str
    id_like: tuple[str, ...]
    family: DistroFamily
    package_manager: PackageManager
    boot_manager: BootManager
    service_manager: str
    gpu_tuning_backend: str
    integration_tier: IntegrationTier
    is_cachyos: bool
    is_bazzite: bool
    commands: Mapping[str, bool] = field(default_factory=dict)
    observations: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "commands", MappingProxyType(dict(self.commands)))

    @property
    def label(self) -> str:
        return self.distro_name or self.distro_id or "Unknown Linux"

    @property
    def supports_current_boot_llm_mode(self) -> bool:
        return (
            self.integration_tier is not IntegrationTier.UNSUPPORTED
            and self.service_manager == "systemd"
            and bool(self.commands.get("udevadm"))
        )

    @property
    def supports_gpu_tuning(self) -> bool:
        return self.gpu_tuning_backend == "cyan-skillfish"

    @property
    def persistent_kernel_policy(self) -> str:
        if self.package_manager is PackageManager.RPM_OSTREE:
            return "managed-rpm-ostree"
        return "observe-only"

    @property
    def desktop_units(self) -> tuple[str, ...]:
        # display-manager.service is the distribution-neutral systemd alias.
        # Never guess sddm/gdm/lightdm from the distribution name.
        return ("display-manager.service",)

    def required_host_mode_commands(self) -> tuple[str, ...]:
        base = ["systemctl", "udevadm"]
        if self.package_manager is PackageManager.RPM_OSTREE:
            base.append("rpm-ostree")
        return tuple(base)

    def tkinter_plan(self) -> PackageInstallPlan:
        manager = self.package_manager
        if manager is PackageManager.RPM_OSTREE:
            packages = ("python3-tkinter",)
            argv = ("rpm-ostree", "install", *packages)
            return PackageInstallPlan(
                "native tkinter", manager, packages, argv, True, True,
                "Stage the package, reboot, then relaunch BC250 LLM MODE.",
            )
        if manager is PackageManager.PACMAN:
            packages = ("tk",)
            argv = ("pacman", "-S", "--needed", "--noconfirm", *packages)
            return PackageInstallPlan(
                "native tkinter", manager, packages, argv, False, True,
                "CachyOS/Arch must be fully updated first; BC250 LLM MODE "
                "never runs pacman -Sy or performs a partial upgrade.",
            )
        if manager is PackageManager.DNF:
            packages = ("python3-tkinter",)
            argv = ("dnf", "install", "-y", *packages)
            return PackageInstallPlan(
                "native tkinter", manager, packages, argv, False, True,
                "Install the host tkinter package, then relaunch the app.",
            )
        if manager is PackageManager.APT:
            packages = ("python3-tk",)
            argv = ("apt-get", "install", "-y", *packages)
            return PackageInstallPlan(
                "native tkinter", manager, packages, argv, False, True,
                "Refresh and upgrade the host through its normal maintenance "
                "workflow before applying this package plan.",
            )
        if manager is PackageManager.ZYPPER:
            packages = ("python311-tk",)
            argv = ("zypper", "--non-interactive", "install", *packages)
            return PackageInstallPlan(
                "native tkinter", manager, packages, argv, False, False,
                "Verify the Python minor version matches this package before applying.",
            )
        return PackageInstallPlan(
            "native tkinter", manager, (), (), False, False,
            "No supported host package manager was detected; install tkinter "
            "for this Python interpreter manually.",
        )

    def runtime_host_plan(self) -> PackageInstallPlan:
        manager = self.package_manager
        if manager is PackageManager.PACMAN:
            packages = (
                "podman", "distrobox", "vulkan-radeon", "vulkan-tools",
            )
            return PackageInstallPlan(
                "container and Vulkan host runtime", manager, packages,
                ("pacman", "-S", "--needed", "--noconfirm", *packages),
                False, False,
                "Run a full `pacman -Syu` first, then apply this reviewed "
                "package plan. Automatic system upgrades are intentionally refused.",
            )
        if manager is PackageManager.RPM_OSTREE:
            return PackageInstallPlan(
                "container and Vulkan host runtime", manager, (), (), False,
                False,
                "Bazzite normally supplies Podman, Distrobox, and RADV in the host image.",
            )
        return PackageInstallPlan(
            "container and Vulkan host runtime", manager, (), (), False,
            False,
            "Install Podman, Distrobox, the RADV Vulkan driver, and vulkaninfo "
            "using the distribution's supported package workflow.",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "distribution": {
                "id": self.distro_id,
                "name": self.distro_name,
                "version": self.distro_version,
                "id_like": list(self.id_like),
                "family": self.family.value,
                "cachyos": self.is_cachyos,
                "bazzite": self.is_bazzite,
            },
            "integration_tier": self.integration_tier.value,
            "package_manager": self.package_manager.value,
            "boot_manager": self.boot_manager.value,
            "persistent_kernel_policy": self.persistent_kernel_policy,
            "service_manager": self.service_manager,
            "gpu_tuning_backend": self.gpu_tuning_backend,
            "current_boot_llm_mode": self.supports_current_boot_llm_mode,
            "commands": dict(sorted(self.commands.items())),
            "observations": list(self.observations),
            "blockers": list(self.blockers),
            "plans": {
                "tkinter": self.tkinter_plan().to_dict(),
                "runtime_host": self.runtime_host_plan().to_dict(),
            },
        }


def _read_os_release(path: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if len(raw) > MAX_OS_RELEASE_BYTES:
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines()[:256]:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value.replace("\\\"", "\"")
    return result


def _family_for(distro_id: str, id_like: tuple[str, ...]) -> DistroFamily:
    identities = {distro_id, *id_like}
    if distro_id == "bazzite":
        return DistroFamily.BAZZITE
    if identities & {"arch", "cachyos", "endeavouros", "garuda"}:
        return DistroFamily.ARCH
    if identities & {"fedora", "rhel", "centos"}:
        return DistroFamily.FEDORA
    if identities & {"debian", "ubuntu", "linuxmint"}:
        return DistroFamily.DEBIAN
    if identities & {"suse", "opensuse", "opensuse-tumbleweed"}:
        return DistroFamily.SUSE
    return DistroFamily.UNKNOWN


def _manager_for(
    family: DistroFamily,
    *,
    command_exists: Callable[[str], bool],
) -> PackageManager:
    candidates: tuple[PackageManager, ...]
    if family is DistroFamily.BAZZITE:
        candidates = (PackageManager.RPM_OSTREE,)
    elif family is DistroFamily.ARCH:
        candidates = (PackageManager.PACMAN,)
    elif family is DistroFamily.FEDORA:
        candidates = (PackageManager.DNF, PackageManager.RPM_OSTREE)
    elif family is DistroFamily.DEBIAN:
        candidates = (PackageManager.APT,)
    elif family is DistroFamily.SUSE:
        candidates = (PackageManager.ZYPPER,)
    else:
        candidates = (
            PackageManager.RPM_OSTREE, PackageManager.PACMAN,
            PackageManager.DNF, PackageManager.APT, PackageManager.ZYPPER,
        )
    return next(
        (candidate for candidate in candidates if command_exists(candidate.value)),
        PackageManager.NONE,
    )


_BOOT_HINTS: tuple[tuple[BootManager, str], ...] = (
    (BootManager.SYSTEMD_BOOT, "/etc/sdboot-manage.conf"),
    (BootManager.LIMINE, "/etc/default/limine"),
    (BootManager.REFIND, "/boot/refind_linux.conf"),
    (BootManager.GRUB, "/etc/default/grub"),
)


def _boot_manager_for(
    package_manager: PackageManager,
    *,
    path_exists: Callable[[str], bool],
) -> tuple[BootManager, tuple[str, ...]]:
    if package_manager is PackageManager.RPM_OSTREE:
        return BootManager.RPM_OSTREE, ()
    matches = [manager for manager, path in _BOOT_HINTS if path_exists(path)]
    if len(matches) == 1:
        return matches[0], ()
    if len(matches) > 1:
        return BootManager.UNKNOWN, (
            "multiple boot-manager configuration files detected; persistent "
            "kernel changes are disabled",
        )
    return BootManager.UNKNOWN, (
        "boot manager was not identified; persistent kernel changes are disabled",
    )


def detect_host_platform(
    *,
    os_release_path: Path | str = Path("/etc/os-release"),
    command_exists: Callable[[str], bool] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    platform_name: str | None = None,
) -> HostPlatform:
    """Detect the host without mutation or durable-state writes.

    Injected probes keep the contract deterministic in tests.  Command
    presence is recorded once at composition; mutation paths revalidate the
    commands immediately before use.
    """

    command_exists = command_exists or (lambda name: shutil.which(name) is not None)
    path_exists = path_exists or (lambda path: Path(path).exists())
    platform_name = platform_name or sys.platform
    release = _read_os_release(Path(os_release_path))
    distro_id = release.get("ID", "").strip().lower()
    id_like = tuple(
        item.strip().lower() for item in release.get("ID_LIKE", "").split()
        if item.strip()
    )
    family = _family_for(distro_id, id_like)
    manager = _manager_for(family, command_exists=command_exists)
    if (
        family is DistroFamily.FEDORA
        and path_exists("/run/ostree-booted")
        and command_exists("rpm-ostree")
    ):
        # Fedora Atomic variants can expose DNF tooling inside their image;
        # the immutable deployment marker, not command ordering, owns host
        # package mutations.
        manager = PackageManager.RPM_OSTREE
    boot_manager, boot_observations = _boot_manager_for(
        manager, path_exists=path_exists
    )
    gpu_tuning_backend = (
        "cyan-skillfish"
        if path_exists("/etc/cyan-skillfish-governor-smu/config.toml")
        else "unavailable"
    )

    command_names = (
        "systemctl", "udevadm", "podman", "distrobox", "vulkaninfo",
        "rpm-ostree", "pacman", "dnf", "apt-get", "zypper",
    )
    commands = {name: bool(command_exists(name)) for name in command_names}
    systemd = bool(commands["systemctl"] and path_exists("/run/systemd/system"))
    observations = list(boot_observations)
    blockers: list[str] = []

    if not platform_name.startswith("linux"):
        blockers.append("host operating system is not Linux")
    if not systemd:
        blockers.append("systemd is required for service and host-mode control")
    if manager is PackageManager.NONE:
        blockers.append("no supported host package manager was detected")
    if not commands["udevadm"]:
        blockers.append("udevadm is required for current-boot GPU power policy")

    is_cachyos = distro_id == "cachyos"
    is_bazzite = distro_id == "bazzite"
    if blockers:
        tier = IntegrationTier.UNSUPPORTED
    elif is_cachyos or is_bazzite:
        tier = IntegrationTier.NATIVE
    elif family in {
        DistroFamily.ARCH, DistroFamily.FEDORA,
        DistroFamily.DEBIAN, DistroFamily.SUSE,
    }:
        tier = IntegrationTier.COMPATIBLE
        observations.append(
            "host family has an implemented adapter but requires separate BC-250 qualification"
        )
    else:
        tier = IntegrationTier.UNSUPPORTED
        blockers.append("distribution family has no reviewed integration profile")

    return HostPlatform(
        schema_version=HOST_PLATFORM_SCHEMA_VERSION,
        distro_id=distro_id or "unknown",
        distro_name=release.get("PRETTY_NAME") or release.get("NAME") or "Unknown Linux",
        distro_version=release.get("VERSION_ID", ""),
        id_like=id_like,
        family=family,
        package_manager=manager,
        boot_manager=boot_manager,
        service_manager="systemd" if systemd else "unsupported",
        gpu_tuning_backend=gpu_tuning_backend,
        integration_tier=tier,
        is_cachyos=is_cachyos,
        is_bazzite=is_bazzite,
        commands=commands,
        observations=tuple(observations),
        blockers=tuple(blockers),
    )


class HostPlatformService:
    """Composed, read-only platform authority plus closed mutation plans."""

    def __init__(self, profile: HostPlatform) -> None:
        if not isinstance(profile, HostPlatform):
            raise TypeError("HostPlatformService requires a HostPlatform")
        self.profile = profile

    @classmethod
    def detect(cls, **kwargs: Any) -> "HostPlatformService":
        return cls(detect_host_platform(**kwargs))

    def status(self) -> dict[str, Any]:
        return self.profile.to_dict()

    def require_current_boot_llm_mode(self) -> None:
        if not self.profile.supports_current_boot_llm_mode:
            detail = "; ".join(self.profile.blockers) or "host profile is unsupported"
            raise RuntimeError(
                f"Current-boot LLM Mode is unavailable on {self.profile.label}: {detail}"
            )

    def persistent_kernel_cleanup(
        self,
        argument: str,
        *,
        runner: Any,
        run_root: Callable[[list[str], bool], Any],
    ) -> dict[str, Any]:
        """Remove an app-owned rpm-ostree argument or refuse to guess.

        CachyOS may use systemd-boot, GRUB, Limine, or rEFInd.  The current
        release therefore observes those managers but never edits persistent
        boot configuration.  BC250 LLM MODE does not add this argument on such
        hosts, so ordinary CachyOS installs need no persistent mutation.
        """

        profile = self.profile
        if profile.package_manager is PackageManager.RPM_OSTREE:
            staged = runner.run(["rpm-ostree", "kargs"], check=False).stdout
            if argument in str(staged).split():
                run_root(["rpm-ostree", "kargs", f"--delete={argument}"], True)
                return {"policy": "managed-rpm-ostree", "removed": True}
            return {"policy": "managed-rpm-ostree", "removed": False}
        return {
            "policy": "observe-only",
            "removed": False,
            "boot_manager": profile.boot_manager.value,
        }


def pacman_install_is_safe(runner: Any) -> tuple[bool, str]:
    """Conservative preflight for a no-refresh Pacman package install.

    The app never invokes ``pacman -Sy`` or an automatic full system upgrade.
    If the currently configured sync database reports upgrades, the operator
    must complete ``pacman -Syu`` outside the app before retrying.
    """

    result = runner.run(["pacman", "-Qu"], check=False)
    pending = str(getattr(result, "stdout", "") or "").strip()
    if pending:
        return False, (
            "CachyOS/Arch package upgrades are pending. Run `sudo pacman -Syu`, "
            "reboot if requested, then retry. BC250 LLM MODE refuses partial upgrades."
        )
    if int(getattr(result, "returncode", 1)) not in (0, 1):
        return False, (
            "Pacman update status could not be verified; no package command was run. "
            "Complete `sudo pacman -Syu` manually, then retry."
        )
    return True, "Pacman reports no pending upgrades in the configured sync database."
