"""Pinned runtime and Bonsai integration. All profiles are disposable fixtures."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from bc250_llm_mode import prism_runtime as prism
from bc250_llm_mode.activation_adapter import ArtifactRejected
from bc250_llm_mode.gui.models_page import build_model_items, model_action
from bc250_llm_mode.model_artifact import VERDICT_RUNTIME_REQUIRED
from bc250_llm_mode.model_library import ModelLibraryQueryService
from bc250_llm_mode.operations.activation import ModelActivateRequestV1
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.runtime_lifecycle import RuntimeUpdateRequestV1
from bc250_llm_mode.operations.workflow import StepFailure
from bc250_llm_mode.queries import ApplicationQueryService
from bc250_llm_mode.repositories import ModelArtifactRepository, ModelInstallationsRepository
from bc250_llm_mode.runtime_builds import RuntimeBuildRepository, RuntimeComponentRepository, RuntimeTreeRepository
from bc250_llm_mode.runtime_handoff import build_payload
from bc250_llm_mode.runtime_process import ProcessFailure
from bc250_llm_mode.server import require_safe_start

from test_acquisition_adapter import ProductionHarness, build_gguf
from test_activation_adapter import world  # noqa: F401
from test_bonsai_runtime_requirements import ARCH, ROTATION, gguf
from test_runtime_adapter_real import real_adapter  # noqa: F401


def _known_fixture(monkeypatch, content):
    digest = hashlib.sha256(content).hexdigest()
    model = next(iter(prism._MODELS.values())).copy()
    model['byte_size'] = len(content)
    monkeypatch.setitem(prism._MODELS, digest, model)
    return digest


def _promote_fixture(units):
    """Seed an isolated repository fixture; never a live verification claim."""
    with units.begin() as conn:
        build = RuntimeBuildRepository(conn).create_immutable(manifest=prism.pinned_manifest())
        tree = RuntimeTreeRepository(conn).record_candidate(
            tree_id='prism-test-tree', build_id=build['build_id'], container_profile='fixture',
            locator='fixture/runtime', manifest_digest=build['manifest_digest'],
            server_binary_digest=prism.pinned_manifest()['binaries'][0]['sha256'])
        RuntimeTreeRepository(conn).move_role(tree['tree_id'], 'ACTIVE_OBSERVED')
        repo = RuntimeComponentRepository(conn)
        row = repo.initialize()
        repo.promote_verified(expected_generation=row['generation'], expected_promoted_build_id=None,
                              expected_rollback_build_id=None, promoted_build_id=build['build_id'],
                              promoted_tree_id=tree['tree_id'], rollback_build_id=None)
    return build


def test_only_exact_verified_ptq_files_gain_compatibility():
    for digest in prism._MODELS:
        assert prism.recognized_layout(VERDICT_RUNTIME_REQUIRED, 'sha256:' + digest) == prism.PRISM_LAYOUT
        assert prism.recognized_layout('rejected_fused_layout', digest) == 'rejected_fused_layout'
        assert prism.known_ptq1_artifact(digest[:16]) is None
    pq = '5b24ea3eebc3e0bccd05fb474eb88b10c57699d71a5db2f29485e3789a70d55d'
    assert prism.recognized_layout(VERDICT_RUNTIME_REQUIRED, pq) == VERDICT_RUNTIME_REQUIRED
    assert prism.recognized_layout(VERDICT_RUNTIME_REQUIRED, '0' * 64) == VERDICT_RUNTIME_REQUIRED


def test_crack_repack_is_an_installed_fit_variant_not_a_remote_file():
    from bc250_llm_mode.catalog import model_by_id, best_quant

    entry = model_by_id('bonsai2-27b-crack')
    digest = next(k for k, v in prism._MODELS.items() if v['catalog_id'] == entry.id)
    installed = prism.installed_fit_catalog(entry, digest)
    assert installed.weights_gib_by_quant == {'PTQ1_0': 5946648928 / 1024**3}
    assert set(entry.allow_globs) == {'PQ2_0'} and best_quant(entry, 8192)[0] == 'PQ2_0'
    items = {item.catalog_id: item for item in build_model_items((), context=8192, slots=1, prism_runtime_ready=True)}
    assert model_action(items['bonsai2-27b']).code == 'install-start'
    assert model_action(items['bonsai2-27b-crack']).code == 'fit'


def test_generated_bonsai_argv_requires_identity_and_bounded_settings(tmp_path):
    import sys
    from bc250_llm_mode.server import generate_launcher

    state = {'current_model':'bonsai', 'current_ctx':8192,
             'installed_models':[{'id':'bonsai','path':'/fixture.gguf','content_digest':next(iter(prism._MODELS))}],
             'optimizations':dict(prism.PRISM_SETTINGS), 'runtime_component_id':prism.pinned_build_id(),
             'runtime_source_commit':prism.PRISM_COMMIT,
             'runtime_server_sha256':prism.pinned_manifest()['binaries'][0]['sha256'],
             'runtime_manifest_digest':prism.pinned_build_id().rsplit(':',1)[1]}
    payload = build_payload(state, config_revision=1)
    handoff = tmp_path/'handoff.json'
    launcher = generate_launcher({'app_dir':str(tmp_path)})
    code = launcher.read_text().split("<<'PYH'\n",1)[1].split('\nPYH\n',1)[0]
    handoff.write_text(json.dumps(payload))
    result = subprocess.run([sys.executable,'-c',code,str(handoff)],capture_output=True,text=True)
    assert result.returncode == 0
    args=result.stdout.splitlines()
    assert args[args.index('--load-mode')+1]=='mmap' and '--no-context-shift' in args
    assert args[args.index('--reasoning')+1]=='off'
    assert args[args.index('--cache-ram')+1]=='0'
    for key,value in [('runtime_component_id','legacy:fixture'),('ctx_total',32768),('threads',6),('kv_cache_type','q4_0')]:
        handoff.write_text(json.dumps({**payload,key:value}))
        assert subprocess.run([sys.executable,'-c',code,str(handoff)],capture_output=True).returncode != 0


@pytest.mark.parametrize('use_prism', [False, True])
def test_ram_cache_flag_follows_runtime_capability_not_model_name(tmp_path, use_prism):
    """The new fork-only switch must not break older ordinary llama.cpp builds."""
    import sys
    from bc250_llm_mode.server import generate_launcher

    state = {'current_model': 'ordinary', 'current_ctx': 8192,
             'installed_models': [{'id': 'ordinary', 'path': '/fixture.gguf',
                                   'content_digest': '1' * 64}],
             'optimizations': dict(prism.PRISM_SETTINGS)}
    if use_prism:
        state.update(runtime_component_id=prism.pinned_build_id(),
                     runtime_source_commit=prism.PRISM_COMMIT,
                     runtime_server_sha256=prism.pinned_manifest()['binaries'][0]['sha256'],
                     runtime_manifest_digest=prism.pinned_build_id().rsplit(':', 1)[1])
    payload = build_payload(state, config_revision=1)
    handoff = tmp_path / 'handoff.json'
    handoff.write_text(json.dumps(payload))
    launcher = generate_launcher({'app_dir': str(tmp_path)})
    code = launcher.read_text().split("<<'PYH'\n", 1)[1].split('\nPYH\n', 1)[0]
    result = subprocess.run([sys.executable, '-c', code, str(handoff)],
                            capture_output=True, text=True)
    assert result.returncode == 0
    args = result.stdout.splitlines()
    assert ('--cache-ram' in args) is use_prism
    if use_prism:
        assert args[args.index('--cache-ram') + 1] == '0'


def test_imported_bonsai_row_is_blocked_until_exact_runtime_is_promoted(tmp_path, monkeypatch):
    content = gguf(ARCH, ROTATION)
    _known_fixture(monkeypatch, content)
    harness = ProductionHarness(tmp_path, content)
    assert harness.engine().execute_one(harness.operation_id).reason_code == 'MODEL_INSTALLED'
    library = ModelLibraryQueryService(harness.units, harness.paths)
    row, = library.entries(context=8192, slots=1)
    assert row.catalog_id == 'bonsai2-27b' and row.architecture == 'qwen35' and row.quant == 'PTQ1_0'
    assert row.runtime_requirement and row.display_name == 'Bonsai 2 27B (PrismML)'
    item = next(i for i in build_model_items((row,), context=8192, slots=1) if not i.remote)
    assert model_action(item).code == 'fit'
    _promote_fixture(harness.units)
    row, = library.entries(context=8192, slots=1)
    item = next(i for i in build_model_items((row,), context=8192, slots=1) if not i.remote)
    assert row.runtime_requirement is None and item.state == 'INSTALLED'
    assert model_action(item).code == 'activate'
    Path(row.path).write_bytes(b'modified managed test copy')
    assert harness.source.read_bytes() == content


def test_promoted_identity_is_read_before_query_connection_closes(tmp_path):
    harness = ProductionHarness(tmp_path, build_gguf())
    build = _promote_fixture(harness.units)
    state = ApplicationQueryService(harness.units, harness.paths).snapshot().data
    assert state['runtime_component_id'] == build['build_id']
    assert state['runtime_source_commit'] == prism.PRISM_COMMIT
    assert state['runtime_manifest_digest'] == build['manifest_digest']
    assert prism.supports_prism_state(state)
    with harness.units.begin() as conn:
        assert prism.prism_runtime_promoted(conn)
        RuntimeTreeRepository(conn).observe_location('prism-test-tree', server_binary_digest='0' * 64)
        assert not prism.prism_runtime_promoted(conn)


def test_copied_local_bytes_must_match_original_resolved_source(tmp_path):
    content = build_gguf()
    harness = ProductionHarness(tmp_path, content)
    copy = harness.host.copy_local
    def changed_source(ctx):
        harness.source.write_bytes(content[:-1] + b'x')
        return copy(ctx)
    harness.host.copy_local = changed_source
    assert harness.engine().execute_one(harness.operation_id).reason_code == 'FAILED_SAFE'
    assert not harness.final_artifacts()


def test_partial_local_copy_restarts_without_appending(tmp_path):
    content = build_gguf()
    harness = ProductionHarness(tmp_path, content)
    staging = harness.host._staging(harness.operation_id)
    (staging/'source.partial').write_bytes(content[:8])
    assert harness.engine().execute_one(harness.operation_id).reason_code == 'MODEL_INSTALLED'
    assert harness.final_artifacts()[0].read_bytes() == content


def test_local_source_hashing_renews_its_lease_before_transfer(tmp_path):
    harness = ProductionHarness(tmp_path, build_gguf())
    original = harness.host._hash_fd
    def slow_hash(fd, *, pulse=None):
        assert pulse is not None
        for _ in range(3):
            harness.clock.advance(45)
            pulse()
        return original(fd, pulse=pulse)
    harness.host._hash_fd = slow_hash
    assert harness.engine().execute_one(harness.operation_id).reason_code == 'MODEL_INSTALLED'


def test_activation_hashing_renews_lease_through_pre_restart_recheck(world):
    from tests.operations.fakes import FakeClock
    from bc250_llm_mode.operations.engine import ExecutionEngine
    from bc250_llm_mode.operations.workflow import EnqueueService

    clock = FakeClock()
    original = world.adapter._identity
    def slow_identity(path, *, pulse=None):
        assert pulse is not None
        for _ in range(3):
            clock.advance(45)
            pulse()
        return original(path, pulse=pulse)
    world.adapter._identity = slow_identity
    record = EnqueueService(world.units, world.registry, clock=clock.now, uuid_factory=lambda:'op-slow-hash').enqueue(
        operation_type='MODEL_ACTIVATE', payload={'model_alias':world.model_b.id}, surface='test')
    outcome = ExecutionEngine(world.units, world.registry, clock=clock.now, worker_id='slow-hash',
                              uuid_factory=lambda:'effect-slow-hash', lease_ttl_seconds=60).execute_one(record.id)
    assert outcome.reason_code == 'SUCCEEDED'


def _install_activation_fixture(world, monkeypatch):
    content = gguf(ARCH, ROTATION)
    digest = _known_fixture(monkeypatch, content)
    path = world.database.parent/'bonsai.gguf'
    path.write_bytes(content)
    with world.units.begin() as conn:
        ModelArtifactRepository(conn).record_verified(
            artifact_id='bonsai-fixture', content_digest='sha256:' + digest, byte_size=len(content),
            canonical_path=str(path), architecture='qwen35', quantization='PTQ1_0',
            catalog_id='bonsai2-27b', source_kind='local')
        ModelInstallationsRepository(conn).install_alias(alias='bonsai-fixture', artifact_id='bonsai-fixture',
                                                       quant='PTQ1_0', display_name='Bonsai fixture')
    return ModelActivateRequestV1(model_alias='bonsai-fixture', context_per_slot=8192, parallel_slots=1)


def test_activation_requires_promoted_runtime_and_fences_its_loss(world, monkeypatch):
    request = _install_activation_fixture(world, monkeypatch)
    with pytest.raises(ArtifactRejected, match='MODEL_RUNTIME_REQUIRED'):
        world.adapter.resolve_candidate(request)
    _promote_fixture(world.units)
    candidate = world.adapter.resolve_candidate(request)
    assert candidate.layout_verdict == prism.PRISM_LAYOUT
    assert all(candidate.settings[k] == v for k, v in prism.PRISM_SETTINGS.items())
    assert world.adapter.observe_candidate(request, candidate).classification is RecoveryClass.COMPLETE
    with world.units.begin() as conn:
        RuntimeTreeRepository(conn).observe_location('prism-test-tree', server_binary_digest='0' * 64)
    assert world.adapter.observe_candidate(request, candidate).classification is not RecoveryClass.COMPLETE
    assert world.server.restarts == 0


@pytest.mark.parametrize('context,slots', [(16384, 1), (8192, 2)])
def test_bonsai_rejects_unqualified_context_and_slot_settings(world, monkeypatch, context, slots):
    request = _install_activation_fixture(world, monkeypatch)
    _promote_fixture(world.units)
    with pytest.raises(ArtifactRejected, match='PRISM_REQUIRES_8192'):
        world.adapter.resolve_candidate(replace(request, context_per_slot=context, parallel_slots=slots))


def test_all_start_paths_require_prism_identity_and_compatibility_settings():
    digest = next(iter(prism._MODELS))
    manifest = prism.pinned_manifest()
    state = {'current_model':'bonsai', 'current_ctx':8192,
             'installed_models':[{'id':'bonsai','path':'/fixture.gguf','content_digest':digest}],
             'optimizations':dict(prism.PRISM_SETTINGS)}
    with pytest.raises(RuntimeError, match='verified Prism'):
        require_safe_start(state)
    state.update(runtime_component_id=prism.pinned_build_id(), runtime_source_commit=prism.PRISM_COMMIT,
                 runtime_server_sha256=manifest['binaries'][0]['sha256'])
    require_safe_start(state)
    assert build_payload(state, config_revision=1)['model_runtime'] == 'prism-ptq1'
    state['optimizations']['batch_size'] = 2048
    with pytest.raises(RuntimeError, match='compatibility settings'):
        require_safe_start(state)


@pytest.fixture
def staged_prism(real_adapter, monkeypatch):
    adapter, upstream, commit = real_adapter
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('native compiler needed for executable fixture')
    root = Path(adapter._prism_bundle_root())
    root.mkdir(parents=True, mode=0o700)
    shutil.copytree(upstream, root/'source')
    (root/'build/bin').mkdir(parents=True)
    manifest = prism.pinned_manifest()
    manifest.update(source_commit=commit, upstream_repository=str(upstream),
                    target_arch=subprocess.check_output(['uname','-m'], text=True).strip(),
                    container_image_id='sha256:'+'a'*64, container_image_digest='sha256:'+'b'*64)
    binaries=[]
    for name in ['llama-server','llama-cli','llama-quantize']:
        path=root/'build/bin'/name
        subprocess.run([compiler,str(upstream/'main.c'),str(upstream/'helper.c'),'-o',str(path)],check=True,capture_output=True)
        path.chmod(0o755)
        text=adapter._binary_smoke_text(str(path),name)
        binaries.append(dict(path='build/bin/'+name,size=path.stat().st_size,mode='755',
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             version_output_digest=hashlib.sha256(text.encode()).hexdigest()))
    manifest['binaries']=binaries
    monkeypatch.setattr(prism,'PRISM_COMMIT',commit)
    monkeypatch.setattr(prism,'_PINNED_MANIFEST',manifest)
    (root/'prism-build.json').write_text(json.dumps(manifest))
    return adapter,root,commit


def test_staged_runtime_is_fully_checked_and_registered_without_compilation(staged_prism):
    adapter,root,commit=staged_prism
    request=RuntimeUpdateRequestV1(requested_ref=prism.PRISM_REF)
    assert adapter.resolve_source(request).source_commit==commit
    assert adapter.fetch_exact_commit(request,commit,lambda **kw:None).fetch_state=='EXISTING'
    environment=adapter.configure_build(request,commit,lambda **kw:None)
    candidate=adapter.compile_candidate(environment,lambda **kw:None)
    from bc250_llm_mode.operations.repositories import OperationRepository
    with adapter._units.begin() as conn:
        OperationRepository(conn).create(operation_type='RUNTIME_UPDATE',request={},surface='test',operation_id='prism-stage')
    smoke=adapter.smoke_and_register_candidate(request,commit,environment,candidate,'prism-stage')
    assert adapter.observe_candidate_manifest(smoke).classification is RecoveryClass.COMPLETE
    with adapter._units.read() as conn:
        assert not (RuntimeComponentRepository(conn).current() or {}).get('promoted_build_id')
        stored=RuntimeBuildRepository(conn).require(smoke.build_id)
        assert prism.is_pinned_manifest(stored['manifest']) and adapter._recipe_matches(stored['manifest'])
    assert not any('cmake' in spec.argv for spec in adapter._proc.specs)


@pytest.mark.parametrize('mutation', ['binary','receipt','source','symlink','permissions'])
def test_changed_staged_runtime_is_refused_before_registration(staged_prism, mutation):
    adapter,root,commit=staged_prism
    if mutation=='binary':(root/'build/bin/llama-cli').write_bytes(b'changed')
    elif mutation=='receipt':(root/'prism-build.json').write_text('{}')
    elif mutation=='source':(root/'source/main.c').write_text('changed')
    elif mutation=='symlink':
        file=root/'build/bin/llama-server';file.unlink();file.symlink_to(root/'build/bin/llama-cli')
    elif mutation=='permissions':(root/'build/bin/llama-server').chmod(0o777)
    with pytest.raises((StepFailure, ProcessFailure)):
        adapter.configure_build(RuntimeUpdateRequestV1(requested_ref=prism.PRISM_REF),commit,lambda **kw:None)
    with adapter._units.read() as conn:
        assert RuntimeBuildRepository(conn).get(prism.pinned_build_id()) is None


def test_stock_runtime_cannot_replace_prism_while_bonsai_is_selected(real_adapter):
    adapter,_,_=real_adapter
    digest=next(iter(prism._MODELS))
    adapter.state_supplier=lambda:{'current_model':'bonsai','installed_models':[{'id':'bonsai','content_digest':digest}]}
    with pytest.raises(StepFailure, match='MODEL_RUNTIME_REQUIRED'):
        adapter.verify_activation_boundary(None,SimpleNamespace(), 'legacy:stock')
