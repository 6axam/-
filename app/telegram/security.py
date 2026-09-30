def is_owner(user_id: int | None, owner_telegram_id: int) -> bool:
    return user_id == owner_telegram_id
