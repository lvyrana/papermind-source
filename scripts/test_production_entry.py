"""Exercise the offline production entry's gates without importing app/data."""
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).resolve().parents[1]/'deploy/vercel-production/app.py'
BASE = {'VERCEL_ENV':'production','PAPERMIND_MIGRATION_APPROVED':'true',
        'PAPERMIND_SQL_BACKEND':'turso','PAPERMIND_FILE_BACKEND':'vercel-blob',
        'BLOB_STORE_ID':'store_production','OWNER_UID':'00000000-0000-4000-8000-000000000002',
        'ZOTERO_OAUTH_CLIENT_KEY':'synthetic','ZOTERO_OAUTH_CLIENT_SECRET':'synthetic',
        'PAPERMIND_PUBLIC_ORIGIN':'https://site.example','CRON_SECRET':'synthetic-test-only-'.ljust(40,'x')}


class ProductionEntryTests(unittest.TestCase):
    def execute(self, changes=None, host=None):
        fake_app = SimpleNamespace(middleware=lambda _: lambda function: function)
        remote = SimpleNamespace(remote_credentials=lambda role: ('libsql://'+(host or role+'-production.example'),'synthetic'))
        with patch.dict(os.environ,BASE | (changes or {}),clear=True), patch.dict(sys.modules,{'api':SimpleNamespace(app=fake_app),'sql_backend':remote}), patch.object(sys,'path',list(sys.path)):
            return runpy.run_path(str(ENTRY))

    def test_prepared_candidate_loads_without_real_app(self):
        self.assertIn('production_scope',self.execute())

    def test_unapproved_or_test_backends_rejected(self):
        for changes in ({'VERCEL_ENV':'preview'},{'PAPERMIND_MIGRATION_APPROVED':'false'},
                        {'PAPERMIND_SYNTHETIC_TASKS':'true'},{'BLOB_STORE_ID':'store_RkRS2huB7kCab3j8'},
                        {'PAPERMIND_SQL_BACKEND':'sqlite'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): self.execute(changes)

    def test_invalid_origin_or_missing_credentials_rejected(self):
        for changes in ({'PAPERMIND_PUBLIC_ORIGIN':'https://site.example/path'},
                        {'PAPERMIND_PUBLIC_ORIGIN':'http://site.example'}, {'CRON_SECRET':'short'},
                        {'ZOTERO_OAUTH_CLIENT_SECRET':''}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): self.execute(changes)

    def test_preview_or_shared_role_databases_rejected(self):
        for host in ('litfinch-library-preview-example.turso.io','same-production.example'):
            with self.subTest(host=host), self.assertRaises(RuntimeError): self.execute(host=host)


if __name__ == '__main__': unittest.main()
