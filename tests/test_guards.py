from codingagent.agent.guards import LoopGuard, TokenBudget
from codingagent.domain.models import Action
from codingagent.domain.enums import ActionType


def _action(name: ActionType, **args) -> Action:
    return Action(action=name, args=args, reason="test")


def test_repeated_identical_call_trips_guard():
    guard = LoopGuard()
    action = _action(ActionType.READ_FILE, path="a.py")
    assert not guard.observe(action)
    assert guard.observe(action)


def test_different_args_reset_counter():
    guard = LoopGuard()
    assert not guard.observe(_action(ActionType.READ_FILE, path="a.py"))
    assert not guard.observe(_action(ActionType.READ_FILE, path="b.py"))
    assert not guard.observe(_action(ActionType.READ_FILE, path="a.py"))


def test_different_tool_same_args_reset_counter():
    guard = LoopGuard()
    assert not guard.observe(_action(ActionType.READ_FILE, path="a.py"))
    assert not guard.observe(_action(ActionType.GREP, path="a.py"))


def test_token_budget():
    budget = TokenBudget(100)
    assert not budget.exceeded
    budget.charge(60)
    assert budget.remaining == 40
    budget.charge(50)
    assert budget.exceeded
    assert budget.remaining == 0
