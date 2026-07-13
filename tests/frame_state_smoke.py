"""Smoke tests for frame_state module. Run: python tests/frame_state_smoke.py

Tests pure Python data model operations (no LangGraph required).
Each test validates one piece of the Frame contract.
"""

import sys
from internagents.frame_state import (
    create_root_frame,
    create_child_frame,
    FrameValidationError,
    normalize_frame_state,
    goal_from_frame,
    frame_from_goal,
    update_frame_status,
    frame_with_elapsed,
)


def test_root_frame_created():
    """Root frame must have id == root_frame_id and no parent."""
    f = create_root_frame(agent_name="main", input_data={"query": "hi"})
    assert f["id"] == f["root_frame_id"], "root_frame_id must equal id"
    assert f.get("parent_frame_id") is None, "root frame must have no parent"
    assert f["agent_name"] == "main", "agent_name mismatch"
    assert f["status"] == "pending", "initial status must be pending"
    assert f["tokens_used"] == 0, "new frame should have 0 tokens"
    assert isinstance(f["messages"], list), "messages must be list"
    assert f["input_data"] == {"query": "hi"}, "input_data mismatch"
    print("✅ test_root_frame_created")


def test_child_frame_derived():
    """Child frame must inherit root_frame_id and set parent_frame_id."""
    parent = create_root_frame(agent_name="main", input_data={})
    child = create_child_frame(
        parent, agent_name="reviewer", input_data={"target": parent["id"]}
    )
    assert child["id"] != parent["id"], "child must have different id"
    assert child["parent_frame_id"] == parent["id"], "parent_frame_id mismatch"
    assert (
        child["root_frame_id"] == parent["root_frame_id"]
    ), "root_frame_id must be inherited"
    assert child["agent_name"] == "reviewer", "agent_name mismatch"
    assert child["status"] == "pending", "new child must be pending"
    assert child["input_data"] == {"target": parent["id"]}, "input_data mismatch"
    print("✅ test_child_frame_derived")


def test_invalid_agent_name():
    """Invalid agent names must raise validation error."""
    try:
        create_root_frame(agent_name="invalid_agent")  # type: ignore
        assert False, "should have raised FrameValidationError"
    except FrameValidationError as e:
        assert "agent_name" in str(e).lower(), f"error message missing 'agent_name': {e}"
    print("✅ test_invalid_agent_name")


def test_normalize_frame_state_valid():
    """normalize_frame_state must preserve valid frame data."""
    f = create_root_frame(agent_name="main", input_data={"x": 1})
    normalized = normalize_frame_state(f)
    assert normalized is not None, "normalized must not be None"
    assert normalized["id"] == f["id"], "id mismatch after normalize"
    assert normalized["root_frame_id"] == f["root_frame_id"], "root_frame_id mismatch"
    assert normalized["agent_name"] == f["agent_name"], "agent_name mismatch"
    assert normalized["input_data"] == f["input_data"], "input_data mismatch"
    print("✅ test_normalize_frame_state_valid")


def test_normalize_frame_state_invalid():
    """normalize_frame_state must return None for invalid input."""
    assert normalize_frame_state(None) is None, "None should return None"
    assert normalize_frame_state("bad") is None, "string should return None"
    assert normalize_frame_state(123) is None, "int should return None"
    assert normalize_frame_state({}) is None, "empty dict should return None"
    assert (
        normalize_frame_state({"id": "x", "root_frame_id": "y", "agent_name": "bad", "status": "pending"})
        is None
    ), "invalid agent_name should return None"
    print("✅ test_normalize_frame_state_invalid")


def test_goal_bridge_frame_to_goal():
    """goal_from_frame must convert Frame to compatible Goal."""
    f = create_root_frame(
        agent_name="main", input_data={"objective": "solve problem"}
    )
    goal = goal_from_frame(f)
    assert goal is not None, "goal must not be None"
    assert goal["id"] == f["id"], "id must match"
    assert "solve problem" in goal["objective"], "objective must include input_data"
    assert goal.get("threadId") == f["root_frame_id"], "threadId should be root_frame_id"
    print("✅ test_goal_bridge_frame_to_goal")


def test_goal_bridge_goal_to_frame():
    """frame_from_goal must convert Goal to Frame."""
    from internagents.goal_state import create_goal_state

    goal = create_goal_state("find answer", token_budget=5000)
    frame = frame_from_goal(goal)
    assert frame is not None, "frame must not be None"
    assert frame["id"] == goal["id"], "id must match"
    assert "find answer" in str(frame.get("input_data", {})), "objective should be in input_data"
    print("✅ test_goal_bridge_goal_to_frame")


def test_update_frame_status():
    """update_frame_status must change status and update timestamp."""
    f = create_root_frame(agent_name="main")
    f_running = update_frame_status(f, "running")
    assert f_running["status"] == "running", "status must be updated"
    assert f_running["updated_at"] >= f["created_at"], "updated_at must be >= created_at"
    assert f_running["id"] == f["id"], "id must not change"
    print("✅ test_update_frame_status")


def test_frame_with_elapsed():
    """frame_with_elapsed must calculate time_used_seconds."""
    f = create_root_frame(agent_name="main")
    f_running = update_frame_status(f, "running")
    f_elapsed = frame_with_elapsed(f_running, now=f_running["created_at"] + 10)
    assert f_elapsed["time_used_seconds"] >= 10, "time_used_seconds must be >= 10"
    print("✅ test_frame_with_elapsed")


def test_frame_with_elapsed_terminal():
    """frame_with_elapsed must not change terminal frames."""
    f = create_root_frame(agent_name="main")
    f_completed = update_frame_status(f, "completed", now=f["created_at"] + 5)
    f_elapsed = frame_with_elapsed(f_completed, now=f["created_at"] + 100)
    assert (
        f_elapsed["time_used_seconds"] == f_completed["time_used_seconds"]
    ), "terminal frame time must not change"
    print("✅ test_frame_with_elapsed_terminal")


def test_multiple_children_same_root():
    """Multiple children from same parent must share root_frame_id."""
    parent = create_root_frame(agent_name="main")
    child1 = create_child_frame(parent, agent_name="reviewer")
    child2 = create_child_frame(parent, agent_name="bookmarker")
    assert (
        child1["root_frame_id"] == child2["root_frame_id"] == parent["root_frame_id"]
    ), "all siblings must share root_frame_id"
    assert child1["id"] != child2["id"], "siblings must have different ids"
    assert (
        child1["parent_frame_id"] == child2["parent_frame_id"] == parent["id"]
    ), "all siblings must have same parent"
    print("✅ test_multiple_children_same_root")


def test_system_prompt_optional():
    """system_prompt field must be optional but preservable."""
    f1 = create_root_frame(agent_name="main")
    assert "system_prompt" not in f1, "system_prompt not in frame by default"

    f2 = create_root_frame(agent_name="main", system_prompt="custom prompt")
    assert f2.get("system_prompt") == "custom prompt", "system_prompt must be stored"
    print("✅ test_system_prompt_optional")


def run_all_tests():
    """Run all smoke tests."""
    tests = [
        test_root_frame_created,
        test_child_frame_derived,
        test_invalid_agent_name,
        test_normalize_frame_state_valid,
        test_normalize_frame_state_invalid,
        test_goal_bridge_frame_to_goal,
        test_goal_bridge_goal_to_frame,
        test_update_frame_status,
        test_frame_with_elapsed,
        test_frame_with_elapsed_terminal,
        test_multiple_children_same_root,
        test_system_prompt_optional,
    ]

    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"❌ {test.__name__}: {e}")
            failed.append((test.__name__, str(e)))
        except Exception as e:
            print(f"❌ {test.__name__}: Unexpected error: {e}")
            failed.append((test.__name__, f"Unexpected: {e}"))

    print(f"\n{'='*60}")
    print(f"Results: {len(tests) - len(failed)}/{len(tests)} tests passed")

    if failed:
        print(f"\nFailed tests:")
        for name, error in failed:
            print(f"  - {name}: {error}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
