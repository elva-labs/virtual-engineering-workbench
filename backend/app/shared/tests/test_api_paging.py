import json
from urllib.parse import quote

import assertpy
import pytest

from app.shared.api import paging

TOKEN = {"PK": "PROJECT#proj-1", "SK": "PROJECT#proj-1", "entity": "PROJECT"}


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(TOKEN),
        quote(json.dumps(TOKEN)),
        json.dumps(json.dumps(TOKEN)),  # a route that answered the token as a JSON string
    ],
)
def test_decode_page_token_accepts_the_forms_clients_send(raw):
    assertpy.assert_that(paging.decode_page_token(raw)).is_equal_to(TOKEN)


@pytest.mark.parametrize("raw", [None, ""])
def test_decode_page_token_without_a_token_is_none(raw):
    assertpy.assert_that(paging.decode_page_token(raw)).is_none()


@pytest.mark.parametrize("raw", ["PK", "[1, 2]", "{not json"])
def test_decode_page_token_refuses_what_is_not_a_token(raw):
    with pytest.raises(paging.PageTokenError):
        paging.decode_page_token(raw)


def test_encode_page_token_round_trips():
    assertpy.assert_that(paging.decode_page_token(paging.encode_page_token(TOKEN))).is_equal_to(TOKEN)
    assertpy.assert_that(paging.encode_page_token("already-a-string")).is_equal_to("already-a-string")
    assertpy.assert_that(paging.encode_page_token(None)).is_none()
    assertpy.assert_that(paging.encode_page_token({})).is_none()


def test_iterate_pages_reads_300_items_in_pages():
    items = list(range(300))

    def fetch(params):
        start = json.loads(params["nextToken"])["offset"] if "nextToken" in params else 0
        end = start + int(params["pageSize"])
        return {"items": items[start:end], "nextToken": {"offset": end} if end < len(items) else None}

    pages = list(paging.iterate_pages(fetch, {"pageSize": "100"}))

    assertpy.assert_that(pages).is_length(3)
    assertpy.assert_that([i for page in pages for i in page["items"]]).is_equal_to(items)


def test_iterate_pages_stops_on_a_repeated_token():
    with pytest.raises(RuntimeError):
        list(paging.iterate_pages(lambda params: {"nextToken": "same"}, {}))


def test_iterate_pages_stops_after_max_pages():
    counter = iter(range(10**6))
    with pytest.raises(RuntimeError):
        list(paging.iterate_pages(lambda params: {"nextToken": str(next(counter))}, {}, max_pages=5))
