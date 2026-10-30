"""Pin copied runtime sources, including checkpoint-sensitive preprocessing."""
import hashlib
import json
from pathlib import Path
import unittest


class SourceProvenanceTests(unittest.TestCase):
    def test_copied_sources_match_recorded_local_digests(self):
        project = Path(__file__).resolve().parents[1]
        manifest = json.loads((project/'SOURCE_MANIFEST.json').read_text())
        for item in manifest['files']:
            with self.subTest(source=item['target']):
                actual = hashlib.sha256((project/item['target']).read_bytes()).hexdigest()
                self.assertEqual(actual, item['local_sha256'])
        adaptive = next(item for item in manifest['files'] if item['target'].endswith('/adaptive_fov.py'))
        self.assertEqual(adaptive['local_sha256'], adaptive['source_sha256'])
