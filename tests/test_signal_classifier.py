import pytest

from models.kronos_signal import classify_returns


def test_classify_returns_probabilities_and_score():
    result = classify_returns([0.01, 0.02, -0.01, -0.0001], fee_buffer=0.0025)
    assert result.p_up == pytest.approx(0.5)
    assert result.p_down == pytest.approx(0.25)
    assert result.score == pytest.approx(0.25)
    assert result.expected_return == pytest.approx(0.004975)
    assert result.confidence == pytest.approx(0.5)
    assert set(result.quantiles) == {"q05", "q25", "q50", "q75", "q95"}


def test_classify_returns_rejects_empty():
    with pytest.raises(Exception):
        classify_returns([], fee_buffer=0.001)
