"""Offline contract checks for native DeGoog and existing SearXNG transport."""
import io
import json
import sqlite3
import threading
import urllib.error
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

import pytest

from experimental.se_dork.backends import normalize_results, search_url
from experimental.se_dork.client import run_preflight, run_reachability_check
from experimental.se_dork.models import RunOptions
from experimental.se_dork.service import _fetch_page, _paginate_results, run_dork_search


def response(payload):
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    return resp


def http_error(code):
    return urllib.error.HTTPError('https://search.test/config', code, 'failure', {}, io.BytesIO())


@pytest.mark.parametrize('url,prefix', [
    ('https://search.test', ''),
    ('https://search.test/sub/', '/sub'),
    ('https://search.test/sub/api/search/', '/sub'),
])
def test_detect_degoog_without_issuing_search(url, prefix):
    replies = [response({'tabs': []})]
    if '/api/search' not in url:
        replies.insert(0, http_error(404))
    with patch('urllib.request.urlopen', side_effect=replies) as opened:
        result = run_reachability_check(url)
    assert result.ok
    assert result.search_endpoint == f'https://search.test{prefix}/api/search'
    assert all('?' not in call.args[0] for call in opened.call_args_list)


def test_degoog_preflight_uses_native_json():
    with patch('urllib.request.urlopen', side_effect=[
        http_error(404), response({'tabs': []}), response({'results': []}),
    ]) as opened:
        result = run_preflight('https://search.test')
    assert result.ok and 'DeGoog' in result.message
    url = opened.call_args.args[0]
    assert urlsplit(url).path == '/api/search'
    assert parse_qs(urlsplit(url).query) == {'q': ['hello'], 'type': ['web'], 'page': ['1']}


@pytest.mark.parametrize('payload', [[], None, {}, {'tabs': 'bad'}])
def test_invalid_detection_is_not_accepted(payload):
    with patch('urllib.request.urlopen', side_effect=[http_error(404), response(payload)]):
        assert not run_reachability_check('https://search.test').ok


@pytest.mark.parametrize('code', [401, 403, 429, 500])
def test_searxng_failures_do_not_switch_provider(code):
    with patch('urllib.request.urlopen', side_effect=http_error(code)) as opened:
        result = run_reachability_check('https://search.test')
    assert not result.ok
    assert opened.call_count == 1


@pytest.mark.parametrize('payload', [[], None, {'results': None}])
def test_preflight_invalid_search_shape(payload):
    with patch('urllib.request.urlopen', side_effect=[response({'tabs': []}), response(payload)]):
        result = run_preflight('https://search.test/api/search')
    assert not result.ok
    assert result.reason_code == 'search_parse_error'


@pytest.mark.parametrize('code', [401, 403])
def test_degoog_auth_failure_has_no_searxng_format_hint(code):
    with patch('urllib.request.urlopen', side_effect=[response({'tabs': []}), http_error(code)]):
        result = run_preflight('https://search.test/api/search')
    assert result.reason_code == 'search_http_error'
    assert 'API-key' in result.message
    assert 'settings.yml' not in result.message


@pytest.mark.parametrize('url', ['file:///tmp/data', 'localhost:4444',
                                 'https://search.test/?q=x', 'https://search.test/#search'])
def test_invalid_url_fails_before_network(url):
    with patch('urllib.request.urlopen') as opened:
        assert not run_preflight(url).ok
    opened.assert_not_called()


def test_both_request_dialects_preserve_query():
    query = 'intitle:"index of" + files & data'
    for endpoint, page_key in [('https://search.test/sub/search', 'pageno'),
                               ('https://search.test/sub/api/search', 'page')]:
        parts = urlsplit(search_url(endpoint, query, 2))
        assert parts.path == urlsplit(endpoint).path
        assert parse_qs(parts.query)['q'] == [query]
        assert parse_qs(parts.query)[page_key] == ['2']


def test_native_metadata_is_preserved_without_mutating_payload():
    row = {'url': 'https://files.test/', 'snippet': 'Index of /',
           'source': 'Brave', 'sources': ['Brave', 'Wikipedia']}
    with patch('urllib.request.urlopen', return_value=response({'results': [row, None]})):
        rows, warnings = _fetch_page('https://search.test/api/search', 'query', 1)
    assert rows[0]['content'] == 'Index of /'
    assert rows[0]['engine'] == 'Brave'
    assert rows[0]['engines'] == ['Brave', 'Wikipedia']
    assert 'engine' not in row
    assert warnings == ()
    assert normalize_results([{'source': {}, 'sources': 'bad'}], degoog=True)[0]['engines'] == []


def test_degoog_stops_at_ten_pages():
    with patch('urllib.request.urlopen', side_effect=[
        response({'results': [{'url': f'https://files.test/{n}'}]}) for n in range(10)
    ]) as opened:
        outcome = _paginate_results('https://search.test/api/search', 'query', 100,
                                    sleep_fn=lambda _: None)
    assert outcome.pages_fetched == 10
    assert opened.call_count == 10
    assert parse_qs(urlsplit(opened.call_args.args[0]).query)['page'] == ['10']


def test_degoog_429_retries_and_cancel_stops_pagination():
    event = threading.Event()
    with patch('urllib.request.urlopen', side_effect=[http_error(429),
        response({'results': [{'url': 'https://files.test/'}]}),
    ]) as opened:
        outcome = _paginate_results('https://search.test/api/search', 'query', 10,
            sleep_fn=lambda _: None, cancel_event=event,
            page_processor=lambda *_: event.set(), short_retry=0)
    assert opened.call_count == 2
    assert outcome.hard_retry_count == 1
    assert outcome.stopped_early
    assert outcome.unique_count == 1


def test_degoog_run_detects_once_and_persists_metadata(tmp_path):
    from experimental.se_dork.classifier import ClassifyResult
    db = tmp_path / 'run.db'
    row = {'url': 'https://files.test/', 'title': 'Index', 'snippet': 'listing',
           'source': 'Brave', 'sources': ['Brave', 'Wikipedia']}
    with patch('urllib.request.urlopen', side_effect=[http_error(404),
        response({'tabs': []}), response({'results': [row]}),
    ]) as opened, patch('experimental.se_dork.classifier.classify_url',
        return_value=ClassifyResult(verdict='OPEN_INDEX', reason_code='index', http_status=200)):
        result = run_dork_search(RunOptions('https://search.test/', 'index of', max_results=1), db_path=db)
    assert result.status == 'done', result.error
    assert result.deduped_count == 1
    assert opened.call_count == 3
    assert all('q=hello' not in call.args[0] for call in opened.call_args_list)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT instance_url FROM dork_runs').fetchone() == ('https://search.test/',)
        stored = conn.execute('SELECT snippet, source_engine, source_engines_json FROM dork_results').fetchone()
    assert stored[:2] == ('listing', 'Brave')
    assert json.loads(stored[2]) == ['Brave', 'Wikipedia']
