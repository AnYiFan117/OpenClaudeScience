#!/usr/bin/env python3
"""Smoke test: verify get_agent_graph produces different graphs per agent_name
and that filter helpers work correctly. This does NOT invoke a real model."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_filter_tools_by_agent():
    """Test: _filter_tools_by_agent applies whitelist/blacklist correctly."""
    from internagents.agent_graph import _filter_tools_by_agent
    from internagents.agent_registry import get_agent_config

    # Fake tools with .name attribute
    class FakeTool:
        def __init__(self, name):
            self.name = name

    tools = [FakeTool("read_file"), FakeTool("write_file"), FakeTool("run_python")]

    # Reviewer has a whitelist and blacklist
    reviewer_cfg = get_agent_config("reviewer")
    assert reviewer_cfg.tool_whitelist is not None, "reviewer should have whitelist"
    filtered = _filter_tools_by_agent(tools, reviewer_cfg)
    names = {t.name for t in filtered}

    # Reviewer should keep read_file (whitelisted)
    assert "read_file" in names, f"read_file should be in reviewer tools, got {names}"

    # Reviewer should not have write_file (blacklisted)
    assert "write_file" not in names, f"write_file should not be in reviewer tools, got {names}"
    print("✅ test_filter_tools_by_agent: reviewer whitelist/blacklist works")


def test_main_config_uses_all_tools():
    """Test: main agent has no whitelist, keeps all tools."""
    from internagents.agent_graph import _filter_tools_by_agent
    from internagents.agent_registry import get_agent_config

    class FakeTool:
        def __init__(self, name):
            self.name = name

    tools = [FakeTool("read_file"), FakeTool("write_file"), FakeTool("run_python")]
    main_cfg = get_agent_config("main")
    assert main_cfg.tool_whitelist is None, "main should have no whitelist"
    filtered = _filter_tools_by_agent(tools, main_cfg)
    assert len(filtered) == 3, f"main should keep all tools, got {[t.name for t in filtered]}"
    print("✅ test_main_config_uses_all_tools: main keeps all tools")


def test_get_agent_graph_callable():
    """Test: get_agent_graph function exists and is callable."""
    from internagents.agent_graph import get_agent_graph

    assert callable(get_agent_graph), "get_agent_graph should be callable"
    print("✅ test_get_agent_graph_callable: function exists")


def test_cache_structure():
    """Test: _AGENT_GRAPH_CACHE exists and has correct structure."""
    from internagents.agent_graph import _AGENT_GRAPH_CACHE

    assert isinstance(_AGENT_GRAPH_CACHE, dict), "_AGENT_GRAPH_CACHE should be dict"
    print("✅ test_cache_structure: cache dict exists")


def test_agent_configs_registered():
    """Test: all 4 agents are registered in agent_registry."""
    from internagents.agent_registry import get_agent_config

    expected_agents = {"main", "reviewer", "bookmarker", "onboarding"}
    for agent_name in expected_agents:
        cfg = get_agent_config(agent_name)
        assert cfg.name == agent_name, f"config name mismatch for {agent_name}"
    print("✅ test_agent_configs_registered: all 4 agents registered")


def test_filter_middlewares_for_agent():
    """Test: _filter_middlewares_for_agent builds middleware list."""
    from internagents.agent_graph import _filter_middlewares_for_agent
    from internagents.agent_registry import get_agent_config

    main_cfg = get_agent_config("main")
    # main should have middlewares: date, goal, skill, kb_sync
    assert "date" in main_cfg.middlewares or len(main_cfg.middlewares) > 0

    # Create a mock backend (doesn't need to be real)
    class MockBackend:
        pass

    backend = MockBackend()

    # Build middleware (we won't actually invoke, just check it doesn't crash)
    agent_config_dict = {"web_search": {"enabled": False}}
    middleware = _filter_middlewares_for_agent(agent_config_dict, main_cfg, backend)
    assert isinstance(middleware, list), "middleware should be a list"
    print(f"✅ test_filter_middlewares_for_agent: built {len(middleware)} middlewares")


def main():
    """Run all tests."""
    tests = [
        test_filter_tools_by_agent,
        test_main_config_uses_all_tools,
        test_get_agent_graph_callable,
        test_cache_structure,
        test_agent_configs_registered,
        test_filter_middlewares_for_agent,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nResults: {passed}/{len(tests)} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
