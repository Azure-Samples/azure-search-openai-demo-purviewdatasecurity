import logging
import os
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from quart import abort, current_app, request

from config import CONFIG_AUTH_CLIENT, CONFIG_SEARCH_CLIENT
from core.authentication import AuthError
from error import error_response


async def path_authorized_by_search(path: str, auth_claims: dict[str, Any]) -> bool:
    access_token = auth_claims.get("access_token")
    if not access_token:
        return False

    search_client = current_app.config[CONFIG_SEARCH_CLIENT]
    path_without_fragment = path.split("#", 1)[0]
    path_candidates = [path_without_fragment, os.path.basename(path_without_fragment)]
    filter_clauses = []
    for candidate in dict.fromkeys(path_candidates):
        escaped_candidate = candidate.replace("'", "''")
        filter_clauses.append(f"sourcefile eq '{escaped_candidate}'")
        filter_clauses.append(f"sourcepage eq '{escaped_candidate}'")

    results = await search_client.search(
        search_text="*",
        top=1,
        filter=" or ".join(filter_clauses),
        x_ms_query_source_authorization=access_token,
    )
    async for _ in results:
        return True
    return False


def authenticated_path(route_fn: Callable[[str, dict[str, Any]], Any]):
    """
    Decorator for routes that request a specific file.
    """

    @wraps(route_fn)
    async def auth_handler(path=""):
        auth_helper = current_app.config[CONFIG_AUTH_CLIENT]
        authorized = True
        try:
            auth_claims = await auth_helper.get_auth_claims_if_enabled(request.headers)
            if auth_helper.require_access_control:
                authorized = await path_authorized_by_search(path, auth_claims)
        except AuthError:
            abort(403)
        except Exception as error:
            logging.exception("Problem checking authentication %s", error)
            return error_response(error, route="/content")

        if not authorized:
            abort(403)

        return await route_fn(path, auth_claims)

    return auth_handler


_C = TypeVar("_C", bound=Callable[..., Any])


def authenticated(route_fn: _C) -> _C:
    """
    Decorator for routes that might require access control. Unpacks Authorization header information into an auth_claims dictionary
    """

    @wraps(route_fn)
    async def auth_handler(*args, **kwargs):
        auth_helper = current_app.config[CONFIG_AUTH_CLIENT]
        try:
            auth_claims = await auth_helper.get_auth_claims_if_enabled(request.headers)
        except AuthError:
            abort(403)
        except Exception as error:
            logging.exception("Problem checking authentication %s", error)
            return error_response(error, route=request.path)

        return await route_fn(auth_claims, *args, **kwargs)

    return cast(_C, auth_handler)
