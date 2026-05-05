from azure.core.exceptions import HttpResponseError

from error import (
    ERROR_MESSAGE_SEARCH_DELEGATED_USER_TENANT,
    error_dict,
    is_search_delegated_user_tenant_error,
)


def test_search_delegated_user_tenant_error_message():
    error = HttpResponseError(
        message=(
            "Invalid header: 'x-ms-query-source-authorization'. DelegatedUserEmail is invalid, "
            "please confirm that the user is in the same tenant as the Azure Search service."
        )
    )

    assert is_search_delegated_user_tenant_error(error)
    assert error_dict(error) == {"error": ERROR_MESSAGE_SEARCH_DELEGATED_USER_TENANT}


def test_other_http_response_errors_use_generic_message():
    error = HttpResponseError(message="A different Azure service error")

    assert not is_search_delegated_user_tenant_error(error)
    assert "Error type: <class 'azure.core.exceptions.HttpResponseError'>" in error_dict(error)["error"]
