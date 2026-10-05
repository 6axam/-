import asyncio
from collections import defaultdict

class IncomingBuffer:
    def __init__(self, delay, callback):
        self.delay,self.callback,self.items,self.tasks = delay,callback,defaultdict(list),{}
        self._content = defaultdict(dict)
    async def add(self, chat_id, message, user_content=None):
        self.items[chat_id].append(message)
        if user_content: self._content[chat_id][message.message_id] = user_content
        task = self.tasks.get(chat_id)
        if task: task.cancel()
        self.tasks[chat_id] = asyncio.create_task(self._flush_later(chat_id))
    async def _flush_later(self, chat_id):
        try:
            await asyncio.sleep(self.delay)
            messages = self.items.pop(chat_id, [])
            self.tasks.pop(chat_id, None)
            await self.callback(chat_id, messages)
        except asyncio.CancelledError: pass

    def content_for(self, chat_id, message_id):
        return self._content.get(chat_id, {}).get(message_id, [])

    def content_for_messages(self, chat_id, message_ids):
        content = self._content.get(chat_id, {})
        return {message_id: list(content.get(message_id, [])) for message_id in message_ids if content.get(message_id)}

    def clear_content(self, chat_id, message_ids):
        content = self._content.get(chat_id)
        if not content:
            return
        for message_id in message_ids:
            content.pop(message_id, None)
        if not content:
            self._content.pop(chat_id, None)
