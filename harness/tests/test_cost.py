"""Cost is the list price of what the tool sent, read from tools/<tool>.yaml."""
from harness.adapters import companyenrich, discolike, lusha, seltz
from harness.spec_runner import compute_cost


def test_per_row_tool_billed_on_all_100_rows_sent() -> None:
    assert abs(compute_cost(companyenrich.SPEC, 100, 100) - 4.9) < 1e-9


def test_rows_past_the_depth_asked_for_are_not_billed() -> None:
    assert abs(compute_cost(companyenrich.SPEC, 120, 100) - 4.9) < 1e-9


def test_flat_plus_per_row_tool() -> None:
    assert abs(compute_cost(discolike.SPEC, 100, 100) - (0.18 + 0.35)) < 1e-9


def test_flat_tool_ignores_row_count() -> None:
    assert compute_cost(seltz.SPEC, 100, 100) == 0.005
    assert compute_cost(seltz.SPEC, 7, 100) == 0.005


def test_unpriced_tool_has_no_cost() -> None:
    assert compute_cost(lusha.SPEC, 50, 100) is None
