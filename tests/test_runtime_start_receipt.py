"""Regressions for asynchronous launch receipts and verified reconciliation."""
import json
from types import SimpleNamespace
import pytest
from bc250_llm_mode.runtime_lifecycle_adapter import RuntimeLifecycleHostAdapter
from bc250_llm_mode.runtime_lifecycle_command import RuntimeLifecycleCommandService
from bc250_llm_mode.runtime_handoff import RuntimeHandoffRenderer, build_payload
from bc250_llm_mode.operations.recovery import RecoveryClass
from bc250_llm_mode.operations.workflow import StepFailure, ProbeResult
from bc250_llm_mode.operations.repositories import OperationRepository, LeaseRepository, EventRepository
from bc250_llm_mode.operations.model import OperationState
from bc250_llm_mode.db import initialize_and_close
from bc250_llm_mode.unit_of_work import UnitOfWorkFactory


def receipt_adapter(monkeypatch, receipts):
    adapter = object.__new__(RuntimeLifecycleHostAdapter)
    adapter._require_seams = lambda: None
    adapter._crash_point = lambda *args: None
    adapter._view_for_build = lambda *args: {}
    adapter.observe_invocation = lambda *args, **kwargs: ProbeResult(RecoveryClass.ABSENT, 'pending')
    starts=[]
    adapter.server_port = SimpleNamespace(restart=lambda view: starts.append(True))
    iterator=iter(receipts)
    adapter._read_receipt = lambda: next(iterator)
    monkeypatch.setattr('time.sleep', lambda _: None)
    return adapter,starts


def test_restart_waits_for_delayed_launcher_receipt(monkeypatch):
    valid={'build_id':'target','operation_id':'operation','nonce':'new'}
    adapter,starts=receipt_adapter(monkeypatch,[{'nonce':'old'}, {}, {'build_id':'target','operation_id':'operation','nonce':'old'}, valid])
    result=adapter.restart_for_runtime_change(None,'target','operation',mode='update')
    assert starts==[True] and result.receipt_present and result.invocation_nonce=='new'


def test_missing_receipt_has_bounded_failure(monkeypatch):
    adapter,starts=receipt_adapter(monkeypatch,[{}, {}])
    times=iter([0,21]);monkeypatch.setattr('time.monotonic',lambda:next(times))
    with pytest.raises(StepFailure,match='SERVICE_START_RECEIPT_TIMEOUT'):
        adapter.restart_for_runtime_change(None,'target','operation',mode='update')
    assert starts==[True]


def test_restoration_accepts_only_current_rendering_with_identical_launch_fields(tmp_path):
    adapter=object.__new__(RuntimeLifecycleHostAdapter)
    view={'current_model':'fixture','current_ctx':8192,'revision':2,'server_port':8080,
          'llama_cpp_path':'/root/llama.cpp','optimizations':{'parallel_slots':1},
          'installed_models':[{'id':'fixture','path':'/fixture.gguf'}]}
    adapter.state_supplier=lambda:dict(view)
    adapter.renderer=RuntimeHandoffRenderer(tmp_path)
    current=build_payload(view,config_revision=2)
    prior={**current,'config_revision':1,'runtime_fingerprint':'prior-derived-metadata'}
    snapshot=SimpleNamespace(handoff_payload=prior)
    adapter.renderer.restore_snapshot(current)
    assert adapter._restored_handoff_matches(snapshot)
    for key,value in [('ctx_total',4096),('model_path','/foreign.gguf'),('runtime_fingerprint','invented')]:
        adapter.renderer.restore_snapshot({**current,key:value})
        assert not adapter._restored_handoff_matches(snapshot)


@pytest.mark.parametrize('proven,stale', [(True,False),(False,False),(True,True)])
def test_reconcile_requires_real_proof_and_revision_and_keeps_failed_history(tmp_path,proven,stale):
    database=tmp_path/'state.db';initialize_and_close(database);units=UnitOfWorkFactory(database)
    with units.begin() as conn:
        ops=OperationRepository(conn)
        row=ops.create(operation_type='RUNTIME_UPDATE',request={},surface='test')
        for state in ['PREPARING','RUNNING','RECOVERY_REQUIRED']:
            row=ops.compare_and_transition(row.id,expected_state=row.state,expected_revision=row.state_revision,target_state=state)
        LeaseRepository(conn).acquire('runtime-active',operation_id=row.id,owner='fixture')
    calls=[]
    def verify(identifier):calls.append(identifier);return proven
    service=RuntimeLifecycleCommandService(units=units,enqueue=None,engine_factory=None,restoration_verifier=verify)
    outcome=service.reconcile_restored(row.id,expected_revision=row.state_revision-int(stale))
    with units.read() as conn:
        assert OperationRepository(conn).require(row.id)==row
        held=LeaseRepository(conn).leases_for_operation(row.id)
        events=conn.execute("SELECT COUNT(*) FROM operation_events WHERE operation_id=? AND code='RUNTIME_RESTORATION_RECONCILED'",(row.id,)).fetchone()[0]
    assert (not held)==(proven and not stale)
    assert events==int(proven and not stale)
    assert outcome.ok==(proven and not stale)
    assert calls==([] if stale else [row.id])
