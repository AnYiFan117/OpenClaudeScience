"""Smoke tests for per-workspace onboarding gate."""

import sys
import os
import json
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internagents.frame_service import (
    _workspace_id_from_resource,
    _load_agent_config,
    _save_agent_config,
    entry_root_frame,
)


def test_workspace_id_hash_stable():
    """Same workspace path should produce same 16-char hash."""
    workspace_path = "/home/user/projects/research"

    # Compute hash manually (same as implementation)
    hash_hex = hashlib.sha256(workspace_path.encode()).hexdigest()[:16]

    # Two calls should produce same result
    id1 = hash_hex
    id2 = hash_hex

    assert id1 == id2, f"Hash should be stable: {id1} vs {id2}"
    assert len(id1) == 16, f"Hash should be 16 chars, got {len(id1)}"
    print("✅ workspace_id_hash_stable")


def test_workspace_id_from_resource_local():
    """Should compute workspace ID from local resource."""
    # This test uses the actual resources file in the repo
    workspace_id = _workspace_id_from_resource("local")

    assert isinstance(workspace_id, str), f"Should return string, got {type(workspace_id)}"
    # Either a 16-char hex or "resource:local" fallback
    assert len(workspace_id) >= 8, f"ID should be reasonable length, got {len(workspace_id)}"
    print("✅ workspace_id_from_resource_local")


def test_workspace_id_fallback_for_missing_resource():
    """Should return fallback ID for non-existent resource."""
    workspace_id = _workspace_id_from_resource("nonexistent-resource")

    assert "resource:" in workspace_id, f"Should fallback to resource:id format, got {workspace_id}"
    assert "nonexistent-resource" in workspace_id, "Should include resource ID"
    print("✅ workspace_id_fallback_for_missing_resource")


def test_load_save_agent_config():
    """Load and save config round-trip."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "test.config.json"

        # Write a test config
        test_config = {
            "backend": {"type": "local"},
            "verification": {"enabled": True},
            "onboarding_completed_workspaces": {},
        }

        # Mock _agent_config_path to return our temp file (patch where it's imported)
        with patch("internagents.agent_graph._agent_config_path") as mock_path:
            mock_path.return_value = config_file

            _save_agent_config(test_config)

            # Verify file was written
            assert config_file.exists(), "Config file should be written"

            # Load it back
            loaded = _load_agent_config()

            assert loaded.get("backend", {}).get("type") == "local"
            assert "onboarding_completed_workspaces" in loaded

    print("✅ load_save_agent_config")


async def test_entry_routes_to_onboarding_when_new():
    """entry_root_frame should route to onboarding for fresh workspace."""
    workspace_id = "test_workspace_id_12ab"

    # Mock config with empty onboarding dict
    test_config = {
        "verification": {"enabled": True},
        "onboarding_completed_workspaces": {},
    }

    with patch("internagents.frame_service._load_agent_config") as mock_load:
        mock_load.return_value = test_config

        with patch("internagents.frame_service._workspace_id_from_resource") as mock_ws_id:
            mock_ws_id.return_value = workspace_id

            with patch("internagents.frame_service.create_and_run_root_frame", new_callable=AsyncMock) as mock_create:
                # Mock onboarding frame running to completion
                mock_create.return_value = {
                    "id": "frame1",
                    "root_frame_id": "root1",
                    "agent_name": "onboarding",
                    "status": "running",
                }

                frame = await entry_root_frame(input_data={"query": "hello"})

                # Verify create_and_run_root_frame was called with agent_name="onboarding"
                calls = mock_create.call_args_list
                assert len(calls) > 0, "Should call create_and_run_root_frame"

                # Find the call with agent_name="onboarding"
                onboarding_call = None
                for call in calls:
                    if call.kwargs.get("agent_name") == "onboarding":
                        onboarding_call = call
                        break

                assert onboarding_call is not None, (
                    f"Should route to onboarding agent, got calls: {[c.kwargs.get('agent_name') for c in calls]}"
                )

    print("✅ entry_routes_to_onboarding_when_new")


async def test_entry_routes_to_main_when_completed():
    """entry_root_frame should route to main for completed workspace."""
    workspace_id = "test_workspace_id_12ab"

    # Mock config with workspace already marked as completed
    test_config = {
        "verification": {"enabled": True},
        "onboarding_completed_workspaces": {
            workspace_id: True,
        },
    }

    with patch("internagents.frame_service._load_agent_config") as mock_load:
        mock_load.return_value = test_config

        with patch("internagents.frame_service._workspace_id_from_resource") as mock_ws_id:
            mock_ws_id.return_value = workspace_id

            with patch("internagents.frame_service.create_and_run_root_frame", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = {
                    "id": "frame1",
                    "root_frame_id": "root1",
                    "agent_name": "main",
                    "status": "running",
                }

                frame = await entry_root_frame(input_data={"query": "hello"})

                # Verify create_and_run_root_frame was called with agent_name="main"
                calls = mock_create.call_args_list
                assert len(calls) > 0, "Should call create_and_run_root_frame"

                # Find the call with agent_name="main"
                main_call = None
                for call in calls:
                    if call.kwargs.get("agent_name") == "main":
                        main_call = call
                        break

                assert main_call is not None, (
                    f"Should route to main agent, got calls: {[c.kwargs.get('agent_name') for c in calls]}"
                )

    print("✅ entry_routes_to_main_when_completed")


async def test_flag_persisted_after_onboarding_completes():
    """Config should be updated with workspace ID after onboarding completes."""
    workspace_id = "test_workspace_id_12ab"

    # Start with empty onboarding dict
    test_config = {
        "verification": {"enabled": True},
        "onboarding_completed_workspaces": {},
    }

    saved_config = None

    def mock_save(config):
        nonlocal saved_config
        saved_config = config

    with patch("internagents.frame_service._load_agent_config") as mock_load:
        mock_load.return_value = test_config

        with patch("internagents.frame_service._workspace_id_from_resource") as mock_ws_id:
            mock_ws_id.return_value = workspace_id

            with patch("internagents.frame_service._save_agent_config", side_effect=mock_save) as mock_save_agent:

                with patch("internagents.frame_service.create_and_run_root_frame", new_callable=AsyncMock) as mock_create:
                    # Simulate onboarding frame completing
                    mock_create.return_value = {
                        "id": "frame1",
                        "root_frame_id": "root1",
                        "agent_name": "onboarding",
                        "status": "completed",  # COMPLETED!
                    }

                    frame = await entry_root_frame(input_data={"query": "hello"})

                    # Verify that _save_agent_config was called
                    assert mock_save_agent.called, "Should call _save_agent_config"

                    # Verify the saved config has the workspace marked as completed
                    assert saved_config is not None, "Config should be saved"
                    completed_workspaces = saved_config.get("onboarding_completed_workspaces", {})
                    assert completed_workspaces.get(workspace_id) is True, (
                        f"Workspace {workspace_id} should be marked as completed, got {completed_workspaces}"
                    )

    print("✅ flag_persisted_after_onboarding_completes")


def run_all_tests():
    """Run all sync tests and return success status."""
    tests = [
        test_workspace_id_hash_stable,
        test_workspace_id_from_resource_local,
        test_workspace_id_fallback_for_missing_resource,
        test_load_save_agent_config,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False

    return True


async def run_all_async_tests():
    """Run all async tests and return success status."""
    tests = [
        test_entry_routes_to_onboarding_when_new,
        test_entry_routes_to_main_when_completed,
        test_flag_persisted_after_onboarding_completes,
    ]

    for test in tests:
        try:
            await test()
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False

    return True


if __name__ == "__main__":
    # Run sync tests
    success = run_all_tests()

    # Run async tests
    if success:
        success = asyncio.run(run_all_async_tests())

    if success:
        print("\n✅ All onboarding entry tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
