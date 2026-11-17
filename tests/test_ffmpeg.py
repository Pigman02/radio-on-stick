from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.ffmpeg import ffmpeg_status, resolve_ffmpeg

ROOT = Path(__file__).resolve().parents[1]


class FfmpegDetectionTests(unittest.TestCase):
    def test_known_bad_path_is_empty(self):
        self.assertEqual(resolve_ffmpeg("/definitely/not/ffmpeg"), "")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_status_and_installer_check(self):
        status = ffmpeg_status("ffmpeg")
        self.assertTrue(status["available"])
        self.assertIn("ffmpeg", status["version"].lower())
        result = subprocess.run(
            ["python3", str(ROOT / "deploy" / "ensure_ffmpeg.py"), "--check", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["available"])


if __name__ == "__main__":
    unittest.main()
