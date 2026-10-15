from __future__ import annotations

import argparse
import asyncio
import getpass

from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport


def _delivery_name(value) -> str:
    names = {
        "SentCodeTypeApp": "сообщение в Telegram на уже авторизованном устройстве",
        "SentCodeTypeSms": "SMS",
        "SentCodeTypeCall": "звонок Telegram",
        "SentCodeTypeMissedCall": "пропущенный звонок Telegram",
        "SentCodeTypeFlashCall": "flash-call Telegram",
        "SentCodeTypeFragmentSms": "SMS",
    }
    return names.get(type(value).__name__, "канал, выбранный Telegram")


def _qr_terminal_art(login_url: str) -> str:
    """Render a local, scan-only QR code without printing the sensitive URL."""
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("QR login needs qrcode. Run: ./.venv/bin/pip install -r requirements.txt") from exc

    code = qrcode.QRCode(
        border=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    code.add_data(login_url)
    code.make(fit=True)
    return "\n".join("".join("██" if cell else "  " for cell in row) for row in code.get_matrix())


async def _login(transport: MTProtoTransport, *, force_sms: bool = False) -> None:
    client = await transport.connect()
    if await client.is_user_authorized():
        print("MTProto session already authorized."); return
    phone = input("Telegram number for Anya account: ").strip()
    if not phone: raise RuntimeError("Phone number is required locally to request a login code.")
    sent = await client.send_code_request(phone, force_sms=force_sms)
    print(f"Telegram requested delivery via: {_delivery_name(sent.type)}.")
    if getattr(sent, "next_type", None):
        print(f"Fallback, if needed: {_delivery_name(sent.next_type)}.")
    code = input("Telegram login code: ").strip()
    try:
        await client.sign_in(phone=phone, code=code)
    except Exception as exc:
        if exc.__class__.__name__ != "SessionPasswordNeededError": raise
        await client.sign_in(password=getpass.getpass("Telegram 2FA password: "))
    print("MTProto session authorized and saved locally.")


async def _login_qr(transport: MTProtoTransport) -> None:
    """Authorize only after a scan from an existing official Telegram client."""
    client = await transport.connect()
    if await client.is_user_authorized():
        print("MTProto session already authorized.")
        return

    print("Open Telegram on Anya's already authorized phone: Settings → Devices → Link Desktop Device.")
    print("Scan the QR below. It expires quickly; a fresh one is shown automatically.")
    while True:
        qr_login = await client.qr_login()
        print(_qr_terminal_art(qr_login.url))
        try:
            await qr_login.wait()
        except asyncio.TimeoutError:
            print("QR expired; generating a fresh code…")
            continue
        print("MTProto session authorized and saved locally.")
        return


async def _smoke(transport: MTProtoTransport, confirmed: bool) -> None:
    if not confirmed: raise RuntimeError("Refusing to send. Re-run with --confirm-saved-messages after reviewing the target.")
    if transport.settings.telegram_mtproto_dry_run: raise RuntimeError("Set TELEGRAM_MTPROTO_DRY_RUN=false locally before a live Saved Messages smoke test.")
    client = await transport.connect()
    if not await client.is_user_authorized(): raise RuntimeError("Run login first.")
    me = await client.get_me()
    sent = await client.send_message("me", "Anya MTProto smoke test")
    print(f"Saved Messages smoke test sent (message_id={sent.id}, account_id_suffix={str(me.id)[-4:]}).")


async def main_async(args) -> None:
    transport = MTProtoTransport(MTProtoSettings())
    try:
        if args.command == "login": await _login(transport, force_sms=args.force_sms)
        elif args.command == "login-qr": await _login_qr(transport)
        elif args.command == "status": print(await transport.status())
        elif args.command == "smoke-test": await _smoke(transport, args.confirm_saved_messages)
        else: raise RuntimeError("logout is intentionally manual: remove the local session only after you confirm the exact path.")
    finally: await transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Anya's local MTProto utility")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login"); login.add_argument("--force-sms", action="store_true", help="Request SMS when Telegram permits it")
    sub.add_parser("login-qr", help="Authorize by scanning a terminal QR from an already signed-in Telegram app")
    sub.add_parser("status")
    smoke = sub.add_parser("smoke-test"); smoke.add_argument("--confirm-saved-messages", action="store_true")
    sub.add_parser("logout")
    try:
        asyncio.run(main_async(parser.parse_args()))
    except KeyboardInterrupt:
        print("\nMTProto login cancelled.")

if __name__ == "__main__": main()
