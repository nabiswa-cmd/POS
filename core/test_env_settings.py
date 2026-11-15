import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config.settings import _load_env_file


class EnvFileTests(SimpleTestCase):
    def test_inline_comments_are_removed_without_truncating_hashes_in_values(self):
        names = ("WINESPOS_TEST_INT", "WINESPOS_TEST_QUOTED", "WINESPOS_TEST_HASH")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ):
            for name in names:
                os.environ.pop(name, None)
            path = Path(directory) / "shop.env"
            path.write_text(
                'WINESPOS_TEST_INT=30   # idle timeout\n'
                'WINESPOS_TEST_QUOTED="key#part" # secret label\n'
                'WINESPOS_TEST_HASH=token#suffix\n',
                encoding="utf-8",
            )

            _load_env_file(path)

            self.assertEqual(os.environ["WINESPOS_TEST_INT"], "30")
            self.assertEqual(os.environ["WINESPOS_TEST_QUOTED"], "key#part")
            self.assertEqual(os.environ["WINESPOS_TEST_HASH"], "token#suffix")
