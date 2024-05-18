from random import choice

import pytest
from requests_mock import Mocker

from musify.api.cache.backend.base import ResponseCache, Connection
from musify.api.cache.session import CachedSession
from tests.api.cache.backend.test_sqlite import TestSQLiteCache as SQLiteCacheTester
from tests.api.cache.backend.testers import ResponseCacheTester


class TestCachedSession:

    @pytest.fixture(scope="class", params=[SQLiteCacheTester])
    def tester(self, request) -> ResponseCacheTester:
        return request.param

    @pytest.fixture(scope="class")
    def connection(self, tester: ResponseCacheTester) -> Connection:
        """Yields a valid :py:class:`Connection` to use throughout tests in this suite as a pytest.fixture."""
        return tester.generate_connection()

    @pytest.fixture(scope="class")
    def cache(self, tester: ResponseCacheTester, connection: Connection) -> ResponseCache:
        """Yields a valid :py:class:`ResponseCache` to use throughout tests in this suite as a pytest.fixture."""
        return tester.generate_cache(connection=connection)

    @pytest.fixture(scope="class")
    def session(self, cache: ResponseCache) -> CachedSession:
        """
        Yields a valid :py:class:`CachedSession` with the given ``cache``
        to use throughout tests in this suite as a pytest.fixture.
        """
        return CachedSession(cache=cache)

    def test_request_cached(
            self,
            session: CachedSession,
            cache: ResponseCache,
            tester: ResponseCacheTester,
            requests_mock: Mocker
    ):
        repository = choice(list(cache.values()))

        key, value = choice(list(repository.items()))
        expected = tester.generate_response_from_item(repository.settings, key, value)
        request = expected.request
        assert key in repository

        response = session.request(method=request.method, url=request.url)
        assert response.json() == expected.json()
        assert len(requests_mock.request_history) == 0

    def test_request_not_cached(
            self,
            session: CachedSession,
            cache: ResponseCache,
            tester: ResponseCacheTester,
            requests_mock: Mocker
    ):
        repository = choice(list(cache.values()))
        expected = tester.generate_response(repository.settings)
        request = expected.request
        key = repository.get_key_from_request(request)
        assert key not in repository
        requests_mock.get(request.url, json=expected.json())

        response = session.request(method=request.method, url=request.url, persist=False)
        assert response.json() == expected.json()
        assert len(requests_mock.request_history) == 1
        assert key not in repository

        response = session.request(method=request.method, url=request.url, persist=True)
        assert response.text == expected.text
        assert len(requests_mock.request_history) == 2
        assert key in repository

        response = session.request(method=request.method, url=request.url)
        assert response.json() == expected.json()
        assert len(requests_mock.request_history) == 2
