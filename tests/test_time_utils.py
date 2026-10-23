from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'papermind'))
from time_utils import data_timestamp, elapsed_seconds


class TimestampTests(unittest.TestCase):
    def test_cloud_timestamp_carries_utc_offset(self):
        with patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'turso'}):
            parsed = datetime.fromisoformat(data_timestamp())
        self.assertEqual(parsed.utcoffset().total_seconds(),0)
        self.assertLess(abs((datetime.now(timezone.utc)-parsed).total_seconds()),2)

    def test_local_format_stays_compatible(self):
        with patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'sqlite'}):
            self.assertIsNone(datetime.fromisoformat(data_timestamp()).tzinfo)

    def test_memory_intervals_accept_both_timestamp_formats(self):
        for value in (datetime.now().isoformat(),datetime.now(timezone.utc).isoformat()):
            self.assertLess(abs(elapsed_seconds(value)),2)
