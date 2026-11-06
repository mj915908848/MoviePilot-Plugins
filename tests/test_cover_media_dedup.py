"""Selection keeps one poster per movie or series across media versions"""

import ast
import json
import os
import re
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import quote, urlparse


SOURCE = Path(os.environ.get('COVER_PLUGIN_SOURCE', 'plugins.v3/mediacovergenerator/__init__.py'))


def load_plugin(path):
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MediaCoverGenerator')
    names = {'__get_items_batch', '__get_series_identities', '__filter_valid_items',
             '__build_content_key', '__build_content_keys', '__build_image_key', '__image_candidates',
             '__get_image_url', '__generate_from_server'}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name=cls.name, bases=[], keywords=[], body=methods, decorator_list=[]
    )], type_ignores=[]))
    namespace = dict(Any=Any, List=List, Optional=Optional, Tuple=Tuple,
                     re=re, json=json, quote=quote, urlparse=urlparse,
                     logger=Mock(), LIVE_TV_TYPE='livetv')
    exec(compile(module, str(path), 'exec'), namespace)
    plugin = namespace['MediaCoverGenerator']()
    plugin._seen_keys = set()
    plugin._series_identity_cache = {}
    plugin._sort_by = 'DateCreated'
    plugin._monitor_sort = False
    plugin._use_primary = True
    plugin._event = Event()
    plugin._MediaCoverGenerator__prefer_primary = lambda: True
    plugin._MediaCoverGenerator__is_single_image_style = lambda: False
    return plugin


def movie(item_id, tmdb='100', **fields):
    return dict(Id=item_id, Type='Movie', Name='Example', ProductionYear=2026,
                ProviderIds={'Tmdb': tmdb}, ImageTags={'Primary': 'poster-' + item_id}, **fields)


class MediaSelectionTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin(SOURCE)

    def select(self, items):
        return self.plugin._MediaCoverGenerator__filter_valid_items(items)

    def test_movie_versions_keep_first_identity_once(self):
        first = movie('1080p')
        self.assertEqual(self.select([first, movie('4k'), movie('other', '200')]),
                         [first, movie('other', '200')])

    def test_same_provider_identity_is_deduplicated_across_pages(self):
        first = movie('1080p')
        self.assertEqual(self.select([first]), [first])
        self.assertEqual(self.select([movie('4k')]), [])

    def test_provider_names_ignore_case_and_whitespace(self):
        first, second = movie('1080p'), movie('4k')
        second['ProviderIds'] = {'tmdb': ' 100 '}
        self.assertEqual(self.select([first, second]), [first])

    def test_remakes_with_distinct_identity_and_year_are_kept(self):
        first, remake = movie('old', '100'), movie('new', '200')
        first['ProductionYear'] = 1999
        self.assertEqual(self.select([first, remake]), [first, remake])

    def test_same_name_and_year_with_conflicting_provider_ids_are_kept(self):
        first, second = movie('a', '100'), movie('b', '200')
        self.assertEqual(self.select([first, second]), [first, second])

    def test_partial_provider_metadata_matches_shared_provider(self):
        first, second = movie('a'), movie('b')
        first['ProviderIds']['Imdb'] = 'tt100'
        second['ProviderIds'] = {'Imdb': 'tt100'}
        self.assertEqual(self.select([first, second]), [first])

    def test_missing_provider_metadata_uses_title_and_year(self):
        first, second = movie('a'), movie('b')
        first['ProviderIds'] = second['ProviderIds'] = {}
        self.assertEqual(self.select([first, second]), [first])

    def test_movie_and_series_with_same_tmdb_number_are_distinct(self):
        first, series = movie('film'), movie('show')
        series['Type'] = 'Series'
        self.assertEqual(self.select([first, series]), [first, series])

    def test_series_versions_keep_one_poster(self):
        first, second = movie('show1080'), movie('show4k')
        first['Type'] = second['Type'] = 'Series'
        self.assertEqual(self.select([first, second]), [first])

    def test_episodes_of_one_merged_series_keep_one_poster(self):
        first, second = movie('ep1', '9001'), movie('ep2', '9002')
        for item in (first, second):
            item.update(Type='Episode', SeriesId='merged-show', SeriesPrimaryImageTag='series-poster')
        self.assertEqual(self.select([first, second]), [first])

    def test_episode_identity_comes_from_series_not_episode_provider_ids(self):
        first, second = movie('ep1080', '9001'), movie('ep4k', '9002')
        for index, item in enumerate((first, second)):
            item.update(Type='Episode', SeriesId='series-' + str(index),
                        SeriesPrimaryImageTag='series-poster-' + str(index),
                        _CoverSeriesIdentity={'Id': 'series-' + str(index), 'Type': 'Series',
                                              'ProviderIds': {'Tmdb': 'series100'}})
        self.assertEqual(self.select([first, second]), [first])

    def test_unavailable_poster_does_not_reserve_content_identity(self):
        unavailable, valid = movie('missing'), movie('good')
        unavailable['ImageTags'] = {}
        self.assertEqual(self.select([unavailable, valid]), [valid])

    def test_same_image_tag_with_different_item_ids_is_deduplicated(self):
        first, second = movie('a', '100'), movie('b', '200')
        first['ImageTags'] = second['ImageTags'] = {'Primary': 'identical-poster'}
        self.assertEqual(self.select([first, second]), [first])

    def test_presentation_identity_groups_versions_without_provider_ids(self):
        first, second = movie('a'), movie('b')
        for item in (first, second):
            item['ProviderIds'] = {}
            item['PresentationUniqueKey'] = 'merged-movie100'
        second['Name'] = 'Alternate title'
        self.assertEqual(self.select([first, second]), [first])

    def test_api_requests_provider_metadata(self):
        service = SimpleNamespace(instance=SimpleNamespace(get_data=Mock(
            return_value=SimpleNamespace(json=lambda: {'Items': [movie('a')]}))))
        self.plugin._MediaCoverGenerator__get_items_batch(service, 'library')
        url = service.instance.get_data.call_args.kwargs['url']
        self.assertIn('ProviderIds', url)

    def test_episode_fetch_resolves_series_provider_ids_and_caches_them(self):
        first, second = movie('ep1080', 'ep1'), movie('ep4k', 'ep2')
        for index, item in enumerate((first, second)):
            item.update(Type='Episode', SeriesId='series-' + str(index),
                        SeriesPrimaryImageTag='poster-' + str(index))
        series = [dict(Id='series-' + str(index), Type='Series',
                       ProviderIds={'Tmdb': 'series100'}) for index in range(2)]
        response = lambda items: SimpleNamespace(json=lambda: {'Items': items})
        service = SimpleNamespace(instance=SimpleNamespace(get_data=Mock(
            side_effect=[response([first, second]), response(series), response([first, second])])))
        fetched = self.plugin._MediaCoverGenerator__get_items_batch(service, 'library')
        self.assertEqual(self.select(fetched), [first])
        self.plugin._MediaCoverGenerator__get_items_batch(service, 'library', offset=50)
        self.assertEqual(service.instance.get_data.call_count, 3)
        self.assertIn('Ids=series-0,series-1', service.instance.get_data.call_args_list[1].kwargs['url'])

    def test_series_lookup_failure_still_groups_merged_episodes(self):
        first, second = movie('ep1'), movie('ep2')
        for item in (first, second):
            item.update(Type='Episode', SeriesId='merged-show', SeriesPrimaryImageTag='poster')
        service = SimpleNamespace(instance=SimpleNamespace(get_data=Mock(
            side_effect=[SimpleNamespace(json=lambda: {'Items': [first, second]}), RuntimeError('offline')])))
        fetched = self.plugin._MediaCoverGenerator__get_items_batch(service, 'library')
        self.assertEqual(self.select(fetched), [first])

    def test_duplicate_rows_add_provider_aliases_for_later_pages(self):
        first, second, third = movie('a'), movie('b'), movie('c')
        first['ProviderIds']['Imdb'] = 'tt100'
        second['ProviderIds'] = {'Imdb': 'tt100', 'Tvdb': '300'}
        third['ProviderIds'] = {'Tvdb': '300'}
        self.assertEqual(self.select([first, second, third]), [first])

    def test_same_title_without_year_does_not_merge_unidentified_movies(self):
        first, second = movie('a'), movie('b')
        for item in (first, second):
            item.pop('ProductionYear')
            item['ProviderIds'] = {}
        self.assertEqual(self.select([first, second]), [first, second])

    def test_paging_continues_until_enough_unique_movies_are_found(self):
        first, another = movie('a'), movie('c', '200')
        self.plugin._MediaCoverGenerator__get_required_items = lambda: 2
        fetch = Mock(side_effect=[[first, movie('b')], [another]])
        self.plugin._MediaCoverGenerator__get_items_batch = fetch
        update = Mock(return_value=True)
        self.plugin._MediaCoverGenerator__update_grid_image = update
        service = SimpleNamespace(type='emby', name='Emby')
        self.plugin._MediaCoverGenerator__generate_from_server(
            service, {'Id': 'lib', 'Name': 'Films', 'CollectionType': 'movies'}, 'Films'
        )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(update.call_args.args[3], [first, another])

if __name__ == '__main__':
    unittest.main()
