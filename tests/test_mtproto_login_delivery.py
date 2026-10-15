from app.telegram.mtproto.__main__ import _delivery_name, _login_qr, _qr_terminal_art


def test_mtproto_login_delivery_labels_do_not_include_phone_or_credentials():
    app = type("SentCodeTypeApp", (), {})()
    sms = type("SentCodeTypeSms", (), {})()
    assert _delivery_name(app) == "сообщение в Telegram на уже авторизованном устройстве"
    assert _delivery_name(sms) == "SMS"


def test_qr_terminal_art_keeps_the_login_url_out_of_terminal_text():
    login_url = "tg://login?token=local-test-token"
    art = _qr_terminal_art(login_url)

    assert login_url not in art
    assert "██" in art
    assert len(art.splitlines()) > 10


class _FakeQrLogin:
    url = "tg://login?token=local-test-token"

    async def wait(self):
        return object()


class _FakeQrClient:
    def __init__(self):
        self.qr_requested = False

    async def is_user_authorized(self):
        return False

    async def qr_login(self):
        self.qr_requested = True
        return _FakeQrLogin()


class _FakeQrTransport:
    def __init__(self):
        self.client = _FakeQrClient()

    async def connect(self):
        return self.client


async def test_qr_login_authorizes_only_after_the_qr_wait_completes(capsys):
    transport = _FakeQrTransport()

    await _login_qr(transport)

    assert transport.client.qr_requested
    output = capsys.readouterr().out
    assert "Link Desktop Device" in output
    assert "authorized and saved locally" in output
    assert _FakeQrLogin.url not in output
