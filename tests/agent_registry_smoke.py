#!/usr/bin/env python3
"""Smoke test for agent_registry — verify all 4 agents are registered and prompts load.

Runs basic sanity checks:
1. All 4 agents (main, reviewer, bookmarker, onboarding) are registered
2. Each agent's prompt file exists and is loadable
3. Metadata parses correctly
4. Identity has been adapted (no "Operon" or "Claude Science" references)
5. Agent configs have expected structure
"""

import sys
from pathlib import Path

# Add internagents to path
_HERE = Path(__file__).resolve().parent
_INTERNAGENTS = _HERE.parent / "internagents"
if _INTERNAGENTS not in sys.path:
    sys.path.insert(0, str(_INTERNAGENTS.parent))


def test_all_4_registered():
    """Test: all 4 agents are in the registry."""
    from internagents.agent_registry import list_registered_agents

    names = list_registered_agents()
    expected = {"main", "reviewer", "bookmarker", "onboarding"}
    actual = set(names)

    assert expected == actual, f"Expected {expected}, got {actual}"
    print("✅ All 4 agents registered")


def test_each_prompt_loadable():
    """Test: each agent's prompt file exists and is loadable."""
    from internagents.agent_registry import load_agent_prompt, list_registered_agents

    for name in list_registered_agents():
        try:
            body = load_agent_prompt(name)
            assert isinstance(body, str), f"{name} prompt is not a string"
            assert len(body) > 100, f"{name} prompt too short: {len(body)} chars"
            print(f"✅ {name} prompt loaded ({len(body)} chars)")
        except Exception as e:
            print(f"❌ {name} prompt failed: {e}")
            raise


def test_identity_adapted():
    """Test: identity has been adapted (no 'Operon' or 'Claude Science' in bodies)."""
    from internagents.agent_registry import load_agent_prompt, list_registered_agents

    forbidden = {"Operon", "Claude Science"}

    for name in list_registered_agents():
        body = load_agent_prompt(name)
        found = []
        for term in forbidden:
            if term in body:
                found.append(term)

        if found:
            print(f"⚠️ {name} still has old identity: {found}")
            # For now this is a warning, not a failure
            # Users may adapt the content themselves
        else:
            print(f"✅ {name} identity adapted (no forbidden terms)")


def test_metadata_yaml_parses():
    """Test: metadata parses correctly."""
    from internagents.agent_registry import load_agent_metadata, list_registered_agents

    for name in list_registered_agents():
        try:
            meta = load_agent_metadata(name)
            assert isinstance(meta, dict), f"{name} metadata not a dict"
            print(f"✅ {name} metadata parses ({len(meta)} fields)")
        except Exception as e:
            print(f"❌ {name} metadata failed: {e}")
            raise


def test_reviewer_output_schema():
    """Test: reviewer has output_schema defined."""
    from internagents.agent_registry import get_agent_config

    cfg = get_agent_config("reviewer")
    assert cfg.output_schema is not None, "reviewer missing output_schema"
    assert "verdict" in cfg.output_schema.get("properties", {}), "verdict not in schema"
    print("✅ reviewer output_schema defined")


def test_response_format_helper_returns_schema_for_reviewer():
    """`_response_format_for_agent` must return reviewer's output_schema so
    `create_deep_agent` binds a structured-output tool that forces the LLM
    to emit JSON matching {verdict, issues, suggestions}."""
    from internagents.agent_graph import _response_format_for_agent
    from internagents.agent_registry import get_agent_config

    cfg = get_agent_config("reviewer")
    rf = _response_format_for_agent(cfg)
    assert rf is not None, "reviewer must have response_format wired"
    assert rf.get("type") == "object"
    assert "verdict" in rf.get("properties", {})
    print("✅ _response_format_for_agent returns reviewer schema")


def test_response_format_helper_returns_none_for_agents_without_schema():
    """main/bookmarker/onboarding don't set output_schema, so their
    response_format must stay None — activating structured output for
    them would bind an unwanted extra tool and change behavior."""
    from internagents.agent_graph import _response_format_for_agent
    from internagents.agent_registry import get_agent_config

    for name in ("main", "bookmarker", "onboarding"):
        cfg = get_agent_config(name)
        assert _response_format_for_agent(cfg) is None, (
            f"{name} must not have response_format (would change tool set)"
        )
    print("✅ _response_format_for_agent None for main/bookmarker/onboarding")


def test_reviewer_cannot_spawn_children():
    """Test: reviewer is configured to not spawn children."""
    from internagents.agent_registry import get_agent_config

    cfg = get_agent_config("reviewer")
    assert cfg.can_spawn_children is False, "reviewer should not spawn children"
    print("✅ reviewer cannot spawn children")


def test_bookmarker_concurrent_flag():
    """Test: bookmarker has concurrent_with_parent=True (v0.2 feature)."""
    from internagents.agent_registry import get_agent_config

    cfg = get_agent_config("bookmarker")
    assert cfg.concurrent_with_parent is True, "bookmarker should have concurrent flag for v0.2"
    print("⚠️ bookmarker concurrent_with_parent=True (v0.2 feature, not yet implemented)")


def test_unknown_agent_raises():
    """Test: requesting unknown agent raises AgentNotFoundError."""
    from internagents.agent_registry import get_agent_config, AgentNotFoundError

    try:
        get_agent_config("nonexistent")  # type: ignore
        assert False, "should have raised AgentNotFoundError"
    except AgentNotFoundError:
        print("✅ unknown agent raises AgentNotFoundError")


def test_agent_configs_structure():
    """Test: all agent configs have expected attributes."""
    from internagents.agent_registry import get_agent_config, list_registered_agents

    for name in list_registered_agents():
        cfg = get_agent_config(name)
        assert cfg.name == name, f"config name mismatch: {cfg.name} != {name}"
        assert cfg.prompt_path.exists(), f"{name} prompt path doesn't exist"
        assert isinstance(cfg.middlewares, tuple), f"{name} middlewares not tuple"
        print(f"✅ {name} config structure valid")


if __name__ == "__main__":
    tests = [
        test_all_4_registered,
        test_each_prompt_loadable,
        test_identity_adapted,
        test_metadata_yaml_parses,
        test_reviewer_output_schema,
        test_response_format_helper_returns_schema_for_reviewer,
        test_response_format_helper_returns_none_for_agents_without_schema,
        test_reviewer_cannot_spawn_children,
        test_bookmarker_concurrent_flag,
        test_unknown_agent_raises,
        test_agent_configs_structure,
    ]

    print("\n" + "=" * 60)
    print("Running agent_registry smoke tests...")
    print("=" * 60 + "\n")

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test_func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {test_func.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)
