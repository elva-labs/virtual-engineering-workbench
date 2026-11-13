"""Page tokens of VEW's list routes, and reading every page of one.

A list route answers `nextToken` as a JSON object (DynamoDB's LastEvaluatedKey) or as a JSON string of
one. A client sends it back as the `nextToken` query parameter, as JSON. Callers that need the whole
list (scheduled jobs, publishing to a program) read every page: a code cap of one page made program
101 invisible to them.
"""

import json
import typing
from urllib.parse import unquote

# Far above 300 programs at any page size; stops a server that never ends its pages.
MAX_PAGES = 10_000


class PageTokenError(ValueError):
    """The nextToken query parameter is not a page token."""


def decode_page_token(raw: str | None) -> dict | None:
    """The page token a caller sent in the query string: JSON, possibly percent-encoded once more."""
    if not raw:
        return None
    for candidate in (raw, unquote(raw)):
        try:
            token = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(token, str):
            # A token the route answered as a JSON string of the object.
            try:
                token = json.loads(token)
            except ValueError:
                continue
        if isinstance(token, dict):
            return token
    raise PageTokenError("nextToken is not a page token")


def encode_page_token(token: typing.Any) -> str | None:
    """The query-string form of a token a list route answered (an object or a JSON string of one)."""
    if token is None or token == "" or token == {}:
        return None
    if isinstance(token, str):
        return token
    return json.dumps(token, sort_keys=True, separators=(",", ":"))


def iterate_pages(
    fetch: typing.Callable[[dict], dict | None],
    query_params: dict,
    token_param: str = "nextToken",
    max_pages: int = MAX_PAGES,
) -> typing.Iterator[dict]:
    """Every page of a list route: `fetch(query_params)` once per page, until it answers no token."""
    seen: set[str] = set()
    token: str | None = None
    for _ in range(max_pages):
        params = dict(query_params)
        if token:
            params[token_param] = token
        response = fetch(params) or {}
        yield response
        token = encode_page_token(response.get(token_param))
        if not token:
            return
        if token in seen:
            raise RuntimeError(f"The list route repeated a page token after {len(seen)} pages")
        seen.add(token)
    raise RuntimeError(f"The list route did not end after {max_pages} pages")
