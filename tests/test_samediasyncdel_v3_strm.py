"""V3 STRM 删除匹配回归测试"""

import ast
import tempfile
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins.v3/samediasyncdel/__init__.py"


class MediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"


class MediaSource(Enum):
    TMDB = "tmdb"
    DOUBAN = "douban"


@dataclass
class TransferHistory:
    media_id: str
    mtype: str
    dest: str
    media_source: MediaSource = MediaSource.TMDB
    season: Optional[str] = None
    episode: Optional[str] = None


class TransferHistoryOper:
    def __init__(self, records):
        self.records = records

    def get_by(self, title=None, year=None, mtype=None, season=None,
               episode=None, media_source=None, media_id=None, dest=None, *,
               match_all_versions=False):
        if not media_source or not media_id:
            return []
        if mtype != MediaType.TV.value and not dest and not match_all_versions:
            return []
        criteria = dict(media_source=media_source, media_id=media_id, mtype=mtype)
        if season is not None:
            criteria["season"] = season
            if episode:
                criteria.update(episode=episode, dest=dest)
        elif dest:
            criteria["dest"] = dest
        return [
            record for record in self.records
            if all(str(getattr(record, key)) == str(value)
                   for key, value in criteria.items())
        ]


class Logger:
    def info(self, message):
        pass

    def warning(self, message):
        pass

    def warn(self, message):
        pass

    def error(self, message):
        pass


def load_lookup_method():
    tree = ast.parse(PLUGIN_PATH.read_text(encoding="utf-8"))
    plugin_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SaMediaSyncDel"
    )
    method_names = {
        "has_prefix",
        "__get_local_media_path",
        "__sync_del",
        "__get_p115_media_path",
        "__get_p123_media_path",
        "__get_local_media_paths",
        "__find_local_transfer_his",
        "__get_transfer_his",
    }
    methods = [
        node for node in plugin_class.body
        if isinstance(node, ast.FunctionDef) and node.name in method_names
    ]
    isolated = ast.ClassDef(
        name="SaMediaSyncDel", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    module = ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[]))
    scope = {
        "MediaType": MediaType,
        "MediaSource": MediaSource,
        "Optional": Optional,
        "TransferHistory": TransferHistory,
        "List": list,
        "Path": Path,
        "settings": SimpleNamespace(RMT_MEDIAEXT=[".mkv", ".mp4"]),
        "logger": Logger(),
    }
    exec(compile(module, str(PLUGIN_PATH), "exec"), scope)
    return scope["SaMediaSyncDel"]


class StrmMovieHistoryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_lookup_method()()
        self.strm_path = "/CD2/115/影视库/电影/木乃伊 (2026)/木乃伊.strm"
        self.movie_path = "/CD2/115/影视库/电影/木乃伊 (2026)/木乃伊.mkv"

    def lookup(self, path, strm_movie=False, media_id=1304313):
        return self.plugin._SaMediaSyncDel__get_transfer_his(
            media_type="Movie",
            media_name="木乃伊 (2026)",
            media_path=path,
            media_source=MediaSource.TMDB,
            media_id=media_id,
            season_num=None,
            episode_num=None,
            strm_movie=strm_movie,
        )[1]

    def test_local_strm_matches_only_history_in_same_directory(self):
        expected = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        self.plugin._transferhis = TransferHistoryOper([expected])

        self.assertEqual(self.lookup(self.strm_path), [expected])

    def test_cloud_strm_matches_renamed_media_file(self):
        expected = TransferHistory(
            1304313, MediaType.MOVIE.value,
            str(Path(self.movie_path).with_name("木乃伊.2026.2160p.mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([expected])

        self.assertEqual(self.lookup(self.movie_path, strm_movie=True), [expected])

    def test_normal_movie_path_remains_exact(self):
        expected = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        other = TransferHistory(
            1304313, MediaType.MOVIE.value,
            str(Path(self.movie_path).with_name("木乃伊.另一版本.mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([expected, other])

        self.assertEqual(self.lookup(self.movie_path), [expected])

    def test_multiple_versions_are_ambiguous(self):
        first = TransferHistory(1304313, MediaType.MOVIE.value, self.movie_path)
        second = TransferHistory(
            1304313, MediaType.MOVIE.value,
            str(Path(self.movie_path).with_name("木乃伊.另一版本.mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([first, second])

        self.assertEqual(self.lookup(self.strm_path), [])

    def test_different_directory_or_identity_does_not_match(self):
        other_dir = TransferHistory(
            1304313, MediaType.MOVIE.value,
            "/CD2/115/影视库/电影/其他电影/木乃伊.mkv",
        )
        other_identity = TransferHistory(42, MediaType.MOVIE.value, self.movie_path)
        self.plugin._transferhis = TransferHistoryOper([other_dir, other_identity])

        self.assertEqual(self.lookup(self.strm_path), [])

    def test_missing_identity_does_not_use_fallback(self):
        history = TransferHistory(None, MediaType.MOVIE.value, self.movie_path)
        self.plugin._transferhis = TransferHistoryOper([history])

        self.assertEqual(self.lookup(self.strm_path, media_id=None), [])

    def test_windows_separators_match_same_directory(self):
        strm_path = r"D:\media\木乃伊 (2026)\木乃伊.strm"
        history = TransferHistory(
            1304313, MediaType.MOVIE.value,
            r"D:\media\木乃伊 (2026)\木乃伊.mkv",
        )
        self.plugin._transferhis = TransferHistoryOper([history])

        self.assertEqual(self.lookup(strm_path), [history])


class LocalMappingFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.source = root / "emby" / "影视库"
        self.targets = [root / name / "影视库" for name in ("CD2", "PT", "PT2")]
        self.relative_path = Path("电影/欧美电影/浴血黑帮：不朽传奇 (2026) {tmdb-875828}/movie.strm")
        self.emby_path = str(self.source / self.relative_path)
        self.series_relative_path = Path("电视剧/国产剧/百花杀 (2026) {tmdb-286506}")
        self.series_emby_path = str(self.source / self.series_relative_path)
        self.plugin = load_lookup_method()()
        self.plugin._local_library_path = "\n".join(
            f"{self.source}#{target}" for target in self.targets
        )

    def find(self, media_type="MOV", season_num=None, episode_num=None, media_path=None,
             media_id=875828):
        return self.plugin._SaMediaSyncDel__find_local_transfer_his(
            media_type=media_type,
            media_name="浴血黑帮：不朽传奇 (2026)",
            media_path=media_path or self.emby_path,
            media_source=MediaSource.TMDB,
            media_id=media_id,
            season_num=season_num,
            episode_num=episode_num,
        )

    def history_at(self, target):
        return TransferHistory(
            875828, MediaType.MOVIE.value,
            str((target / self.relative_path).with_name("浴血黑帮：不朽传奇.Peaky.Blinders.2026.mkv")),
        )



class DuplicateLocalMappingTests(LocalMappingFixture, unittest.TestCase):
    def test_third_mapping_finds_only_matching_transfer_history(self):
        expected = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper([expected])

        mapped_path, _, histories = self.find()

        self.assertEqual(mapped_path, str(self.targets[2] / self.relative_path))
        self.assertEqual(histories, [expected])

    def test_records_in_multiple_destinations_are_ambiguous(self):
        self.plugin._transferhis = TransferHistoryOper(
            [self.history_at(self.targets[0]), self.history_at(self.targets[2])]
        )

        mapped_path, _, histories = self.find()

        self.assertIsNone(mapped_path)
        self.assertEqual(histories, [])

    def test_strm_filename_selects_one_matching_version(self):
        matching = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[0] / self.relative_path).with_suffix(".mkv")),
        )
        other = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper([matching, other])

        mapped_path, _, histories = self.find()

        self.assertEqual(mapped_path, str(self.targets[0] / self.relative_path))
        self.assertEqual(histories, [matching])

    def test_same_strm_filename_in_two_destinations_remains_ambiguous(self):
        first = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[0] / self.relative_path).with_suffix(".mkv")),
        )
        third = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[2] / self.relative_path).with_suffix(".mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([first, third])

        mapped_path, _, histories = self.find()

        self.assertIsNone(mapped_path)
        self.assertEqual(histories, [])

    def test_single_mapping_selects_one_matching_filename(self):
        self.plugin._local_library_path = f"{self.source}#{self.targets[0]}"
        matching = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[0] / self.relative_path).with_suffix(".mkv")),
        )
        other = self.history_at(self.targets[0])
        self.plugin._transferhis = TransferHistoryOper([matching, other])

        mapped_path, _, histories = self.find()

        self.assertEqual(mapped_path, str(self.targets[0] / self.relative_path))
        self.assertEqual(histories, [matching])

    def test_exact_strm_history_has_priority_over_media_fallback(self):
        exact = TransferHistory(
            875828, MediaType.MOVIE.value,
            str(self.targets[0] / self.relative_path),
        )
        media = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[2] / self.relative_path).with_suffix(".mkv")),
        )
        self.plugin._transferhis = TransferHistoryOper([exact, media])

        mapped_path, _, histories = self.find()

        self.assertEqual(mapped_path, str(self.targets[0] / self.relative_path))
        self.assertEqual(histories, [exact])

    def test_ambiguous_first_destination_cannot_select_third(self):
        first = self.history_at(self.targets[0])
        another = TransferHistory(
            875828, MediaType.MOVIE.value,
            str((self.targets[0] / self.relative_path).with_name("other-version.mkv")),
        )
        third = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper([first, another, third])

        mapped_path, _, histories = self.find()

        self.assertIsNone(mapped_path)
        self.assertEqual(histories, [])

    def test_existing_mapped_file_is_skipped(self):
        existing_path = self.targets[0] / self.relative_path
        existing_path.parent.mkdir(parents=True)
        existing_path.touch()
        expected = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper(
            [self.history_at(self.targets[0]), expected]
        )

        mapped_path, _, histories = self.find()

        self.assertEqual(mapped_path, str(self.targets[2] / self.relative_path))
        self.assertEqual(histories, [expected])

    def test_whole_series_lookup_does_not_duplicate_path_independent_records(self):
        expected = TransferHistory(
            286506, MediaType.TV.value,
            str(self.targets[0] / self.series_relative_path / "Season 1/E01.mp4"),
        )
        self.plugin._transferhis = TransferHistoryOper([expected])

        mapped_path, _, histories = self.find(
            media_type="Series", media_path=self.series_emby_path, media_id=286506
        )

        self.assertEqual(mapped_path, self.series_emby_path)
        self.assertEqual(histories, [expected])

    def test_whole_series_selects_all_mapped_records_with_existing_media_files(self):
        first_dest = self.targets[0] / self.series_relative_path / "Season 1/E04.mp4"
        second_dest = self.targets[1] / self.series_relative_path / "Season 1/E07.mkv"
        for dest in (first_dest, second_dest):
            dest.parent.mkdir(parents=True)
            dest.touch()
        first = TransferHistory(286506, MediaType.TV.value, str(first_dest))
        second = TransferHistory(286506, MediaType.TV.value, str(second_dest))
        unrelated = TransferHistory(
            286506, MediaType.TV.value,
            str(self.targets[2] / "电视剧/国产剧/其他剧/Season 1/E01.mkv"),
        )
        self.plugin._transferhis = TransferHistoryOper([first, second, unrelated])

        mapped_path, _, histories = self.find(
            media_type="Series", media_path=self.series_emby_path, media_id=286506
        )

        self.assertEqual(mapped_path, self.series_emby_path)
        self.assertEqual(histories, [first, second])

    def test_whole_series_skips_when_emby_directory_still_exists(self):
        Path(self.series_emby_path).mkdir(parents=True)
        record = TransferHistory(
            286506, MediaType.TV.value,
            str(self.targets[0] / self.series_relative_path / "Season 1/E04.mp4"),
        )
        self.plugin._transferhis = TransferHistoryOper([record])

        mapped_path, _, histories = self.find(
            media_type="Series", media_path=self.series_emby_path, media_id=286506
        )

        self.assertIsNone(mapped_path)
        self.assertEqual(histories, [])


class V3ContractTests(LocalMappingFixture, unittest.TestCase):
    def test_non_numeric_identity_and_provider_are_used(self):
        expected = self.history_at(self.targets[2])
        expected.media_id = "tt1234567"
        expected.media_source = MediaSource.DOUBAN
        wrong_provider = self.history_at(self.targets[0])
        wrong_provider.media_id = expected.media_id
        self.plugin._transferhis = TransferHistoryOper([wrong_provider, expected])
        _, _, records = self.plugin._SaMediaSyncDel__find_local_transfer_his(
            media_type="MOV", media_name="电影", media_path=self.emby_path,
            media_source=MediaSource.DOUBAN, media_id=expected.media_id,
            season_num=None, episode_num=None,
        )
        self.assertEqual(records, [expected])

    def test_missing_provider_never_queries_history(self):
        self.plugin._transferhis = Mock()
        _, _, records = self.plugin._SaMediaSyncDel__find_local_transfer_his(
            media_type="MOV", media_name="电影", media_path=self.emby_path,
            media_source=None, media_id="875828", season_num=None, episode_num=None,
        )
        self.assertEqual(records, [])
        self.plugin._transferhis.get_by.assert_not_called()

    def test_series_provider_and_directory_boundaries_are_preserved(self):
        dest = str(self.targets[0] / self.series_relative_path / "Season 1/E01.mkv")
        expected = TransferHistory("286506", MediaType.TV.value, dest)
        wrong_source = TransferHistory(
            "286506", MediaType.TV.value, dest, MediaSource.DOUBAN
        )
        neighboring = TransferHistory("286506", MediaType.TV.value, dest.replace(
            "{tmdb-286506}/", "{tmdb-286506}-other/"
        ))
        self.plugin._transferhis = TransferHistoryOper([expected, wrong_source, neighboring])
        _, _, records = self.find(
            media_type="Series", media_path=self.series_emby_path, media_id="286506"
        )
        self.assertEqual(records, [expected])

    def test_episode_query_keeps_season_episode_and_exact_path(self):
        dest = str(self.targets[2] / self.series_relative_path / "Season 1/E07.mkv")
        expected = TransferHistory(
            "286506", MediaType.TV.value, dest, season="S01", episode="E07"
        )
        wrong_episode = TransferHistory(
            "286506", MediaType.TV.value, dest, season="S01", episode="E06"
        )
        self.plugin._transferhis = TransferHistoryOper([expected, wrong_episode])
        _, _, records = self.find(
            media_type="Episode", media_path=str(
                self.source / self.series_relative_path / "Season 1/E07.mkv"
            ), media_id="286506", season_num="1", episode_num="7"
        )
        self.assertEqual(records, [expected])

    def test_malformed_mapping_does_not_block_later_valid_mapping(self):
        self.plugin._local_library_path = "broken\n#\n" + self.plugin._local_library_path
        expected = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper([expected])
        self.assertEqual(self.find()[2], [expected])

    def test_duplicate_mapping_lines_do_not_duplicate_matches(self):
        self.plugin._local_library_path += "\n" + self.plugin._local_library_path
        expected = self.history_at(self.targets[2])
        self.plugin._transferhis = TransferHistoryOper([expected])
        self.assertEqual(self.find()[2], [expected])

    def test_windows_source_mapping_supports_mixed_separators(self):
        self.plugin._local_library_path = rf"Z:\Media#{self.targets[0]}"
        expected = TransferHistory(
            "875828", MediaType.MOVIE.value, str(self.targets[0] / "电影/movie.mkv")
        )
        self.plugin._transferhis = TransferHistoryOper([expected])
        self.assertEqual(self.find(media_path=r"Z:\Media\电影\movie.strm")[2], [expected])

    def test_similarly_named_source_directory_is_not_a_mapping(self):
        self.plugin._transferhis = Mock()
        self.assertEqual(self.find(media_path=str(self.source) + "-other/movie.strm")[2], [])
        self.plugin._transferhis.get_by.assert_not_called()

    def test_sync_del_uses_v3_lookup_arguments(self):
        lookup = Mock(return_value=(None, None, []))
        self.plugin._SaMediaSyncDel__find_local_transfer_his = lookup
        self.plugin._SaMediaSyncDel__sync_del(
            media_type="MOV", media_name="电影", media_path=self.emby_path,
            media_source=MediaSource.TMDB, media_id="875828", season_num=None,
            episode_num=None, media_storage="local", media_suffix=None,
        )
        self.assertEqual(lookup.call_args.kwargs["media_source"], MediaSource.TMDB)
        self.assertEqual(lookup.call_args.kwargs["media_id"], "875828")

    def test_cloud_lookups_keep_strm_flag_after_suffix_replacement(self):
        for storage in ("p115", "p123"):
            with self.subTest(storage=storage):
                mapping = f"{self.source}#{self.targets[0]}#/影视库"
                setattr(self.plugin, f"_{storage}_library_path", mapping)
                setattr(self.plugin, f"_{storage}_force_delete_files", False)
                lookup = Mock(return_value=("电影", []))
                self.plugin._SaMediaSyncDel__get_transfer_his = lookup
                self.plugin._SaMediaSyncDel__sync_del(
                    media_type="MOV", media_name="电影", media_path=self.emby_path,
                    media_source=MediaSource.TMDB, media_id="875828", season_num=None,
                    episode_num=None, media_storage=storage, media_suffix="mkv",
                )
                self.assertEqual(lookup.call_count, 2)
                self.assertTrue(all(call.kwargs["strm_movie"] for call in lookup.call_args_list))
                self.assertTrue(lookup.call_args_list[0].kwargs["media_path"].endswith(".mkv"))


if __name__ == "__main__":
    unittest.main()
