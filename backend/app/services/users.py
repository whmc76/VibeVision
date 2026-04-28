from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def get_or_create_telegram_user(
    db: Session,
    *,
    telegram_id: str,
    username: str | None = None,
    display_name: str | None = None,
) -> User:
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user and username:
        user = db.scalar(select(User).where(User.username == username, User.telegram_id.is_(None)))
        if user:
            user.telegram_id = telegram_id

    if user:
        if username:
            user.username = username
        if display_name:
            user.display_name = display_name
        db.add(user)
        db.flush()
        return user

    user = User(
        telegram_id=telegram_id,
        username=username,
        display_name=display_name,
    )
    db.add(user)
    db.flush()
    return user
