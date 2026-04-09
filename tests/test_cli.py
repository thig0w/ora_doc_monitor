import cli


def test_read_json_returns_expected_keys():
    data = cli.read_json()
    assert "auth_req" in data
    assert "noauth_req" in data


def test_read_json_auth_req_structure():
    data = cli.read_json()
    for entry in data["auth_req"]:
        assert "desc" in entry
        assert "doc_id" in entry


def test_read_json_noauth_req_structure():
    data = cli.read_json()
    for entry in data["noauth_req"]:
        assert "desc" in entry
        assert "doc_id" in entry
        assert entry["doc_id"].startswith("http")
