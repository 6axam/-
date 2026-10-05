import asyncio
from types import SimpleNamespace

from app.llm.schemas import ImageContent
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


async def test_buffered_vision_content_survives_flush_with_message_association_and_is_cleared():
    received = []
    first = ImageContent(data=b"first", mime_type="image/png")
    second = ImageContent(data=b"second", mime_type="image/png")

    class Manager:
        async def handle_turn(self, chat_id, message_ids, user_content):
            received.append((chat_id, message_ids, user_content))

    manager = Manager()

    async def callback(chat_id, messages):
        message_ids = [message.message_id for message in messages]
        associated = buffer.content_for_messages(chat_id, message_ids)
        content = [frame for message_id in message_ids for frame in associated.get(message_id, [])]
        await manager.handle_turn(chat_id, message_ids, content)
        buffer.clear_content(chat_id, message_ids)

    buffer = IncomingBuffer(.01, callback)
    await buffer.add(1, SimpleNamespace(message_id=10), user_content=[first])
    await buffer.add(1, SimpleNamespace(message_id=11), user_content=[second])
    await asyncio.sleep(.03)

    assert received == [(1, [10, 11], [first, second])]
    assert buffer.content_for_messages(1, [10, 11]) == {}

    await buffer.add(1, SimpleNamespace(message_id=12))
    await asyncio.sleep(.03)
    assert received[-1] == (1, [12], [])
