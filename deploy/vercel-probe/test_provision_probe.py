"""Offline post-provision safeguards, including anonymous private-file denial."""
import os
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import provision_probe as probe


def role_environment():
    values = {}
    for role in probe.ROLES:
        prefix = "PAPERMIND_" + role.upper() + "_TURSO_"
        values[prefix + "DATABASE_URL"] = "libsql://" + role + ".turso.io"
        values[prefix + "AUTH_TOKEN"] = "synthetic"
    return values


class ProvisionTests(unittest.TestCase):
    def test_database_check_is_read_only(self):
        raw = MagicMock()
        raw.execute.return_value.fetchone.side_effect = [(1,), (0,)] * 3
        with patch.dict(os.environ, role_environment(), clear=True), patch.object(probe.libsql, "connect", return_value=raw):
            result = probe.database_checks()
        self.assertTrue(result["ok"])
        self.assertFalse(result["writes_performed"])
        self.assertEqual(raw.close.call_count, 3)
        self.assertTrue(all(call.args[0].startswith("SELECT ") for call in raw.execute.call_args_list))

    def test_duplicate_database_and_partial_configuration_fail_before_connect(self):
        values = role_environment()
        values["PAPERMIND_AUTH_TURSO_DATABASE_URL"] = "https://library.turso.io/"
        with patch.dict(os.environ, values, clear=True), patch.object(probe.libsql, "connect") as connect:
            with self.assertRaisesRegex(probe.ProvisionFailure, "not_distinct"):
                probe.database_checks()
            connect.assert_not_called()
        del values["PAPERMIND_RUNTIME_TURSO_AUTH_TOKEN"]
        with patch.dict(os.environ, values, clear=True), patch.object(probe.libsql, "connect") as connect:
            with self.assertRaisesRegex(probe.ProvisionFailure, "configuration"):
                probe.database_checks()
            connect.assert_not_called()

    def blob_fixture(self, anonymous_status):
        sdk = MagicMock()
        sdk.put_blob.return_value.url = "https://fixture.private.blob.vercel-storage.com/synthetic.bin"
        sdk.head_blob.return_value.size = 4096
        sdk.stream_blob.return_value.__enter__.return_value = [probe.synthetic_content(4096)]
        response = MagicMock()
        response.__enter__.return_value = SimpleNamespace(status_code=anonymous_status)
        package = ModuleType("vercel.blob")
        package.sync = sdk
        modules = {"vercel": ModuleType("vercel"), "vercel.blob": package}
        return sdk, response, modules

    def test_private_roundtrip_leaves_unique_fixture_and_denies_anonymous(self):
        sdk, response, modules = self.blob_fixture(403)
        with patch.dict("sys.modules", modules), patch.object(probe.httpx, "stream", return_value=response):
            result = probe.blob_checks()
        self.assertTrue(result["ok"])
        self.assertTrue(result["retained"])
        self.assertFalse(result["browser_direct_upload_tested"])
        self.assertEqual(sdk.put_blob.call_args.kwargs["access"], "private")
        self.assertFalse(sdk.put_blob.call_args.kwargs["allow_overwrite"])
        sdk.delete_blob.assert_not_called()

    def test_anonymous_success_is_a_failure_and_errors_are_sanitized(self):
        sdk, response, modules = self.blob_fixture(200)
        with patch.dict("sys.modules", modules), patch.object(probe.httpx, "stream", return_value=response):
            with self.assertRaises(probe.ProvisionFailure):
                probe.blob_checks()
            sdk.put_blob.side_effect = RuntimeError("secret-in-provider-error")
            with self.assertRaisesRegex(probe.ProvisionFailure, "^private_upload$"):
                probe.blob_checks()

    def test_large_fixture_is_generated_and_size_is_bounded(self):
        self.assertEqual(len(probe.synthetic_content(probe.LARGE_SIZE)), probe.LARGE_SIZE)
        with self.assertRaises(probe.ProvisionFailure):
            probe.synthetic_content(100_000_000)


if __name__ == "__main__":
    unittest.main()
