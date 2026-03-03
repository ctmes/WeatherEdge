import pytest
import numpy as np
from weatheredge import analytics

def test_brier_skill_score():
    # Perfect model
    assert analytics.brier_skill_score(0, 0.5) == 1.0
    # Baseline model
    assert analytics.brier_skill_score(0.5, 0.5) == 0.0
    # Worse than baseline
    assert analytics.brier_skill_score(0.6, 0.5) == pytest.approx(-0.2)
    # Baseline is zero (perfect)
    assert analytics.brier_skill_score(0.1, 0) == -float('inf')

def test_win_rate_confidence_interval():
    # 50/100 -> ~0.4 to 0.6
    low, high = analytics.win_rate_confidence_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3)
    assert high == pytest.approx(0.5961, abs=1e-3)
    
    # 0/10 -> low should be 0, high should be > 0
    low, high = analytics.win_rate_confidence_interval(0, 10)
    assert low == pytest.approx(0.0)
    assert high > 0
    
    # 10/10 -> high should be 1, low should be < 1
    low, high = analytics.win_rate_confidence_interval(10, 10)
    assert high == pytest.approx(1.0)
    assert low < 1

def test_max_drawdown():
    # Flat
    assert analytics.max_drawdown([100, 100, 100]) == 0.0
    # Simple drop
    assert analytics.max_drawdown([100, 90, 80]) == 0.2
    # Recovery
    assert analytics.max_drawdown([100, 80, 110, 55]) == 0.5 # (110-55)/110
    # Empty
    assert analytics.max_drawdown([]) == 0.0

def test_sharpe_ratio():
    # No volatility -> 0 or error? (We return 0)
    assert analytics.sharpe_ratio([0.01, 0.01, 0.01]) == 0.0
    # Simple case (daily stats are scale by sqrt(365))
    # If daily return is 0.1% constant, mean=0.001, std=0.
    # If we add some noise:
    rets = [0.01, -0.01, 0.01, -0.01]
    sharpe = analytics.sharpe_ratio(rets)
    assert sharpe == 0.0 # mean is 0
    
    rets_pos = [0.02, 0.01, 0.03, 0.02]
    sharpe_pos = analytics.sharpe_ratio(rets_pos)
    assert sharpe_pos > 0

def test_reliability_diagram():
    probs = [0.1, 0.2, 0.8, 0.9]
    wins = [0, 0, 1, 1]
    data = analytics.reliability_diagram_data(probs, wins, n_bins=2)
    # Bin 1: 0.1, 0.2 -> mean 0.15, actual 0
    # Bin 2: 0.8, 0.9 -> mean 0.85, actual 1
    assert len(data) == 2
    assert data[0]['actual_frequency'] == 0
    assert data[1]['actual_frequency'] == 1
    print("All tests passed!")

if __name__ == "__main__":
    print("Running test_brier_skill_score...")
    test_brier_skill_score()
    print("Running test_win_rate_confidence_interval...")
    test_win_rate_confidence_interval()
    print("Running test_max_drawdown...")
    test_max_drawdown()
    print("Running test_sharpe_ratio...")
    test_sharpe_ratio()
    print("Running test_reliability_diagram...")
    test_reliability_diagram()
