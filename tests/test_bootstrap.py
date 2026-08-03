from bc250_llm_mode.bootstrap import ACKS, terminal_acknowledgment


def test_bootstrap_requires_all_three_distinct_acknowledgments():
    assert len(ACKS) == 3
    assert len(set(ACKS)) == 3
    assert any("hot" in item for item in ACKS)
    assert any("40 CUs" in item for item in ACKS)
    assert any("12 GiB" in item for item in ACKS)


def test_terminal_bootstrap_requires_every_exact_confirmation():
    state = {}
    answers = iter(["YES", "YES", "YES", "I ACCEPT"])
    assert terminal_acknowledgment(state, input_fn=lambda _prompt: next(answers), output_fn=lambda _line: None)
    assert state["disclaimer_ack"] is True


def test_terminal_bootstrap_rejects_partial_confirmation():
    state = {}
    answers = iter(["YES", "no"])
    assert not terminal_acknowledgment(
        state, input_fn=lambda _prompt: next(answers), output_fn=lambda _line: None
    )
    assert not state.get("disclaimer_ack")
