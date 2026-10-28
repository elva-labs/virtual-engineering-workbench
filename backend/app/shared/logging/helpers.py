import copy

DICT_KEY_HEADERS = "headers"
DICT_KEY_MULTI_VALUE_HEADERS = "multiValueHeaders"
DICT_KEY_AUTHORIZATION = "Authorization"
DICT_KEY_REQUEST_CONTEXT = "requestContext"
DICT_KEY_AUTHORIZER = "authorizer"
DICT_KEY_USER_EMAIL = "userEmail"
DICT_KEY_USER_NAME = "userName"
MASKED_JWT = "***"


def _mask_header(headers: dict, header_name: str) -> None:
    for name in list(headers):
        if name.lower() == header_name.lower():
            value = headers[name]
            headers[name] = [MASKED_JWT for _ in value] if isinstance(value, list) else MASKED_JWT


def clear_auth_headers(event: dict, *, mask_body: bool = False) -> dict:
    log_dict = copy.deepcopy(event)

    if DICT_KEY_HEADERS in log_dict and log_dict[DICT_KEY_HEADERS]:
        _mask_header(log_dict[DICT_KEY_HEADERS], DICT_KEY_AUTHORIZATION)
        _mask_header(log_dict[DICT_KEY_HEADERS], "Idempotency-Key")

    if DICT_KEY_MULTI_VALUE_HEADERS in log_dict and log_dict[DICT_KEY_MULTI_VALUE_HEADERS]:
        _mask_header(log_dict[DICT_KEY_MULTI_VALUE_HEADERS], DICT_KEY_AUTHORIZATION)
        _mask_header(log_dict[DICT_KEY_MULTI_VALUE_HEADERS], "Idempotency-Key")

    if mask_body and "body" in log_dict and log_dict["body"] is not None:
        log_dict["body"] = MASKED_JWT

    if DICT_KEY_REQUEST_CONTEXT in log_dict and DICT_KEY_AUTHORIZER in log_dict[DICT_KEY_REQUEST_CONTEXT]:
        authorizer = log_dict[DICT_KEY_REQUEST_CONTEXT][DICT_KEY_AUTHORIZER]
        if DICT_KEY_USER_EMAIL in authorizer:
            authorizer[DICT_KEY_USER_EMAIL] = MASKED_JWT
        if DICT_KEY_USER_NAME in authorizer:
            authorizer[DICT_KEY_USER_NAME] = MASKED_JWT

    return log_dict
