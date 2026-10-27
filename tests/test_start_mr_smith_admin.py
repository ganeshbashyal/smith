from scripts import start_mr_smith_admin


def test_available_port_uses_next_free_port(monkeypatch):
    busy = {8000, 8001}

    class FakeSocket:
        def __init__(self, *_args):
            self.port = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def bind(self, address):
            self.port = address[1]
            if self.port in busy:
                raise OSError("address in use")

    monkeypatch.setattr(start_mr_smith_admin.socket, "socket", FakeSocket)

    assert start_mr_smith_admin.available_port(8000) == 8002


def test_admin_token_check_sends_bearer_header(monkeypatch):
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(start_mr_smith_admin.urllib.request, "urlopen", fake_urlopen)

    assert start_mr_smith_admin.admin_token_works("http://127.0.0.1:8001", "private-token")
    assert seen == {
        "url": "http://127.0.0.1:8001/api/admin/services",
        "authorization": "Bearer private-token",
        "timeout": 5,
    }


def test_admin_token_check_rejects_http_error(monkeypatch):
    def unauthorized(*_args, **_kwargs):
        raise start_mr_smith_admin.urllib.error.HTTPError(
            "http://127.0.0.1:8001/api/admin/services",
            401,
            "Unauthorized",
            {},
            None,
        )

    monkeypatch.setattr(start_mr_smith_admin.urllib.request, "urlopen", unauthorized)

    assert not start_mr_smith_admin.admin_token_works(
        "http://127.0.0.1:8001", "invalid-token"
    )
