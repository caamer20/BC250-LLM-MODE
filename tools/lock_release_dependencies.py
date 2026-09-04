"""Generate reviewed hash locks from a Python-3.11 resolution and local 3.14 environment.

This explicit maintainer command reads bounded PyPI release metadata. It never
installs packages. Lock changes still need the normal CI and candidate review.
"""
import argparse
import importlib.metadata
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python311-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    current = {normalize(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()
               if normalize(d.metadata["Name"]) not in {"bc250-llm-mode", "pip"}}
    older = {normalize(row["metadata"]["name"]): row["metadata"]["version"]
             for row in json.loads(args.python311_report.read_text())["install"]
             if normalize(row["metadata"]["name"]) != "bc250-llm-mode"}
    # Keep the environment already tested on this checkout. NumPy has a newer
    # Python floor, so 3.11 uses its separately resolved compatible wheel line.
    for name in older.keys() & current.keys() - {"numpy"}:
        older[name] = current[name]
    pairs = sorted(set(current.items()) | set(older.items()))

    def fetch(pair):
        name, version = pair
        if not re.fullmatch(r"[a-z0-9-]+", name) or not re.fullmatch(r"[a-zA-Z0-9.+-]+", version):
            raise ValueError("invalid package identity")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as response:
            raw = response.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise ValueError("metadata size bound")
        metadata = json.loads(raw)
        hashes = sorted({item["digests"]["sha256"] for item in metadata["urls"]
                         if item["packagetype"] == "bdist_wheel" and not item.get("yanked")})
        if not hashes or any(not re.fullmatch("[0-9a-f]{64}", digest) for digest in hashes):
            raise ValueError("unavailable wheel hashes")
        return pair, hashes

    with ThreadPoolExecutor(max_workers=4) as pool:
        hashes = dict(pool.map(fetch, pairs))
    args.output.mkdir(parents=True, exist_ok=True)
    for tag, versions in (("3.11", older), ("3.14", current)):
        lines = [f"# Reviewed release/test environment for Python {tag}.",
                 "# Install with --require-hashes --only-binary=:all:; update explicitly."]
        for name, version in sorted(versions.items()):
            lines.append(f"{name}=={version} " + " ".join(f"--hash=sha256:{digest}" for digest in hashes[name, version]))
        (args.output / f"python-{tag}.txt").write_text("\n".join(lines) + "\n")
        print(f"Python {tag}: {len(versions)} exact packages with wheel hashes")


if __name__ == "__main__":
    main()
