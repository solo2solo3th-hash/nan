"""Tests for automatic assistant-session encryption key fallback."""
import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet


class AssistantAccountsKeyTests(unittest.TestCase):
    def setUp(self):
        self.db = types.ModuleType("database")
        self.values = {}
        self.db.setting_get = lambda key: self.values.get(key, "")
        self.db.setting_set = lambda key, value: self.values.__setitem__(key, value)
        self.previous = sys.modules.get("database")
        sys.modules["database"] = self.db
        sys.modules.pop("assistant_accounts", None)

    def tearDown(self):
        sys.modules.pop("assistant_accounts", None)
        if self.previous is not None:
            sys.modules["database"] = self.previous
        else:
            sys.modules.pop("database", None)

    def test_invalid_configured_key_falls_back_to_stable_token_derived_key(self):
        with patch.dict(os.environ, {"ASSISTANT_ENCRYPTION_KEY": "Fernet", "TOKEN": "test-token"}):
            accounts = importlib.import_module("assistant_accounts")
            first = accounts._fernet().encrypt(b"test").decode()
            second = accounts._fernet().decrypt(first.encode())
            self.assertEqual(second, b"test")
            self.assertEqual(accounts._fernet()._signing_key, accounts._fernet()._signing_key)

    def test_fallback_is_stable_across_module_reload(self):
        with patch.dict(os.environ, {"ASSISTANT_ENCRYPTION_KEY": "Fernet", "TOKEN": "test-token"}):
            accounts = importlib.import_module("assistant_accounts")
            encrypted = accounts._fernet().encrypt(b"persisted")
            sys.modules.pop("assistant_accounts", None)
            accounts = importlib.import_module("assistant_accounts")
            self.assertEqual(accounts._fernet().decrypt(encrypted), b"persisted")

    def test_does_not_fallback_when_encrypted_sessions_exist_and_configured_key_invalid(self):
        self.values["ASSISTANT_SESSIONS_ENCRYPTED"] = "existing-ciphertext"
        with patch.dict(os.environ, {"ASSISTANT_ENCRYPTION_KEY": "Fernet", "TOKEN": "test-token"}):
            accounts = importlib.import_module("assistant_accounts")
            with self.assertRaises(RuntimeError):
                accounts._fernet()


if __name__ == "__main__":
    unittest.main()
