"""Smoke tests for async delegate surface.

The internal dispatch mechanism now goes through langgraph_sdk HTTP client
(dispatch spawns an INDEPENDENT run on the local-runtime instead of an
in-process asyncio.Task). The old asyncio-mock-based tests no longer apply
to the current dispatch path; they are pending rewrite against the new
langgraph_sdk client mock surface. See [[phase-3d-test-refresh]].

Meanwhile this file keeps the tests that don't touch dispatch internals:
- timeout-validation guards on collect_child_frames
- topology guards on send_message_to_frame
"""

import asyncio
import unittest

import pytest

from internagents.frame_state import create_root_frame
from internagents.frame_service import (
    _child_metadata,
    _child_results,
    _parent_inboxes,
    _running_children,
    collect_child_frames,
    send_message_to_frame,
)


class AsyncDelegateSurfaceTests(unittest.TestCase):
    def setUp(self):
        _child_metadata.clear()
        _child_results.clear()
        _running_children.clear()
        _parent_inboxes.clear()

    def tearDown(self):
        _child_metadata.clear()
        _child_results.clear()
        _running_children.clear()
        _parent_inboxes.clear()

    def test_collect_rejects_none_timeout(self):
        async def run_test():
            with self.assertRaises(ValueError):
                await collect_child_frames(["frame-1"], timeout=None)  # type: ignore[arg-type]
        asyncio.run(run_test())

    def test_collect_rejects_timeout_too_large(self):
        async def run_test():
            with self.assertRaises(ValueError):
                await collect_child_frames(["frame-1"], timeout=1801)
        asyncio.run(run_test())

    def test_send_message_to_parent_enqueues(self):
        async def run_test():
            parent = create_root_frame(agent_name="main", input_data={"objective": "root"})
            child = create_root_frame(agent_name="main", input_data={"objective": "child"})
            child["parent_frame_id"] = parent["id"]

            result = await send_message_to_frame(child, "parent", "hello", "info")

            self.assertEqual(result["status"], "sent")
            self.assertIn(parent["id"], _parent_inboxes)
            queued = _parent_inboxes[parent["id"]].get_nowait()
            self.assertEqual(queued["message"], "hello")
            self.assertEqual(queued["from_frame_id"], child["id"])
        asyncio.run(run_test())

    def test_send_message_refuses_child_target(self):
        async def run_test():
            sender = create_root_frame(agent_name="main", input_data={"objective": "s"})
            # Any non-'parent' target is treated as a child frame_id → refused (MVP)
            result = await send_message_to_frame(sender, "some-child-id", "hi", "info")
            self.assertEqual(result["status"], "refused")
            self.assertIn("reason", result)
        asyncio.run(run_test())


# NOTE: dispatch/collect/stop against a real langgraph_sdk client is exercised
# end-to-end via scripts/verify_async_delegate.py; add unit-level coverage
# with a mocked _get_lg_client() in a follow-up (Phase 3d).
if __name__ == "__main__":  # pragma: no cover
    unittest.main()
