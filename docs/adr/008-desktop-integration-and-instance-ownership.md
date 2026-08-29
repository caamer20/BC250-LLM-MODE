# ADR 008 — Desktop integration and GUI instance ownership

Status: accepted for EXP-1 implementation

## Decisions

1. Desktop integration is per-user and writes only the resolved XDG
   applications directory, user-local executable directory, and user-local
   icon directory.
2. The desktop entry launches one fixed app-owned launcher without a shell
   command line and never installs an autostart entry.
3. Files are published atomically and carry a private ownership receipt with
   their SHA-256 digests. Removal refuses a file whose content no longer
   matches the receipt.
4. Only the GUI is single-instance. CLI and operation-worker processes remain
   independent peers.
5. GUI ownership uses a profile-local `flock`, mode-0600 AF_UNIX socket, a
   profile-local mode-0600 random nonce, a closed bounded JSON protocol, and
   same-UID peer validation where available. If an OS AF_UNIX path limit makes
   the profile socket name impossible, the socket alone uses a UID- and
   profile-hashed name under `XDG_RUNTIME_DIR` or the platform temporary
   directory; lock, nonce, permissions, and peer checks remain authoritative.
6. The owner socket is polled by the existing Tk refresh coordinator; no
   listener thread, daemon, signal, or process kill is introduced.
7. Stale socket/nonce cleanup is allowed only after exclusive lock ownership
   is obtained. A contender never removes owner state.
8. Activation accepts only `ACTIVATE`, `ROUTE`, `OPEN_OPERATION`, and
   `OPEN_MODEL`; route and identifier values are bounded and never interpreted
   as paths, commands, prompts, or shell text.
