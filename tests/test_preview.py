import io, os, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.preview import extract_snapshot, preview_environment

class PreviewTests(unittest.TestCase):
    def test_expected_https_origin_and_secure_cookie(self):
        with patch.dict(os.environ, {'ALLOWED_HOSTS':'*', 'COOKIE_SECURE':'0'}):
            env=preview_environment('https://sample-name.trycloudflare.com')
        self.assertEqual(env['COOKIE_SECURE'], '1')
        self.assertEqual(env['PUBLIC_ORIGIN'], 'https://sample-name.trycloudflare.com')
        self.assertEqual(env['ALLOWED_HOSTS'], '127.0.0.1,localhost,sample-name.trycloudflare.com')
        self.assertTrue(Path(env['DATA_DIR']).is_absolute())

    def test_reject_unexpected_tunnel_origin(self):
        for url in ('http://sample.trycloudflare.com', 'https://example.com', 'https://trycloudflare.com.evil.test'):
            with self.assertRaises(ValueError):preview_environment(url)

    def test_snapshot_rejects_path_traversal_before_extraction(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as z:
            z.writestr('app/main.py', 'example');z.writestr('../outside.txt', 'invalid')
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'release'
            with self.assertRaises(ValueError):extract_snapshot(raw.getvalue(), target)
            self.assertFalse((Path(root)/'outside.txt').exists())
            self.assertFalse((target/'app/main.py').exists())

if __name__=='__main__':unittest.main()
