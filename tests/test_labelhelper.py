import pytest

from core.labelhelper import LabelHelper


class FakeSearchResult:
    id = "doc-1"
    sourcefile = "hello-confidential-1.docx"
    sourcepage = None
    sensitivity_label = "defa4170-0d19-0005-0005-bc88714345d2"


@pytest.mark.asyncio
async def test_extract_labels_resolves_guid_with_app_token(monkeypatch):
    helper = LabelHelper(
        tenant_id="tenant-id",
        server_app_id="server-app-id",
        server_app_secret="server-app-secret",
    )
    monkeypatch.setattr(helper, "_get_app_graph_token", lambda: "app-token")

    async def fake_get_label_data(label_id, access_token, token_source):
        assert label_id == "defa4170-0d19-0005-0005-bc88714345d2"
        assert access_token == "app-token"
        assert token_source == "app-only"
        return {
            "name": "Confidential",
            "displayName": "Confidential",
            "priority": 5,
            "color": "#0078d4",
        }

    monkeypatch.setattr(helper, "_get_label_data", fake_get_label_data)

    document_labels = await helper.extract_labels_from_search_results([FakeSearchResult()])

    assert len(document_labels) == 1
    label = document_labels[0].label
    assert label.id == "defa4170-0d19-0005-0005-bc88714345d2"
    assert label.name == "Confidential"
    assert label.display_name == "Confidential"
    assert label.priority == 5
