from aiogram import Bot


def create_bot(token: str) -> Bot:
    return Bot(token)
