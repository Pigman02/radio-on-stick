import tempfile
import unittest
from pathlib import Path

from app.media_meta import _parse_id3


def _frame(name: bytes, body: bytes) -> bytes:
    return name + len(body).to_bytes(4, "big") + b"\x00\x00" + body


def _syncsafe(value: int) -> bytes:
    return bytes(
        (
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


class MediaMetaTests(unittest.TestCase):
    def test_id3_tags_lyrics_and_cover_are_read_with_stdlib(self) -> None:
        image = b"\xff\xd8\xff\xe0" + b"fake-jpeg" + b"\xff\xd9"
        frames = b"".join(
            (
                _frame(b"TIT2", b"\x03" + "测试歌曲".encode("utf-8")),
                _frame(b"TPE1", b"\x03" + "测试歌手".encode("utf-8")),
                _frame(b"TALB", b"\x03" + "测试专辑".encode("utf-8")),
                _frame(b"USLT", b"\x03eng\x00" + b"[00:00.00]" + "一句".encode("utf-8")),
                _frame(b"APIC", b"\x03image/jpeg\x00\x03\x00" + image),
            )
        )
        payload = b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tagged.mp3"
            path.write_bytes(payload)
            result = _parse_id3(path)
        self.assertEqual(result["title"], "测试歌曲")
        self.assertEqual(result["artist"], "测试歌手")
        self.assertEqual(result["album"], "测试专辑")
        self.assertIn("[00:00.00]", result["lyrics"])
        self.assertEqual(result["cover_bytes"], image)
        self.assertEqual(result["cover_mime"], "image/jpeg")


if __name__ == "__main__":
    unittest.main()
