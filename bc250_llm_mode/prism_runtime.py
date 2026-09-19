"""Pinned Bonsai compatibility artifact and exact model identities.

This is an explicit, locally staged compatibility build, not a general binary
import or a performance claim. Its original source/recipe/toolchain and all
three executable digests are fixed. The runtime lifecycle still owns exchange,
receipt verification, inference, promotion and rollback.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

PRISM_REF = "prism-bonsai-2"
PRISM_COMMIT = "5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6"
PRISM_LAYOUT = "prism_ptq1"
PRISM_CONTEXT_LIMIT = 8192
PRISM_SETTINGS = {"batch_size": 128, "ubatch_size": 128, "threads": 2,
                  "kv_cache_type": "q8_0", "parallel_slots": 1, "flash_attention": "auto",
                  "runtime_enabled": True, "fast_sync": False}
PRISM_REQUIREMENT = (
    "Requires the verified Prism Bonsai runtime. This experimental model supports "
    "up to 8,192 context tokens and one slot in this installation."
)

# Observed build manifest, from the audited BC250 compatibility build. Never
# accept a user-supplied manifest as authority for these capabilities.
_PINNED_MANIFEST = {'binaries': [{'mode': '755',
               'path': 'build/bin/llama-server',
               'sha256': 'a5e325d87d52cc89bcebd196fa92cec36c0aeaebb1bdacb1ad2c81c1c995ff6c',
               'size': 88245928,
               'version_output_digest': 'a95f5596b458716b36ef8d39c9658ac4222f1b1a43e78cbe843b3992f8ac36dc'},
              {'mode': '755',
               'path': 'build/bin/llama-cli',
               'sha256': '60bc05ab311ba6adf4ebefd4d1adc09ecd1b50029b15cf2a042ee4d08dd44cdc',
               'size': 88452928,
               'version_output_digest': 'a95f5596b458716b36ef8d39c9658ac4222f1b1a43e78cbe843b3992f8ac36dc'},
              {'mode': '755',
               'path': 'build/bin/llama-quantize',
               'sha256': '489a89a8a769d1850c1840ce5b74d278bc4ba0a935fbdfb73fb8acadc31facad',
               'size': 69240312,
               'version_output_digest': '336cfc46bda93c688d9063a7cec953ae9d3596464230db436117687b25043f3b'}],
 'build_parallelism': {'policy': 'bounded-1'},
 'cmake_generator': 'Unix Makefiles',
 'cmake_options': ['-DCMAKE_BUILD_TYPE=Release',
                   '-DBUILD_SHARED_LIBS=OFF',
                   '-DGGML_VULKAN=ON',
                   '-DGGML_NATIVE=OFF',
                   '-DGGML_OPENMP=OFF',
                   '-DLLAMA_CURL=OFF',
                   '-DLLAMA_BUILD_TESTS=ON',
                   '-DVulkan_GLSLC_EXECUTABLE=/run/host/var/tmp/bc250-bonsai-experimental/build-tools/glslc',
                   '-DCMAKE_C_COMPILER=/run/host/var/tmp/bc250-bonsai-experimental/compiler-root/usr/bin/clang',
                   '-DCMAKE_CXX_COMPILER=/run/host/var/tmp/bc250-bonsai-experimental/compiler-root/usr/bin/clang++',
                   '-DCMAKE_CXX_FLAGS_RELEASE=-O0 -DNDEBUG',
                   '-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG',
                   '-DCMAKE_CXX_COMPILER_LAUNCHER=/run/host/var/tmp/bc250-bonsai-experimental/build-tools/bounded-cxx.py'],
 'cmake_targets': ['llama-server', 'llama-cli', 'llama-quantize'],
 'component': 'llamacpp',
 'container_image_digest': 'sha256:c7b2fc48e60b10f3633e24c81ef56e054e780585465d2c129a6af81e6a883fd6',
 'container_image_id': 'sha256:dad1e5b55410e25ab1b24f7f9fb3681290a229dce3216bb7645fee8fc3645a01',
 'recipe_digest': '69f7475828a3b4bc9cc175320f418d612f8c6b0d7edde1824c35ad21bd2a1f01',
 'recipe_version': 1,
 'requested_ref': 'prism-bonsai-2',
 'schema_version': 1,
 'smoke_contract_version': 1,
 'source_checkout_verified': True,
 'source_commit': '5d80cff0b8cb9f2bf823cfc4e71e3abb97f290d6',
 'target_arch': 'x86_64',
 'toolchain': {'cmake': '6b8c2501a983639b330c555c9a105057ea877b701cf25920bc3fe32dd2a54b13',
               'compiler': 'a2e19e6c9520faa00c47e5d55ae1fe65c98f427ed6dc7e83471a853556e47cc4',
               'compiler_packages': 'c14d54782547502893c8c46c3ed7cb46209aafdf62662dccbfd7dd729e42e2c1',
               'cxx_launcher': 'a7f69f20e1adcd91b2f3cf8468c8620b03ef2ac6c6e9292e2c8f46dfc82225eb',
               'glslc': 'bc30d2ebcc6895cf330e7f9420300c1ef2725fd51a2d4b8d396c0dad96c0eaf0',
               'libc': '4f8da2367faf020d6a92f6d786528529dbbe72bf8a2785cc8bf1b57d69e2338b',
               'linker': '6dad389662ee1694d73c2439261c9fe27262df2759868fcaf955a912f2387ded',
               'make': '11b6ecd77585d4680846f6b6ff705d7bceedf8f7ca63eb30d92661fed1ca66c7'},
 'upstream_repository': 'https://github.com/PrismML-Eng/llama.cpp'}

_MODELS = {
    "53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3": {
        "catalog_id": "bonsai2-27b", "quantization": "PTQ1_0",
        "byte_size": 5946648928, "architecture": "qwen35", "tensor_count": 851,
        "source_repo": "prism-ml/Ternary-Bonsai-2-27B-gguf",
        "display_name": "Bonsai 2 27B (PrismML)",
    },
    "5a264c32944e90222d275b47239ca50b56a175e2edc97e5bdb99c59d7c8f4e15": {
        "catalog_id": "bonsai2-27b-crack", "quantization": "PTQ1_0",
        "byte_size": 5946648928, "architecture": "qwen35", "tensor_count": 851,
        "source_repo": "dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF",
        "display_name": "Bonsai 2 27B CRACK (lossless PTQ1_0)",
        "derived_from_sha256": "5b24ea3eebc3e0bccd05fb474eb88b10c57699d71a5db2f29485e3789a70d55d",
    },
}


def pinned_manifest() -> dict[str, Any]:
    return deepcopy(_PINNED_MANIFEST)


def pinned_build_id() -> str:
    from .runtime_builds import derive_build_id
    return derive_build_id(_PINNED_MANIFEST)[0]


def is_pinned_manifest(manifest: dict[str, Any]) -> bool:
    # RuntimeBuildRepository stores display-only requested_ref separately.
    return ({k: v for k, v in manifest.items() if k != "requested_ref"}
            == {k: v for k, v in _PINNED_MANIFEST.items() if k != "requested_ref"})


def known_ptq1_artifact(digest: str | None) -> dict[str, Any] | None:
    value = str(digest or "").removeprefix("sha256:")
    model = _MODELS.get(value)
    return dict(model) if model else None


def recognized_layout(raw_verdict: str, digest: str) -> str:
    if raw_verdict == "rejected_runtime_required" and known_ptq1_artifact(digest):
        return PRISM_LAYOUT
    return raw_verdict


def prism_runtime_promoted(conn) -> bool:
    """Query only: command/start boundaries recheck the actual binary/receipt."""
    from .runtime_builds import RuntimeBuildRepository, RuntimeComponentRepository, RuntimeTreeRepository
    component = RuntimeComponentRepository(conn).current() or {}
    if component.get("promoted_build_id") != pinned_build_id():
        return False
    record = RuntimeBuildRepository(conn).get(component["promoted_build_id"])
    tree = RuntimeTreeRepository(conn).get(component.get("promoted_tree_id") or "")
    return bool(record and record.get("provenance_class") == "IMMUTABLE_SOURCE"
                and is_pinned_manifest(record.get("manifest") or {})
                and tree and tree.get("build_id") == component["promoted_build_id"]
                and tree.get("role") == "ACTIVE_OBSERVED"
                and tree.get("server_binary_digest") == _PINNED_MANIFEST["binaries"][0]["sha256"])


def supports_prism_state(state: dict[str, Any]) -> bool:
    return (state.get("runtime_component_id") == pinned_build_id()
            and state.get("runtime_source_commit") == PRISM_COMMIT
            and state.get("runtime_server_sha256") == _PINNED_MANIFEST["binaries"][0]["sha256"])


def catalog_requirement(entry: Any, *, prism_ready: bool) -> str | None:
    # CRACK's remote file is PQ2, so runtime promotion never makes that
    # unsupported publisher packing installable. Its verified PTQ repack is
    # admitted separately by complete content identity during local import.
    if entry.id == "bonsai2-27b" and prism_ready:
        return None
    return entry.runtime_requirement


def installed_fit_catalog(entry: Any, digest: str | None):
    """The local CRACK repack must not invent a downloadable Hub variant."""
    from dataclasses import replace

    model = known_ptq1_artifact(digest)
    if entry is not None and model:
        return replace(entry, weights_gib_by_quant={"PTQ1_0": model["byte_size"] / 1024**3})
    return entry
