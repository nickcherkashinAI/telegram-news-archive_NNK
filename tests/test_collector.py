import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import unittest
import collector

class ArchiveTests(unittest.TestCase):
    def test_reporting_window(self):
        start, end = collector.report_window(datetime(2026, 11, 22, 4, 20, tzinfo=timezone.utc))
        self.assertEqual(start.isoformat(), '2026-11-21T04:00:00+00:00')
        self.assertEqual(end.isoformat(), '2026-11-22T04:00:00+00:00')
        start2, end2 = collector.report_window(datetime(2026, 11, 22, 3, 20, tzinfo=timezone.utc))
        self.assertEqual(start2.isoformat(), '2026-11-20T04:00:00+00:00')
        self.assertEqual(end2.isoformat(), '2026-11-21T04:00:00+00:00')

    def test_dedup_and_split(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            with patch.object(collector, 'DATA', root/'archive'), patch.object(collector, 'REPORTS', root/'reports'), patch.object(collector, 'ROOT', root):
                messages = [dict(channel='bbbreaking', id=i, date_utc=f'2026-11-21T08:{i%60:02d}:00+00:00',
                                 date_msk='', text='Пост', media_type=None, url=f'https://t.me/bbbreaking/{i}') for i in range(1,82)]
                collector.merge_records('bbbreaking', messages)
                collector.merge_records('bbbreaking', messages)
                saved = json.loads((root/'archive/bbbreaking/2026-11-21.json').read_text())
                self.assertEqual(len(saved), 81)
                m = collector.produce_report(['bbbreaking'], {'bbbreaking': '2026-11-22T04:20:00+00:00'}, datetime(2026,11,22,4,20,tzinfo=timezone.utc))
                self.assertEqual(m['total_messages'], 81)
                self.assertEqual([p['count'] for p in m['channels'][0]['parts']], [40,40,1])
                self.assertTrue(m['all_channels_fetched_after_window'])

if __name__ == '__main__':
    unittest.main()
