# ADR 007 — Capability-driven Linux host integration

**Status:** Accepted

**Date:** 2026-08-28

## Context

The appliance was originally integrated directly with Bazzite. The durable
database, operation engine, model/runtime lifecycle, gateway, chat, and backup
subsystems are portable, but native Tk installation and current-boot host-mode
control assumed `rpm-ostree` and Bazzite-specific desktop services. CachyOS is
a relevant BC-250 host, but it is mutable Arch Linux, uses Pacman, and may boot
through systemd-boot, GRUB, Limine, or rEFInd.

Scattering distribution-name branches through frontends would duplicate the
privileged control plane and make unsupported boot mutations likely. Persisting
detected host facts would also become stale after an OS reinstall, boot-manager
change, or migration of an application profile.

## Decisions

1. `host_platform.py` is the sole host-integration authority. It performs
   bounded read-only detection from `/etc/os-release`, command presence,
   systemd markers, immutable-root markers, and known boot-manager files.
2. Distribution identity is diagnostic input. Mutations are selected only
   from closed package/service contracts; unrecognized combinations fail
   closed.
3. Bazzite and CachyOS have native integration profiles. Arch-family, Fedora,
   Debian-family, and SUSE adapters are `compatible-unqualified` until each
   receives separate physical BC-250 evidence. Non-Linux, non-systemd, and
   unknown-family hosts are unsupported.
4. Bazzite retains `rpm-ostree install python3-tkinter` and its required reboot.
   CachyOS uses `pacman -S --needed --noconfirm tk` only after a read-only
   `pacman -Qu` preflight. The app never invokes `pacman -Sy`, never initiates
   a full system upgrade, and refuses installation when pending upgrades are
   observed. Runtime dependency plans are preview-only.
5. The Fedora Distrobox remains the controlled inference/build guest on every
   host. Host package managers install only the desktop/container/Vulkan
   integration needed to run that guest.
6. Current-boot LLM Mode requires systemd and udev. It uses
   `display-manager.service`, not a guessed SDDM/GDM unit, and preserves the
   next-boot `graphical.target`/no-autostart invariant.
7. Persistent `amdgpu.runpm=0` cleanup remains automated only for an app-owned
   rpm-ostree deployment. CachyOS boot managers are detected and reported but
   not edited in this release. An externally supplied argument is surfaced as
   an explicit manual-recovery condition.
8. Cyan GPU tuning is capability-gated. If its configuration is absent, the
   GUI disables clock controls. The thermal watchdog still monitors and keeps
   its emergency-stop latch, but reports that a clock cap was not applied.
9. Host observations are recomputed at every composition and are never stored
   as durable truth. They appear in platform/status/doctor/home/support-bundle
   read models only.
10. CLI, chat, wizard, and dashboard host-mode actions all route through the
    same composed `HostModeService`. Frontends may not import distribution
    adapters or package-manager commands.
11. `platform status` and `platform plan` execute before application
    composition and perform no filesystem or database writes.
12. Implementation support is not physical qualification. Every advertised
    native profile must repeat install, reboot, host-mode, inference, thermal,
    runtime rollback, backup/restore, and soak evidence on a physical BC-250.

## Security and failure policy

- Package plans contain fixed argv and package names; no `/etc/os-release`
  value enters an executed command.
- Ambiguous boot-manager evidence disables persistent mutation.
- Missing package, service, udev, or platform capabilities produce typed JSON
  diagnostics or a refusal before host effects.
- The existing privilege wrapper and frozen elevated-call-site census remain
  unchanged.
- Platform diagnostics contain no credentials, prompts, model contents, or
  raw application state.

## Consequences

The application can run its reviewed host workflow on CachyOS without
`rpm-ostree`, while Bazzite behavior remains regression-tested. Other systemd
distributions can be added by extending the closed detector and package maps,
without changing durable operations or frontends. Persistent CachyOS
boot-configuration automation remains intentionally deferred until each boot
backend has atomic backup, verification, rollback, and physical reboot tests.
