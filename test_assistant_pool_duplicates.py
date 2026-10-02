"""
Unit tests for AssistantPool duplicate session detection.

Tests validate that stored sessions matching environment sessions
are correctly skipped during pool initialization to prevent
concurrent Telegram auth key usage (406 AUTH_KEY_DUPLICATED error).

Run with: python test_assistant_pool_duplicates.py
"""
import sys
import os

# Mock external dependencies BEFORE importing assistant_pool
from unittest.mock import MagicMock, patch

# Mock the pyrogram and pytgcalls modules
sys.modules['pyrogram'] = MagicMock()
sys.modules['pyrogram.errors'] = MagicMock()
sys.modules['pytgcalls'] = MagicMock()
sys.modules['pytgcalls.filters'] = MagicMock()
sys.modules['pytgcalls.types'] = MagicMock()
sys.modules['pytgcalls.sync'] = MagicMock()

# Mock database and config before importing
sys.modules['database'] = MagicMock()
sys.modules['config'] = MagicMock()

import logging
logging.basicConfig(level=logging.DEBUG)


def test_duplicate_session_skipped_when_matches_slot_1():
    """
    Test that a stored session matching PYROGRAM_SESSION_STRING (slot 1)
    is skipped during _load_persistent_settings() to prevent concurrent usage.
    """
    print("\n[Test 1] Testing duplicate session detection (slot 1)...")
    
    # Create mock modules for this test
    mock_config = MagicMock()
    mock_config.SESSION_STRING = "test_session_slot_1"
    mock_config.API_ID = "123"
    mock_config.API_HASH = "abc"
    mock_config.CALLS_READY_TIMEOUT = 30
    
    mock_database = MagicMock()
    mock_database.setting_get = MagicMock(return_value=None)
    mock_database.setting_set = MagicMock()
    
    mock_accounts = MagicMock()
    # Stored sessions include a duplicate of slot 1
    mock_accounts.load_sessions = MagicMock(return_value={
        2: "different_session_slot_2",
        3: "test_session_slot_1",  # DUPLICATE of slot 1
    })
    mock_accounts.save_sessions = MagicMock()
    
    # Patch the imports in assistant_pool
    with patch.dict('sys.modules', {
        'config': mock_config,
        'database': mock_database,
        'assistant_accounts': mock_accounts,
    }):
        # Now we can import assistant_pool
        import importlib
        if 'assistant_pool' in sys.modules:
            del sys.modules['assistant_pool']
        
        import assistant_pool
        importlib.reload(assistant_pool)
        
        # Mock VoiceCallRunner
        assistant_pool.VoiceCallRunner = MagicMock()
        
        # Set environment variable
        os.environ['PYROGRAM_SESSION_STRING'] = "test_session_slot_1"
        
        try:
            # Create pool
            pool = assistant_pool.AssistantPool()
            
            # Verify slot 1 is loaded
            assert 1 in pool._sessions, "Slot 1 should be in sessions"
            assert pool._sessions[1] == "test_session_slot_1", "Slot 1 should have the correct session"
            
            # Call _load_persistent_settings()
            pool._load_persistent_settings()
            
            # Verify:
            # - Slot 2 (different session) is loaded
            assert 2 in pool._sessions, "Slot 2 should be loaded (unique session)"
            assert pool._sessions[2] == "different_session_slot_2", "Slot 2 session should match stored"
            
            # - Slot 3 (duplicate of slot 1) is SKIPPED
            assert 3 not in pool._sessions, "Slot 3 should be SKIPPED (duplicate of slot 1)"
            
            # - Slot 1 remains unchanged
            assert pool._sessions[1] == "test_session_slot_1", "Slot 1 should remain unchanged"
            
            print("  ✓ Duplicate session correctly skipped")
            print("  ✓ Unique stored sessions correctly loaded")
            print("  ✓ Environment slot 1 preserved")
            
        finally:
            if 'PYROGRAM_SESSION_STRING' in os.environ:
                del os.environ['PYROGRAM_SESSION_STRING']


def test_different_sessions_both_loaded():
    """
    Test that stored sessions with different session strings
    are correctly loaded without being skipped.
    """
    print("\n[Test 2] Testing unique sessions are loaded...")
    
    mock_config = MagicMock()
    mock_config.SESSION_STRING = "slot_1_session"
    mock_config.API_ID = "123"
    mock_config.API_HASH = "abc"
    mock_config.CALLS_READY_TIMEOUT = 30
    
    mock_database = MagicMock()
    mock_database.setting_get = MagicMock(return_value=None)
    mock_database.setting_set = MagicMock()
    
    mock_accounts = MagicMock()
    mock_accounts.load_sessions = MagicMock(return_value={
        2: "slot_2_unique_session",
        3: "slot_3_unique_session",
    })
    mock_accounts.save_sessions = MagicMock()
    
    with patch.dict('sys.modules', {
        'config': mock_config,
        'database': mock_database,
        'assistant_accounts': mock_accounts,
    }):
        import importlib
        if 'assistant_pool' in sys.modules:
            del sys.modules['assistant_pool']
        
        import assistant_pool
        importlib.reload(assistant_pool)
        assistant_pool.VoiceCallRunner = MagicMock()
        
        os.environ['PYROGRAM_SESSION_STRING'] = "slot_1_session"
        
        try:
            pool = assistant_pool.AssistantPool()
            pool._load_persistent_settings()
            
            # All slots should be loaded
            assert 1 in pool._sessions, "Slot 1 should be present"
            assert 2 in pool._sessions, "Slot 2 should be loaded"
            assert 3 in pool._sessions, "Slot 3 should be loaded"
            
            assert pool._sessions[1] == "slot_1_session", "Slot 1 session incorrect"
            assert pool._sessions[2] == "slot_2_unique_session", "Slot 2 session incorrect"
            assert pool._sessions[3] == "slot_3_unique_session", "Slot 3 session incorrect"
            
            print("  ✓ All unique sessions loaded successfully")
            
        finally:
            if 'PYROGRAM_SESSION_STRING' in os.environ:
                del os.environ['PYROGRAM_SESSION_STRING']


def test_environment_slot_takes_precedence():
    """
    Test that environment-managed slots (e.g., ASSISTANT_SESSION_2)
    are not overridden by stored sessions in the same slot.
    """
    print("\n[Test 3] Testing environment slot precedence...")
    
    mock_config = MagicMock()
    mock_config.SESSION_STRING = "slot_1_env"
    mock_config.API_ID = "123"
    mock_config.API_HASH = "abc"
    mock_config.CALLS_READY_TIMEOUT = 30
    
    mock_database = MagicMock()
    mock_database.setting_get = MagicMock(return_value=None)
    mock_database.setting_set = MagicMock()
    
    mock_accounts = MagicMock()
    mock_accounts.load_sessions = MagicMock(return_value={
        2: "slot_2_stored",  # Should be ignored
        3: "slot_3_stored",
    })
    mock_accounts.save_sessions = MagicMock()
    
    with patch.dict('sys.modules', {
        'config': mock_config,
        'database': mock_database,
        'assistant_accounts': mock_accounts,
    }):
        import importlib
        if 'assistant_pool' in sys.modules:
            del sys.modules['assistant_pool']
        
        import assistant_pool
        importlib.reload(assistant_pool)
        assistant_pool.VoiceCallRunner = MagicMock()
        
        # Set environment variables
        os.environ['PYROGRAM_SESSION_STRING'] = "slot_1_env"
        os.environ['ASSISTANT_SESSION_2'] = "slot_2_env"
        
        try:
            pool = assistant_pool.AssistantPool()
            pool._load_persistent_settings()
            
            # Slot 2 should use environment value, not stored
            assert pool._sessions[2] == "slot_2_env", "Slot 2 should use environment value"
            assert 2 in pool._environment_slots, "Slot 2 should be marked as environment-managed"
            
            # Slot 3 should be loaded from stored
            assert 3 in pool._sessions, "Slot 3 should be loaded"
            assert pool._sessions[3] == "slot_3_stored", "Slot 3 should use stored value"
            assert 3 not in pool._environment_slots, "Slot 3 should not be environment-managed"
            
            print("  ✓ Environment slots correctly take precedence")
            print("  ✓ Stored sessions correctly loaded for non-environment slots")
            
        finally:
            for key in ['PYROGRAM_SESSION_STRING', 'ASSISTANT_SESSION_2']:
                if key in os.environ:
                    del os.environ[key]


if __name__ == "__main__":
    try:
        test_duplicate_session_skipped_when_matches_slot_1()
        test_different_sessions_both_loaded()
        test_environment_slot_takes_precedence()
        print("\n" + "="*50)
        print("✅ All tests PASSED")
        print("="*50)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

