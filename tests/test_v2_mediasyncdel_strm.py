"""V2 MediaSyncDel 的 STRM 电影整理记录匹配回归测试。"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins.v2/mediasyncdel/__init__.py"


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"


@dataclass
class TransferHistory:
    tmdbid: int
    mtype: str
    dest: str


class TransferHistoryOper:
    def __init__(self, records: list[TransferHistory]):
        self.records = records

    def get_by(self, **criteria):
        return [
            record for record in self.records
            if all(getattr(record, field) == value for field, value in criteria.items())
        ]


class Logger:
    def info(self, message):
        pass

    def warning(self, message):
        pass


def load_transfer_history_method():
    """只加载真实插件方法，避免依赖正在运行的 MoviePilot 后端。"""
    tree = ast.parse(PLUGIN_PATH.read_text(encoding="utf-8"))
    plugin_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MediaSyncDel"
    )
    method = next(
        node for node in plugin_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__get_transfer_his"
    )
    isolated_class = ast.ClassDef(
        name="MediaSyncDel", bases=[], keywords=[], body=[method], decorator_list=[]
    )
    module = ast.fix_missing_locations(ast.Module(body=[isolated_class], type_ignores=[]))
    scope = {"MediaType": MediaType, "TransferHistory": TransferHistory, "List": list,
             "Path": Path, "settings": SimpleNamespace(RMT_MEDIAEXT=[".mkv", ".mp4"]),
             "logger": Logger()}
    exec(compile(module, str(PLUGIN_PATH), "exec"), scope)
    return scope["MediaSyncDel"]


class StrmMovieHistoryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_transfer_history_method()()
        self.strm_path = (
            "/CD2/115/影视库/电影/欧美电影/木乃伊 (2026) {tmdb-1304313}/"
            "木乃伊.Lee Cronin's The Mummy.2026.strm"
        )
        self.movie_path = self.strm_path[:-5] + ".mkv"

    def lookup(self, media_path=None, tmdb_id=1304313):
        return self.plugin._MediaSyncDel__get_transfer_his(
            media_type="Movie", media_name="木乃伊 (2026)",
            media_path=media_path or self.strm_path,
            tmdb_id=tmdb_id, season_num=None, episode_num=None,
        )[1]

    def test_normal_movie_path_keeps_exact_match(self):
        expected = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        other = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path + ".other")
        self.plugin._transferhis = TransferHistoryOper([expected, other])

        self.assertEqual(self.lookup(media_path=self.movie_path), [expected])

    def test_strm_movie_matches_one_transfer_in_same_directory(self):
        expected = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        self.plugin._transferhis = TransferHistoryOper([expected])

        self.assertEqual(self.lookup(), [expected])

    def test_strm_movie_skips_multiple_versions_in_same_directory(self):
        first = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        second = TransferHistory(
            1304313, MediaType.MOVIE.value,
            str(Path(self.movie_path).with_name("木乃伊.另一版本.mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([first, second])

        self.assertEqual(self.lookup(), [])

    def test_strm_movie_does_not_match_a_different_directory(self):
        other = TransferHistory(
            1304313, MediaType.MOVIE.value,
            str(Path(self.movie_path).parent.parent / "其他电影" / Path(self.movie_path).name),
        )
        self.plugin._transferhis = TransferHistoryOper([other])

        self.assertEqual(self.lookup(), [])

    def test_strm_movie_does_not_match_different_tmdb_or_media_type(self):
        wrong_tmdb = TransferHistory(42, MediaType.MOVIE.value, self.movie_path)
        wrong_type = TransferHistory(1304313, MediaType.TV.value, self.movie_path)
        self.plugin._transferhis = TransferHistoryOper([wrong_tmdb, wrong_type])

        self.assertEqual(self.lookup(), [])

    def test_strm_movie_ignores_nonmedia_record(self):
        nonmedia = TransferHistory(1304313, MediaType.MOVIE.value,
                                   str(Path(self.movie_path).with_suffix(".nfo")))
        self.plugin._transferhis = TransferHistoryOper([nonmedia])

        self.assertEqual(self.lookup(), [])

    def test_strm_movie_matches_windows_separator_history(self):
        strm_path = r"D:\media\movie\木乃伊 (2026)\木乃伊.strm"
        movie_path = r"D:\media\movie\木乃伊 (2026)\木乃伊.mkv"
        expected = TransferHistory(1304313, MediaType.MOVIE.value, movie_path)
        self.plugin._transferhis = TransferHistoryOper([expected])

        self.assertEqual(self.lookup(media_path=strm_path), [expected])

    def test_strm_movie_without_transfer_history_stays_unmatched(self):
        self.plugin._transferhis = TransferHistoryOper([])

        self.assertEqual(self.lookup(), [])


if __name__ == "__main__":
    unittest.main()
