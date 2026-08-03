from bc250_llm_mode.disclaimer import DISCLAIMER_TEXT, acknowledgment_valid


def test_exact_acceptance_and_required_warning_topics():
    assert acknowledgment_valid(True, True, True, "I ACCEPT")
    assert not acknowledgment_valid(True, True, True, "i accept")
    assert not acknowledgment_valid(True, False, True, "I ACCEPT")
    assert "INCREDIBLY HOT" in DISCLAIMER_TEXT
    assert "UNLOCK ALL 40 COMPUTE UNITS" in DISCLAIMER_TEXT
    assert "~12 GiB for the GPU and ~4 GiB" in DISCLAIMER_TEXT

