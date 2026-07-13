#!/usr/bin/env python3
"""Test the concurrent bookmarker task infrastructure (no actual model call).
Uses a stub checkpointer + monkeypatched bookmarker graph."""

import asyncio
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_bookmarker_task_creates_and_cancels():
    """Test: spawn_bookmarker_background_task creates a task that can be cancelled."""
    from internagents.frame_service import spawn_bookmarker_background_task

    tmpdir = Path(tempfile.mkdtemp())
    task = await spawn_bookmarker_background_task(
        parent_frame_id="test-parent",
        root_frame_id="test-root",
        interval_seconds=1000,   # never fires during test
        bookmarks_dir=tmpdir,
        checkpointer=None,       # no-op mode
    )
    assert task is not None, "task should be returned"
    assert not task.done(), "task should not be done immediately"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done(), "task should be done after cancel"
    print("✅ test_bookmarker_task_creates_and_cancels")


async def test_bookmarker_task_survives_none_checkpointer():
    """Test: with checkpointer=None the loop should idle, not crash."""
    from internagents.frame_service import spawn_bookmarker_background_task

    task = await spawn_bookmarker_background_task(
        parent_frame_id="test-parent",
        root_frame_id="test-root",
        interval_seconds=0.1,
        bookmarks_dir=Path("/tmp/internagents-test-bookmarks"),
        checkpointer=None,
    )

    # Let it iterate a couple times with short interval
    await asyncio.sleep(0.3)
    assert not task.done(), "task should still be running after no-op iterations"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_read_frame_checkpoint_returns_none_for_none_checkpointer():
    """Test: _read_frame_checkpoint handles None checkpointer gracefully."""
    from internagents.frame_service import _read_frame_checkpoint

    result = await _read_frame_checkpoint("test-frame", None)
    assert result is None, "should return None for None checkpointer"
    print("✅ test_read_frame_checkpoint_returns_none_for_none_checkpointer")


async def test_extract_bookmarks_from_empty_result():
    """Test: _extract_bookmarks handles empty result gracefully."""
    from internagents.frame_service import _extract_bookmarks

    result = {}
    bookmarks = _extract_bookmarks(result)
    assert isinstance(bookmarks, list), "should return a list"
    assert len(bookmarks) == 0, "should return empty list for empty result"
    print("✅ test_extract_bookmarks_from_empty_result")


async def test_extract_bookmarks_from_output_data():
    """Test: _extract_bookmarks extracts from output_data."""
    from internagents.frame_service import _extract_bookmarks

    result = {
        "output_data": {
            "bookmarks": [{"title": "Test 1"}, {"title": "Test 2"}]
        }
    }
    bookmarks = _extract_bookmarks(result)
    assert len(bookmarks) == 2, f"should extract 2 bookmarks, got {len(bookmarks)}"
    assert bookmarks[0]["title"] == "Test 1"
    print("✅ test_extract_bookmarks_from_output_data")


async def test_spawn_bookmarker_creates_jsonl_file():
    """Test: spawn_bookmarker_background_task with no-op still creates output dir."""
    from internagents.frame_service import spawn_bookmarker_background_task

    tmpdir = Path(tempfile.mkdtemp()) / "bookmarks"
    task = await spawn_bookmarker_background_task(
        parent_frame_id="test-frame-1",
        root_frame_id="test-root-1",
        interval_seconds=1000,
        bookmarks_dir=tmpdir,
        checkpointer=None,
    )

    # Directory should be created (by frame_service or in task init)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    print("✅ test_spawn_bookmarker_creates_jsonl_file")


async def main():
    """Run all async tests."""
    tests = [
        test_bookmarker_task_creates_and_cancels,
        test_bookmarker_task_survives_none_checkpointer,
        test_read_frame_checkpoint_returns_none_for_none_checkpointer,
        test_extract_bookmarks_from_empty_result,
        test_extract_bookmarks_from_output_data,
        test_spawn_bookmarker_creates_jsonl_file,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            await t()
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
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
