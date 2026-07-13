"""Smoke tests for frame_state module. Run: python tests/frame_state_smoke.py

Tests pure Python data model operations (no LangGraph required).
Each test validates one piece of the Frame contract.
"""

import sys
from internagents.frame_state import (
    ACTIVE_FRAME_STATUSES,
    TERMINAL_FRAME_STATUSES,
    MAX_FRAME_OBJECTIVE_CHARS,
    create_root_frame,
    create_child_frame,
    FrameValidationError,
    frame_response,
    frame_with_elapsed,
    normalize_frame_state,
    update_frame_status,
    validate_frame_objective,
    validate_token_budget,
    validate_frame_status,
)


def test_blocked_status_valid():
    """validate_frame_status must accept 'blocked' as a valid terminal status."""
    assert validate_frame_status("blocked") == "blocked"
    print("✅ test_blocked_status_valid")


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


def test_validate_frame_objective():
    """validate_frame_objective must trim and reject empty/oversize."""
    assert validate_frame_objective("  hello  ") == "hello"
    try:
        validate_frame_objective("")
        assert False, "should reject empty"
    except FrameValidationError:
        pass
    try:
        validate_frame_objective("x" * (MAX_FRAME_OBJECTIVE_CHARS + 1))
        assert False, "should reject oversize"
    except FrameValidationError:
        pass
    print("✅ test_validate_frame_objective")


def test_validate_token_budget():
    """validate_token_budget must accept positive int, reject others."""
    assert validate_token_budget(None) is None
    assert validate_token_budget(1000) == 1000
    for bad in [0, -1, "1000", 1.5]:
        try:
            validate_token_budget(bad)  # type: ignore
            assert False, f"should reject {bad!r}"
        except FrameValidationError:
            pass
    print("✅ test_validate_token_budget")


def test_root_frame_with_token_budget():
    """create_root_frame must accept token_budget and store in evolution_context."""
    f = create_root_frame(agent_name="main", input_data={"objective": "x"}, token_budget=5000)
    assert f.get("evolution_context", {}).get("token_budget") == 5000
    print("✅ test_root_frame_with_token_budget")


def test_frame_response_shape():
    """frame_response must expose frame + remainingTokens (None when no budget)."""
    f = create_root_frame(agent_name="main")
    resp = frame_response(f)
    assert "frame" in resp and "remainingTokens" in resp
    assert resp["remainingTokens"] is None, "no budget → remainingTokens None"

    f2 = create_root_frame(agent_name="main", token_budget=1000)
    resp2 = frame_response(f2)
    assert resp2["remainingTokens"] == 1000, "no tokens used → full budget remains"
    print("✅ test_frame_response_shape")


def test_status_sets_disjoint():
    """ACTIVE and TERMINAL frame status sets must be disjoint."""
    assert ACTIVE_FRAME_STATUSES & TERMINAL_FRAME_STATUSES == set(), \
        "active and terminal status sets must not overlap"
    assert "running" in ACTIVE_FRAME_STATUSES
    assert "blocked" in TERMINAL_FRAME_STATUSES
    print("✅ test_status_sets_disjoint")


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
        test_blocked_status_valid,
        test_root_frame_created,
        test_child_frame_derived,
        test_invalid_agent_name,
        test_normalize_frame_state_valid,
        test_normalize_frame_state_invalid,
        test_validate_frame_objective,
        test_validate_token_budget,
        test_root_frame_with_token_budget,
        test_frame_response_shape,
        test_status_sets_disjoint,
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
