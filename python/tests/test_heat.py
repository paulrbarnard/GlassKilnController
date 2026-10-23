"""The solenoid heat budget: 5% duty in 60 s at 12 V."""

import pytest

from kiln.bridge import BridgeError, DriveSettings, HeatBudget
from kiln.protocol import Key

ENTER = DriveSettings(kick_ms=100, hold_duty=240, settle_ms=200, long_hold_duty=80)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_costs():
    tap = HeatBudget.cost(ENTER, 200)  # 0.1 s full + 0.1 s at 240
    assert tap == pytest.approx(0.1 + 0.1 * (240 / 255) ** 2)
    long = HeatBudget.cost(ENTER, 5000)  # + 4.8 s at 80
    assert long == pytest.approx(tap + 4.8 * (80 / 255) ** 2)
    full_5s = HeatBudget.cost(DriveSettings(100, 255, 200, 255), 5000)
    assert full_5s == pytest.approx(5.0)


def test_waits_for_the_coil_to_cool():
    clock = Clock()
    budget = HeatBudget(clock=clock)
    cost = HeatBudget.cost(ENTER, 5000)  # ~0.66
    for _ in range(4):
        assert budget.wait_s(Key.ENTER, cost) == 0
        budget.add(Key.ENTER, cost)
    # 4 x 0.66 = 2.64; another would reach 3.3 > 3.0: wait ~6 s
    assert budget.wait_s(Key.ENTER, cost) == pytest.approx((4 * cost + cost - 3.0) / 0.05)
    clock.t += 60  # a minute later it has drained 3.0
    assert budget.wait_s(Key.ENTER, cost) == 0
    assert budget.wait_s(Key.UP, cost) == 0  # each coil has its own budget


def test_a_press_that_can_never_fit_is_refused():
    with pytest.raises(BridgeError, match="can never fit"):
        HeatBudget().wait_s(Key.UP, HeatBudget.cost(DriveSettings(100, 255, 200, 255), 5000))
