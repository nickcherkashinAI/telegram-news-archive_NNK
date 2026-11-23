"""One-time local interactive login. NEVER commit .env.local or any Telegram session."""
from getpass import getpass
from pathlib import Path
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

OUT = Path(__file__).resolve().parent / '.env.local'


def main():
    if OUT.exists():
        print('Файл .env.local уже существует. Чтобы не перезаписать сессию, запуск отменён.')
        return
    print('Однократная авторизация через Telegram MTProto. Данные вводятся только на твоём компьютере.')
    api_id = int(input('API ID (my.telegram.org): ').strip())
    api_hash = getpass('API hash (ввод скрыт): ').strip()
    if not api_hash:
        raise ValueError('Пустой API hash')
    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        client.start(
            phone=lambda: input('Номер Telegram в международном формате (+...): ').strip(),
            password=lambda: getpass('Пароль облачной двухфакторной защиты, если включена: '),
        )
        me = client.get_me()
        session = client.session.save()
        # Keep secrets on the computer and out of console output / shell history.
        fd = os.open(str(OUT), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(f'TG_API_ID={api_id}\nTG_API_HASH={api_hash}\nTG_SESSION={session}\n')
        print(f'Авторизация успешна (Telegram ID: {me.id}). Секреты записаны в .env.local.')
        print('Не отправляй файл в ChatGPT, Telegram, GitHub-коммиты или третьим лицам.')
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
