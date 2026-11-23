"""Docker bind-mount initialization safety checks (no third-party dependencies)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docker_entrypoint


class DockerEntrypointTests(unittest.TestCase):
    def test_rejects_other_data_paths_before_root_repair(self):
        for invalid in ('/', '/etc', '/opt', '/data/../etc'):
            with self.subTest(invalid=invalid):
                with patch.dict(os.environ, {'DATA_DIR': invalid}):
                    with self.assertRaisesRegex(RuntimeError, 'DATA_DIR must be /data'):
                        docker_entrypoint._storage_path()

    def test_storage_repair_skips_symlinks_to_outside_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            storage = root / 'data'
            storage.mkdir()
            (storage / 'artwork').mkdir()
            (storage / 'artwork' / 'example.webp').write_bytes(b'image bytes')
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'private.txt').write_text('unrelated host data')
            (storage / 'outside-link').symlink_to(outside, target_is_directory=True)
            (storage / 'file-link').symlink_to(outside / 'private.txt')
            # Testing with the current UID (avoids needing root privileges).
            docker_entrypoint.prepare_storage(storage, os.getuid(), os.getgid())
            self.assertEqual((outside / 'private.txt').read_text(), 'unrelated host data')
            self.assertTrue((storage / 'outside-link').is_symlink())
            self.assertTrue((storage / 'file-link').is_symlink())

    def test_creates_storage_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Path(temp) / 'data'
            docker_entrypoint.prepare_storage(storage, os.getuid(), os.getgid())
            saved = storage / 'tracker.sqlite3'
            saved.write_bytes(b'previous data')
            saved.chmod(0o400)
            storage.chmod(0o500)
            docker_entrypoint.prepare_storage(storage, os.getuid(), os.getgid())
            self.assertEqual(saved.read_bytes(), b'previous data')
            self.assertTrue(saved.stat().st_mode & 0o200)
            self.assertTrue(os.access(storage, os.W_OK | os.X_OK))


if __name__ == '__main__':
    unittest.main()
