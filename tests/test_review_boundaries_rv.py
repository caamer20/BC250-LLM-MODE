"""Crash, cross-process, live HTTP and release binding review exit gates."""
import json
import socket
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from bc250_llm_mode.backup_adapter import BackupHostAdapter
from bc250_llm_mode.operations.backup import BackupRestoreRequestV1
from bc250_llm_mode.operations.workflow import RecoveryClass
from bc250_llm_mode.profile_access import profile_access
from bc250_llm_mode.restore_profile import read_receipt
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory
from test_backup_adapter import _profile, _create_backup, _ctx, _seed_operation, _fake_exchange


class SimulatedDeath(BaseException):
    pass


@pytest.mark.parametrize("after_exchange", [False, True])
def test_restore_takeover_never_blindly_exchanges_twice(tmp_path, after_exchange):
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths)
    _, _, _, record = _create_backup(adapter, paths, units)
    with units.read() as conn:
        digest = conn.execute("SELECT manifest_digest FROM backup_sets WHERE backup_id=?", (record['backup_id'],)).fetchone()[0]
    _seed_operation(units, "restore-crash", "BACKUP_RESTORE")
    ctx = _ctx("restore-crash", BackupRestoreRequestV1(backup_id=record['backup_id'], confirmation_digest=digest))
    calls = []
    def crash(active, candidate, **kwargs):
        calls.append(1)
        if after_exchange:
            _fake_exchange(active, candidate, **kwargs)
        raise SimulatedDeath()
    with profile_access(paths.app_dir, exclusive=True):
        adapter.stage_candidate(ctx)
        adapter._exchange = crash
        with pytest.raises(SimulatedDeath):
            adapter.publish_exchange(ctx)
        assert read_receipt(paths.app_dir, ctx.operation_id)['phase'] == 'intent'
        adapter._exchange = _fake_exchange
        assert adapter.probe_restore_published(ctx).classification is (RecoveryClass.COMPLETE if after_exchange else RecoveryClass.ABSENT)
        adapter.publish_exchange(ctx)
        adapter.verify_post_restore(ctx)
        adapter.promote_or_rollback(ctx)
        assert read_receipt(paths.app_dir, ctx.operation_id)['phase'] == 'promoted'
        assert adapter.probe_terminal(ctx).classification is RecoveryClass.COMPLETE


@pytest.mark.parametrize("mode", ["cancel", "deadline"])
def test_silent_sse_is_interrupted_within_two_seconds(mode):
    from bc250_llm_mode.chat_service import ChatSessionService
    from bc250_llm_mode.chat_lifecycle import ChatCancellation, ChatDeadline, ChatResultClassification
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(4)
    entered, release = threading.Event(), threading.Event()
    def backend():
        with listener.accept()[0] as connection:
            connection.settimeout(3)
            request = b""
            while b"\r\n\r\n" not in request:
                request += connection.recv(8192)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            entered.set()
            release.wait(4)
    server = threading.Thread(target=backend, daemon=True)
    server.start()
    token = ChatCancellation()
    results = []
    worker = threading.Thread(target=lambda: results.append(ChatSessionService().stream(
        {"server_port": listener.getsockname()[1], "current_model": "tiny", "current_ctx": 8192},
        [{"role": "user", "content": "test"}], lambda _: None,
        cancellation=token, deadline=ChatDeadline(total_s=0.5 if mode == "deadline" else 5))), daemon=True)
    try:
        worker.start()
        assert entered.wait(2)
        started = time.monotonic()
        if mode == "cancel":
            token.cancel()
        worker.join(2)
        assert not worker.is_alive() and time.monotonic() - started < 2
        assert results[0].classification is (ChatResultClassification.CANCELLED if mode == "cancel" else ChatResultClassification.TIMEOUT)
    finally:
        release.set()
        server.join(2)
        listener.close()
        worker.join(2)


def test_gateway_default_generation_cap_reaches_real_backend():
    import httpx
    from test_gateway_live import _FakeBackend, _start_gateway
    from bc250_llm_mode.gateway import MAX_GENERATED_TOKENS
    backend = _FakeBackend()
    _, server, port = _start_gateway(backend.base_url())
    try:
        response = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Authorization": "Bearer globally-scoped-secret"},
            json={"model": "tiny", "messages": [{"role": "user", "content": "test"}]}, timeout=3)
        assert response.status_code == 200
        assert json.loads(backend.hits[-1][2])["max_tokens"] == MAX_GENERATED_TOKENS
    finally:
        server.shutdown()
        server.server_close()
        backend.shutdown()


@pytest.mark.parametrize("framing", [b"Content-Length: -1\r\n", b"Content-Length: 2\r\nContent-Length: 3\r\n", b"Transfer-Encoding: chunked\r\n"])
def test_real_gateway_rejects_ambiguous_body_before_read(framing):
    from test_gateway_live import _start_gateway
    _, server, port = _start_gateway("http://127.0.0.1:1")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as peer:
            peer.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n" + framing + b"\r\n")
            assert b" 400 " in peer.recv(4096).split(b"\r\n")[0]
    finally:
        server.shutdown()
        server.server_close()


def test_timestamp_free_readiness_does_not_become_fresh():
    from test_appliance_readiness_euf1 import _ready
    value = _ready(model={"installed": True, "service_active": True, "protocol_ready": True, "identity_matches": True})
    assert not value.native_chat_ready


def test_release_jobs_pin_source_and_consume_real_decision():
    from test_release_workflow_policy_g4 import _load_workflow
    workflow = _load_workflow('release.yml')
    for name, job in workflow['jobs'].items():
        for step in job.get('steps', []):
            if 'checkout@' in step.get('uses', '') and name != 'validate-candidate':
                assert step['with']['ref'] == '${{ needs.validate-candidate.outputs.source_sha }}'
            assert '${{ inputs.' not in step.get('run', '')
    text = Path('.github/workflows/release.yml').read_text()
    for required in ('--signer-workflow', '--signer-digest', '--source-digest', '--source-ref', '--output decision/release-decision.json', 'cmp decision/release-decision.json'):
        assert required in text


def test_evaluate_persists_actual_blocked_decision(tmp_path, capsys):
    from tools.release.__main__ import main
    output = tmp_path / 'decision' / 'release-decision.json'
    manifest = output.parent / 'release-manifest.json'
    status = main(['evaluate', '--candidate', '0.9.0.dev0', '--source-commit', 'a'*40,
                  '--artifacts', str(tmp_path / 'artifacts'), '--level', 'final',
                  '--output', str(output), '--manifest-output', str(manifest)])
    assert status == 1
    assert json.loads(capsys.readouterr().out) == json.loads(output.read_text())
    assert json.loads(manifest.read_text())['release_status'] == 'BLOCKED'
    import hashlib
    assert output.with_suffix('.json.sha256').read_text().startswith(hashlib.sha256(output.read_bytes()).hexdigest())


def test_verified_rollback_recovers_death_after_reverse_exchange(tmp_path):
    import sqlite3
    paths = _profile(tmp_path)
    units = UnitOfWorkFactory(paths.database_path)
    adapter = BackupHostAdapter(units, paths)
    _, _, _, record = _create_backup(adapter, paths, units)
    with units.read() as conn:
        digest = conn.execute("SELECT manifest_digest FROM backup_sets WHERE backup_id=?", (record['backup_id'],)).fetchone()[0]
    _seed_operation(units, 'restore-rollback', 'BACKUP_RESTORE')
    ctx = _ctx('restore-rollback', BackupRestoreRequestV1(backup_id=record['backup_id'], confirmation_digest=digest))
    with profile_access(paths.app_dir, exclusive=True):
        adapter.stage_candidate(ctx)
        adapter._exchange = _fake_exchange
        adapter.publish_exchange(ctx)
        with closing(sqlite3.connect(paths.database_path)) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version=14")
            conn.commit()
        def reverse_then_die(active, candidate, **kwargs):
            _fake_exchange(active, candidate, **kwargs)
            raise SimulatedDeath()
        adapter._exchange = reverse_then_die
        with pytest.raises(SimulatedDeath):
            adapter.verify_post_restore(ctx)
        assert read_receipt(paths.app_dir, ctx.operation_id)['phase'] == 'rollback_intent'
        # This raises on a blind second reverse, so success proves identity recovery.
        adapter.verify_post_restore(ctx)
        assert read_receipt(paths.app_dir, ctx.operation_id)['phase'] == 'rolled_back'
        assert adapter.promote_or_rollback(ctx)['disposition'] == 'RESTORE_ROLLED_BACK'


def test_unreadable_conversation_is_visible_and_preserved(tmp_path):
    from bc250_llm_mode.conversation_service import ConversationService
    directory = tmp_path / 'conversations'
    directory.mkdir()
    path = directory / 'broken.json'
    path.write_text('not json')
    rows = ConversationService(directory).list()
    assert rows[0]['invalid'] and rows[0]['conversation_id'] == 'broken'
    assert path.read_text() == 'not json'


@pytest.mark.parametrize('interrupt', [False, True])
def test_ci_inventory_detects_an_omitted_test(tmp_path, interrupt):
    import subprocess
    import sys
    import os
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'test_newly_added.py').write_text('def test_one(): pass\ndef test_two(): pass\n')
    if interrupt:
        (tmp_path / 'conftest.py').write_text('import pytest\ndef pytest_runtest_setup(item):\n    if item.name == "test_two": pytest.exit("synthetic missing execution")\n')
    report = tmp_path / 'inventory.json'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'tools.ci_inventory', '-q', str(tmp_path)],
        cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(root), 'BC250_TEST_INVENTORY': str(report)},
        capture_output=True, timeout=15)
    record = json.loads(report.read_text())
    assert len(record['selected']) == 2
    assert bool(record['missing']) is interrupt
    assert (result.returncode != 0) is interrupt
    assert (record['exit_status'] != 0) is interrupt


def test_applied_idle_policy_survives_unapplied_profile_edit(tmp_path):
    from _native import NativeApp
    from bc250_llm_mode.workload_profiles import WorkloadProfileRepository
    from bc250_llm_mode.repositories import RuntimeConfigRepository
    from bc250_llm_mode.idle_policy import IdlePolicyService
    world = NativeApp(tmp_path)
    units = world.application.units
    with units.begin() as conn:
        profile = WorkloadProfileRepository(conn).create_custom(profile_id='a' * 32, name='Review', context_per_slot=8192, slots=1, idle_policy='STOP_AFTER', stop_after_minutes=5)
        RuntimeConfigRepository(conn).update(model_alias=None, context=8192, slots=1, profile_id=profile.profile_id, profile_revision=profile.revision)
        conn.execute("UPDATE workload_profiles SET idle_policy='KEEP_LOADED', stop_after_minutes=NULL, revision=revision+1 WHERE profile_id=?", (profile.profile_id,))
    decision = IdlePolicyService(units, server_active=lambda: True, stop_server=lambda: {'active': False}, now=lambda: '2026-09-04T01:10:00Z').evaluate(last_request_at='2026-09-04T01:00:00Z', active_requests=0)
    assert decision.policy == 'STOP_AFTER' and decision.action == 'STOP'


def test_gateway_profile_transaction_works_with_read_only_external_lock(tmp_path, monkeypatch):
    import errno
    import os
    from _native import NativeApp
    world = NativeApp(tmp_path)
    units = world.application.units
    profile = units.database_path.parent
    lock = profile.parent / ('.' + profile.name + '-access.lock')
    assert lock.is_file()
    original_open = os.open

    def sandbox_open(path, flags, *args, **kwargs):
        if Path(path) == lock and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise OSError(errno.EROFS, 'Read-only file system')
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', sandbox_open)
    with units.begin() as conn:
        conn.execute('CREATE TABLE sandbox_probe (value INTEGER)')
        conn.execute('INSERT INTO sandbox_probe VALUES (1)')
    with units.read() as conn:
        assert conn.execute('SELECT value FROM sandbox_probe').fetchone()[0] == 1


def test_profile_lock_refuses_fifo_without_waiting_for_a_writer(tmp_path):
    import os
    from bc250_llm_mode.profile_access import profile_access, ProfileBusy
    profile = tmp_path / 'profile'
    profile.mkdir()
    os.mkfifo(tmp_path / '.profile-access.lock')
    with pytest.raises(ProfileBusy, match='regular file'):
        with profile_access(profile):
            raise AssertionError('A special file cannot provide the profile lock')
