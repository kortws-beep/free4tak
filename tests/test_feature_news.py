"""11-05 [특징주] 뉴스 수집 · 유튜브 추천 보관(지우지 않고 archive로)."""
import sqlite3
import unittest

from _helpers import use_temp_cwd

_tmp = use_temp_cwd()
import feature_news as F  # noqa: E402

M = {"비에이치": "090460", "SK": "034730", "크래프톤": "259960", "SK하이닉스": "000660"}


class News(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(F.parse_title("[특징주] 비에이치, 애플 폴더블폰 공개 앞두고 20.7% 상승(종합)", M), ("090460", "비에이치", 20.7, 1))
        self.assertEqual(F.parse_title("[특징주] HP, GSK 구조조정 CEO 선임", M)[0], None)       # 'SK'가 GSK에 걸리면 안 됨
        self.assertEqual(F.parse_title("美 반도체주 급락에…SK하이닉스 프리마켓 약세[특징주]", M)[1], "SK하이닉스")
        self.assertEqual(F.parse_title("[특징주] 크래프톤, 3% 하락", M)[2], -3.0)

    def test_save(self):
        items = [{"title": "[특징주] <b>크래프톤</b>, 자사주 소각에 급등", "link": "l1", "pubDate": "Wed, 04 Nov 2026 18:32:00 +0900"},
                 {"title": "[미국 특징주] 애플 급락", "link": "l2", "pubDate": "Wed, 04 Nov 2026 18:32:00 +0900"},
                 {"title": "그냥 뉴스", "link": "l3"}]
        self.assertEqual(F.save_items(items, M, db="n.db"), (1, False))
        self.assertEqual(F.save_items(items, M, db="n.db"), (0, True))
        self.assertEqual(sqlite3.connect("n.db").execute("SELECT pub_ts, code FROM news").fetchall(), [("2026-11-04 18:32", "259960")])


class YoutubeArchive(unittest.TestCase):
    def test_archive(self):
        import youtube_stock_monitor as Y
        c = sqlite3.connect("y.db")
        c.execute("CREATE TABLE youtube_picks (id INTEGER PRIMARY KEY, pick_date TEXT, stock_name TEXT)")
        c.executemany("INSERT INTO youtube_picks (pick_date, stock_name) VALUES (?,?)", [("2026-01-01", "옛픽"), ("2999-01-01", "새픽")])
        c.commit(); c.close()
        self.assertEqual(Y.cleanup_old_picks(25, db="y.db"), 1)
        c = sqlite3.connect("y.db")
        self.assertEqual([r[0] for r in c.execute("SELECT stock_name FROM youtube_picks")], ["새픽"])
        self.assertEqual([r[0] for r in c.execute("SELECT stock_name FROM youtube_picks_archive")], ["옛픽"])


if __name__ == "__main__":
    unittest.main()
