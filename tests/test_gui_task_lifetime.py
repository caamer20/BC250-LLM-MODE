"""Worker completion and queue pressure must release UI owners on the UI thread."""
import gc
import queue
import threading
import time

from bc250_llm_mode.gui.tasks import BoundedTaskLane, TaskLanes


def test_cyclic_ui_owners_are_collected_on_ui_thread_not_allocating_worker():
    finalized = []
    owner = threading.get_ident()
    enabled = gc.isenabled()
    thresholds = gc.get_threshold()
    class UIReference:
        def __del__(self):
            finalized.append(threading.get_ident())
    lanes = TaskLanes()
    try:
        value = UIReference()
        value.cycle = value
        del value
        gc.set_threshold(1, 1, 1)
        def allocate():
            garbage = []
            for _ in range(10000):
                child = []
                child.append(child)
                garbage.append(child)
            return len(garbage)
        assert lanes.action.submit(1, allocate)
        assert lanes.results.get(timeout=2).value == 10000
        assert not finalized
        lanes.close()
        assert finalized == [owner]
        assert gc.isenabled() == enabled
    finally:
        lanes.close()
        gc.set_threshold(*thresholds)


def test_close_retains_running_ui_owners_until_main_thread_reaps_them():
    started, finish = threading.Event(), threading.Event()
    finalized = []
    owner = threading.get_ident()
    enabled = gc.isenabled()
    class UIReference:
        def __del__(self):
            finalized.append(threading.get_ident())
    lanes = TaskLanes()
    def submit():
        reference = UIReference()
        def work():
            started.set()
            finish.wait(timeout=2)
            return reference
        assert lanes.action.submit(1, work)
    try:
        submit()
        assert started.wait(timeout=1)
        lanes.close()
        assert not finalized and not gc.isenabled()
        finish.set()
        lanes.action._thread.join(timeout=1)
        assert not lanes.action._thread.is_alive()
        assert not finalized
        lanes.close()
        assert finalized == [owner]
        assert gc.isenabled() == enabled
    finally:
        finish.set()
        lanes.action._thread.join(timeout=1)
        lanes.close()


def test_completed_task_closures_are_finalized_on_submitting_thread():
    completed = []
    owner_thread = threading.get_ident()
    class UIReference:
        def __del__(self):
            completed.append(threading.get_ident())
    results = queue.Queue(1)
    lane = BoundedTaskLane("test", results)
    def submit():
        reference = UIReference()
        def work():
            return lambda: reference
        assert lane.submit(1, work)
    try:
        submit()
        result = results.get(timeout=1)
        # Replace the worker's prior loop task; the old implementation released
        # its last closure here on the worker and triggered Tk finalizer errors.
        while not lane.submit(2, lambda: "next"):
            time.sleep(0.001)
        del result
        results.get(timeout=1)
        lane.close()
        gc.collect()
        assert completed == [owner_thread]
        assert not lane._owners
    finally:
        lane.close()


def test_full_queue_does_not_evict_ui_callbacks_on_worker():
    finalized = []
    owner_thread = threading.get_ident()
    class UIReference:
        def __del__(self):
            finalized.append(threading.get_ident())
    results = queue.Queue(1)
    lane = BoundedTaskLane("test", results)
    reference = UIReference()
    fn = lambda value=reference: (lambda: value)
    try:
        assert lane.submit(1, fn)
        del fn, reference
        until = time.monotonic() + 1
        while results.empty() and time.monotonic() < until:
            time.sleep(0.001)
        while not lane.submit(2, lambda: "blocked behind queue"):
            time.sleep(0.001)
        time.sleep(0.08)
        assert not finalized
        first = results.get(timeout=1)
        assert first.generation == 1
        del first
        second = results.get(timeout=1)
        assert second.value == "blocked behind queue"
        lane.close()
        gc.collect()
        assert finalized == [owner_thread]
    finally:
        lane.close()
