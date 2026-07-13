"""Integration smoke tests: verify Frame is the source of truth end-to-end."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internagents.frame_state import create_root_frame


def test_create_root_frame_accepts_frame_id():
    """create_root_frame must support explicit frame_id (for LangGraph thread pinning)."""
    f = create_root_frame(agent_name="main", input_data={}, frame_id="custom-id-123")
    assert f["id"] == "custom-id-123", f"Expected frame_id='custom-id-123', got {f['id']}"
    assert f["root_frame_id"] == "custom-id-123", f"Expected root_frame_id='custom-id-123', got {f['root_frame_id']}"
    print("✅ create_root_frame accepts frame_id and sets root_frame_id correctly")


def test_frame_middleware_importable():
    """frame_middleware module must expose FrameContextMiddleware and FrameRootMiddleware."""
    from internagents.frame_middleware import FrameContextMiddleware, FrameRootMiddleware
    assert FrameContextMiddleware is not None
    assert FrameRootMiddleware is not None
    print("✅ FrameContextMiddleware, FrameRootMiddleware are importable")


def test_frame_tools_registered():
    """frame_tools() must return exactly two tools."""
    from internagents.frame_tools import frame_tools
    tools = frame_tools()
    names = [t.name for t in tools]
    assert names == ["get_frame", "update_frame"], f"unexpected tool names: {names}"
    print("✅ frame_tools returns get_frame / update_frame")


def test_agent_registry_uses_frame_middleware():
    """agent_registry must reference 'frame' middleware, not 'goal'."""
    from internagents.agent_registry import AGENT_CONFIGS
    main_mw = AGENT_CONFIGS["main"].middlewares
    assert "frame" in main_mw, f"'frame' must be in main middlewares: {main_mw}"
    assert "goal" not in main_mw, f"'goal' must not be in main middlewares: {main_mw}"
    print("✅ agent_registry.AGENT_CONFIGS['main'].middlewares uses 'frame'")


def test_get_agent_graph_cache_accessible():
    """_AGENT_GRAPH_CACHE should be accessible (even if empty)."""
    from internagents.agent_graph import _AGENT_GRAPH_CACHE
    assert isinstance(_AGENT_GRAPH_CACHE, dict), f"_AGENT_GRAPH_CACHE should be a dict, got {type(_AGENT_GRAPH_CACHE)}"
    print("✅ _AGENT_GRAPH_CACHE is accessible as a dict")


def test_agent_graph_no_goal_imports():
    """agent_graph.py must not import from goal_* modules."""
    import inspect
    from internagents import agent_graph
    source = inspect.getsource(agent_graph)
    assert "from internagents.goal_" not in source, \
        "agent_graph.py must not import from internagents.goal_*"
    assert "from internagents.frame_middleware import" in source, \
        "agent_graph.py must import from frame_middleware"
    assert "from internagents.frame_tools import" in source, \
        "agent_graph.py must import from frame_tools"
    print("✅ agent_graph.py has no goal_* imports, uses frame_* only")


if __name__ == "__main__":
    tests = [
        test_create_root_frame_accepts_frame_id,
        test_frame_middleware_importable,
        test_frame_tools_registered,
        test_agent_registry_uses_frame_middleware,
        test_get_agent_graph_cache_accessible,
        test_agent_graph_no_goal_imports,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n✅ Results: {passed}/{len(tests)} tests passed")
    if failed > 0:
        print(f"❌ {failed} tests failed")
        sys.exit(1)
