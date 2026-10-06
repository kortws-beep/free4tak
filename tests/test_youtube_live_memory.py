"""유튜브 라이브 모니터 — 전사 후 메모리 정리(gc + malloc_trim)와 로그."""
import io
import sys
import types
import unittest
import contextlib

from _helpers import stub, use_temp_cwd

stub("requests")
stub("youtube_stock_monitor", CHANNELS={}, init_db=None, save_pick=None, validate_stock_name=None,
     extract_stock_picks=None, generate_comment=None, notify_report=None, _get_llm_client=None)
_tmp = use_temp_cwd()
import youtube_live_monitor as Y  # noqa: E402


class Seg:
    def __init__(self, t): self.text = t


class Model:
    def __init__(self, fail=False): self.fail = fail
    def transcribe(self, path, **k):
        if self.fail:
            raise RuntimeError("boom")
        return (Seg(x) for x in ["삼성전자", "목표가"]), types.SimpleNamespace()


class Memory(unittest.TestCase):
    def test_release_after_transcribe_even_on_error(self):
        calls = []
        Y._release_memory = lambda: calls.append(1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(Y.transcribe_audio(Model(), "x.m4a"), "삼성전자 목표가")
            self.assertEqual(Y.transcribe_audio(Model(fail=True), "x.m4a"), "")
        self.assertEqual(len(calls), 2)
        self.assertIn("🧠 [메모리]", out.getvalue())

    def test_rss_reads_proc(self):
        if sys.platform.startswith("linux"):
            self.assertGreater(Y._rss_mb(), 0)


if __name__ == "__main__":
    unittest.main()
