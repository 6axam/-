import asyncio
from types import SimpleNamespace

from app.telegram.buffer import IncomingBuffer


async def test_debounce_aggregates_messages_into_one_callback():
    received = []

    async def callback(chat_id, messages):
        received.append((chat_id, [message.text for message in messages]))

    buffer = IncomingBuffer(.02, callback)
    await buffer.add(1, SimpleNamespace(text="короче"))
    await asyncio.sleep(.005)
    await buffer.add(1, SimpleNamespace(text="она не работает"))
    await asyncio.sleep(.04)
    assert received == [(1, ["короче", "она не работает"])]
