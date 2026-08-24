"""Immutable hub source resolution and range-aware transfer (U1.1 §5).

Typed httpx adapter: pins an immutable repository revision before any byte
moves, enumerates only catalog-allowed files, transfers with durable
validators and safe partial resume, strips credentials on cross-origin
redirects, and never logs or persists signed URLs or tokens.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

MAX_METADATA_BYTES = 256 * 1024
MAX_REDIRECTS = 3
MAX_RETRIES = 2
CHUNK_BYTES = 4 * 1024 * 1024
CONNECT_TIMEOUT_S = 10.0
READ_IDLE_TIMEOUT_S = 30.0


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def catalog_fingerprint(entry, quantization: str) -> str:
    """Canonical digest of acquisition-relevant catalog policy (§5.1)."""
    payload = {
        "schema": 1,
        "id": entry.id,
        "repo": entry.repo,
        "source_repo": entry.source_repo,
        "allow_glob": entry.allow_globs.get(quantization),
        "avoid": list(entry.avoid),
        "conversion": bool(entry.conversion),
        "checksum_manifest": entry.checksum_manifest,
        "temporary_disk_gib": entry.temporary_disk_gib,
        "true_block_count": entry.true_block_count,
        "validation_tier": entry.validation_tier,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "fp:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _safe_remote_filename(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    if name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._/@+-]+", name)) and ".." not in name


@dataclass
class BlobIdentity:
    filename: str
    revision: str
    repo: str
    expected_size: int
    validator: str


class HubClient:
    """Bounded HTTP client pinned to one immutable base origin."""

    def __init__(
        self,
        *,
        api_base: str = "https://huggingface.co/api",
        file_base: str = "https://huggingface.co",
        token_provider: Callable[[], str | None] | None = None,
        transport=None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.file_base = file_base.rstrip("/")
        self._token = token_provider or (lambda: None)
        self._transport = transport

    def _client(self):
        return httpx.Client(
            timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_IDLE_TIMEOUT_S),
            transport=self._transport,
            follow_redirects=False,
        )

    def _auth_headers(self, url_host_trusted: bool) -> dict[str, str]:
        token = self._token()
        if token and url_host_trusted:
            return {"Authorization": f"Bearer {token}"}
        return {}

    @staticmethod
    def _same_origin(url: str, base: str) -> bool:
        from urllib.parse import urlparse

        return urlparse(url).netloc == urlparse(base).netloc

    def resolve_revision(self, repo: str) -> str:
        """Pin the repository's current default revision to an immutable SHA."""
        url = f"{self.api_base}/models/{repo}"
        with self._client() as client:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    response = client.get(url, headers=self._auth_headers(True))
                except httpx.HTTPError:
                    if attempt >= MAX_RETRIES:
                        raise SourceError("SOURCE_TIMEOUT", "metadata unreachable")
                    continue
                if response.status_code == 401:
                    raise SourceError("SOURCE_AUTH_REQUIRED", "auth required")
                if response.status_code == 429:
                    raise SourceError("SOURCE_RATE_LIMITED", "rate limited")
                if response.status_code != 200:
                    raise SourceError(
                        "SOURCE_REVISION_UNAVAILABLE", f"status {response.status_code}"
                    )
                if len(response.content) > MAX_METADATA_BYTES:
                    raise SourceError("SOURCE_MANIFEST_INVALID", "metadata too large")
                try:
                    sha = response.json().get("sha")
                except ValueError as exc:
                    raise SourceError(
                        "SOURCE_MANIFEST_INVALID", "bad metadata JSON"
                    ) from exc
                if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise SourceError("SOURCE_MANIFEST_INVALID", "no immutable revision")
                return sha
        raise SourceError("SOURCE_TIMEOUT", "unreachable")

    def observe_blob(self, repo: str, revision: str, filename: str) -> BlobIdentity:
        """HEAD observation pinning size and transport validator."""
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise SourceError("SOURCE_MANIFEST_INVALID", "mutable revision refused")
        if not _safe_remote_filename(filename):
            raise SourceError("SOURCE_MANIFEST_INVALID", "unsafe filename")
        url = f"{self.file_base}/{repo}/resolve/{revision}/{filename}"
        with self._client() as client:
            target = url
            trusted = True
            redirects = 0
            while True:
                response = client.head(target, headers=self._auth_headers(trusted))
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    if not location:
                        raise SourceError("SOURCE_REDIRECT_REFUSED", "empty redirect")
                    redirects += 1
                    if redirects > MAX_REDIRECTS:
                        raise SourceError(
                            "SOURCE_REDIRECT_REFUSED", "too many redirects"
                        )
                    trusted = self._same_origin(location, self.file_base)
                    target = location
                    continue
                if response.status_code == 401:
                    raise SourceError("SOURCE_AUTH_REQUIRED", "auth required")
                if response.status_code == 429:
                    raise SourceError("SOURCE_RATE_LIMITED", "rate limited")
                if response.status_code != 200:
                    raise SourceError(
                        "SOURCE_REVISION_UNAVAILABLE", f"status {response.status_code}"
                    )
                length = response.headers.get("Content-Length")
                validator = (
                    response.headers.get("ETag")
                    or response.headers.get("X-Linked-Etag")
                    or ""
                )
                break
        if length is None:
            raise SourceError("SOURCE_MANIFEST_INVALID", "no content length")
        return BlobIdentity(
            filename=filename,
            revision=revision,
            repo=repo,
            expected_size=int(length),
            validator=hashlib.sha256(validator.encode()).hexdigest()[:16],
        )

    def stream_range(
        self,
        blob: BlobIdentity,
        dest: Path,
        *,
        expected_fingerprint: str,
        pulse: Callable[..., None] | None = None,
    ) -> int:
        """Range-resumable download into ``dest``; returns completed size.

        Resume requires a matching partial receipt, immutable blob identity,
        and compatible validator; anything else resets the owned partial.
        """
        receipt_path = dest.with_name(dest.name + "-receipt.json")
        resume_from = 0
        if dest.exists():
            receipt = None
            if receipt_path.exists():
                try:
                    receipt = json.loads(receipt_path.read_text())
                except (ValueError, OSError):
                    receipt = None
            if (
                isinstance(receipt, dict)
                and receipt.get("fingerprint") == expected_fingerprint
                and receipt.get("validator") == blob.validator
                and receipt.get("total") == blob.expected_size
                and dest.stat().st_size <= blob.expected_size
            ):
                resume_from = dest.stat().st_size
            else:
                # Foreign/stale/corrupt partial: reset only the owned file.
                dest.unlink(missing_ok=True)
                receipt_path.unlink(missing_ok=True)

        url = f"{self.file_base}/{blob.repo}/resolve/{blob.revision}/{blob.filename}"
        written = resume_from
        with self._client() as client:
            for attempt in range(MAX_RETRIES + 1):
                headers = self._auth_headers(True)
                if written:
                    headers["Range"] = f"bytes={written}-"
                    headers["If-Range"] = blob.validator
                try:
                    with client.stream("GET", url, headers=headers) as response:
                        if response.status_code == 200 and written:
                            written = 0  # server ignored Range: reset partial
                        elif response.status_code == 416:
                            if written == blob.expected_size:
                                break
                            raise SourceError("SOURCE_RANGE_MISMATCH", "range refused")
                        elif response.status_code in (301, 302, 303, 307, 308):
                            raise SourceError(
                                "SOURCE_REDIRECT_REFUSED",
                                "redirect on body not followed",
                            )
                        elif response.status_code not in (200, 206):
                            raise SourceError(
                                "SOURCE_REVISION_UNAVAILABLE",
                                f"status {response.status_code}",
                            )
                        with open(dest, "ab" if written else "wb") as fh:
                            for chunk in response.iter_bytes(CHUNK_BYTES):
                                fh.write(chunk)
                                fh.flush()
                                written += len(chunk)
                                if pulse is not None:
                                    pulse(
                                        phase="transfer",
                                        current=written,
                                        total=blob.expected_size,
                                        unit="bytes",
                                        cancellation_safe=True,
                                    )
                        break
                except SourceError:
                    raise
                except httpx.HTTPError:
                    if attempt >= MAX_RETRIES:
                        raise SourceError(
                            "SOURCE_TIMEOUT", "transfer failed after retries"
                        )
                    written = dest.stat().st_size if dest.exists() else 0
        if written != blob.expected_size:
            raise SourceError("SOURCE_RANGE_MISMATCH", "incomplete transfer")
        receipt_path.write_text(
            json.dumps(
                {
                    "fingerprint": expected_fingerprint,
                    "validator": blob.validator,
                    "total": blob.expected_size,
                },
                sort_keys=True,
            )
        )
        return written