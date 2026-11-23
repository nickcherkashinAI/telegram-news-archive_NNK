"""Archive ALL available MTProto channel posts since the checkpoint and publish a daily manifest.

Dependencies: Telethon >=1.40,<2. No paid APIs. Does not download media binaries.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'archive'
REPORTS = ROOT / 'reports'
STATE = ROOT / 'state.json'
MOSCOW = ZoneInfo('Europe/Moscow')
UTC = timezone.utc
CHUNK_SIZE = 40
MAX_PER_CHANNEL = 50000
CHANNEL_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')


def read_env_local():
    """Read local secrets for first local test; GitHub uses Actions Secrets instead."""
    p = ROOT / '.env.local'
    if not p.exists():
        return
    for line in p.read_text('utf-8').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        k, v = line.split('=', 1)
        if k in ('TG_API_ID', 'TG_API_HASH', 'TG_SESSION'):
            os.environ.setdefault(k, v.strip())


def dump_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + '\n', encoding='utf-8')
    temp.replace(path)


def load_json(path, default):
    return json.loads(path.read_text('utf-8')) if path.exists() else default


def normalize_channel(value):
    if not isinstance(value, str):
        raise ValueError('Имя канала должно быть текстом')
    v = value.strip().replace('https://t.me/', '').replace('http://t.me/', '').removeprefix('@').strip('/')
    if not CHANNEL_RE.fullmatch(v):
        raise ValueError(f'Неверное имя публичного канала: {value!r}')
    return v.lower()


def parse_iso(s):
    dt = datetime.fromisoformat(s)
    if not dt.tzinfo:
        raise ValueError('Ожидается UTC-датировка с часовым поясом')
    return dt.astimezone(UTC)


def moscow_date(dt):
    return dt.astimezone(MOSCOW).date().isoformat()


def record_from_message(channel, m):
    dt = m.date
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    media = getattr(m, 'media', None)
    edit = getattr(m, 'edit_date', None)
    if edit is not None and edit.tzinfo is None:
        edit = edit.replace(tzinfo=UTC)
    return {
        'channel': channel,
        'id': int(m.id),
        'date_utc': dt.isoformat(),
        'date_msk': dt.astimezone(MOSCOW).isoformat(),
        'url': f'https://t.me/{channel}/{m.id}',
        'text': (getattr(m, 'message', None) or ''),
        'media_type': type(media).__name__ if media is not None else None,
        'album_grouped_id': str(m.grouped_id) if getattr(m, 'grouped_id', None) is not None else None,
        'edit_date_utc': edit.astimezone(UTC).isoformat() if edit else None,
    }


def merge_records(channel, records):
    by_day = {}
    for r in records:
        day = moscow_date(parse_iso(r['date_utc']))
        by_day.setdefault(day, []).append(r)
    for day, day_records in by_day.items():
        f = DATA / channel / f'{day}.json'
        old = load_json(f, [])
        merged = {int(item['id']): item for item in old}
        merged.update({int(item['id']): item for item in day_records})
        dump_json(f, sorted(merged.values(), key=lambda r: (r['date_utc'], r['id'])))


def report_window(now_utc):
    """Latest COMPLETED 07:00-06:59 Moscow reporting interval."""
    now_msk = now_utc.astimezone(MOSCOW)
    anchor = datetime.combine(now_msk.date(), time(7, 0), MOSCOW)
    if now_msk < anchor:
        anchor -= timedelta(days=1)
    return (anchor - timedelta(days=1)).astimezone(UTC), anchor.astimezone(UTC)


def produce_report(channels, last_success, now_utc):
    start, end = report_window(now_utc)
    # A useful human date, the date the reporting interval ENDS.
    date_key = end.astimezone(MOSCOW).date().isoformat()
    repo = os.environ.get('GITHUB_REPOSITORY', '').strip()
    branch = os.environ.get('GITHUB_REF_NAME', 'main').strip() or 'main'
    all_channels = []
    total = 0
    for channel in channels:
        arr = []
        day = start.astimezone(MOSCOW).date()
        last_day = (end - timedelta(microseconds=1)).astimezone(MOSCOW).date()
        while day <= last_day:
            arr.extend(load_json(DATA / channel / f'{day.isoformat()}.json', []))
            day += timedelta(days=1)
        arr = [r for r in arr if start <= parse_iso(r['date_utc']) < end]
        arr.sort(key=lambda r: (r['date_utc'], r['id']))
        total += len(arr)
        parts = []
        for pos in range(0, len(arr), CHUNK_SIZE):
            n = pos // CHUNK_SIZE + 1
            rel = f'reports/daily/{date_key}/{channel}-part-{n:03d}.json'
            body = {'channel': channel, 'period_start_utc': start.isoformat(),
                    'period_end_exclusive_utc': end.isoformat(), 'messages': arr[pos:pos+CHUNK_SIZE]}
            dump_json(ROOT / rel, body)
            parts.append({
                'path': rel,
                'url': f'https://raw.githubusercontent.com/{repo}/{branch}/{rel}' if repo else None,
                'count': len(body['messages']),
                'sha256': hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(),
            })
        refreshed = parse_iso(last_success[channel])
        all_channels.append({
            'channel': channel, 'message_count': len(arr), 'parts': parts,
            'last_success_utc': refreshed.isoformat(),
            # This flag is only a successful post-window fetch, NOT an absolute completeness proof.
            'post_window_collection_confirmed': refreshed >= end,
        })
    manifest = {
        'version': 1, 'source': 'Telegram MTProto user API (Telethon)',
        'reporting_date_msk': date_key,
        'period_start_msk': start.astimezone(MOSCOW).isoformat(),
        'period_end_exclusive_msk': end.astimezone(MOSCOW).isoformat(),
        'generated_at_utc': now_utc.isoformat(),
        'total_messages': total,
        'all_channels_fetched_after_window': all(x['post_window_collection_confirmed'] for x in all_channels),
        'channels': all_channels,
        'limitations': [
            'Cannot recover posts deleted before they were first collected',
            'Binary photos, videos, audio and documents are not downloaded; message captions and media types are saved',
            'Successful API fetch does not independently prove total absence of unavailable/hidden messages',
            'Scheduled GitHub Actions runs may be delayed or skipped',
        ],
    }
    dump_json(REPORTS / 'latest_manifest.json', manifest)
    dump_json(REPORTS / 'daily' / date_key / 'manifest.json', manifest)
    md = [f'# Дайджест: {date_key} (МСК)', '',
          f'Период: {manifest["period_start_msk"]} — до {manifest["period_end_exclusive_msk"]} (не включая конец)',
          f'Всего сообщений: {total}',
          f'После окончания периода все каналы считаны: {manifest["all_channels_fetched_after_window"]}', '']
    for entry in all_channels:
        md.append(f'## @{entry["channel"]}: {entry["message_count"]} сообщений')
        for part in entry['parts']:
            md.append(f'- {part["path"]} ({part["count"]} шт.)' + (f' — {part["url"]}' if part['url'] else ''))
    md.extend(['', 'Обработка: прочитать ВСЕ перечисленные JSON-файлы, сверить суммы по каждому каналу. Если какой-либо файл недоступен, не заявлять о полном обзоре.', ''])
    (REPORTS / 'latest_index.md').write_text('\n'.join(md), encoding='utf-8')
    return manifest


async def run():
    read_env_local()
    needed = ('TG_API_ID', 'TG_API_HASH', 'TG_SESSION')
    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        raise RuntimeError('Отсутствуют секреты: ' + ', '.join(missing) + '. Сначала запусти login.py.')
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import FloodWaitError
    conf = load_json(ROOT / 'channels.json', [])
    if not isinstance(conf, list) or not conf:
        raise ValueError('channels.json должен содержать непустой список')
    channels = list(dict.fromkeys(normalize_channel(x) for x in conf))
    state = load_json(STATE, {'last_success': {}})
    last_success = dict(state.get('last_success', {}))
    now = datetime.now(UTC)
    # First run: backfill 72 hours; subsequent runs: since last successfully captured interval minus 3h overlap.
    # A long outage automatically catches up without an artificial time limit.
    plan = {}
    for channel in channels:
        prior = last_success.get(channel)
        plan[channel] = (parse_iso(prior) - timedelta(hours=3)) if prior else (now - timedelta(hours=72))
    client = TelegramClient(StringSession(os.environ['TG_SESSION']),
                            int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'])
    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError('Telegram-сессия не авторизована. Выполни login.py повторно.')
        for channel in channels:
            oldest = plan[channel]
            received = []
            count_scanned = 0
            try:
                entity = await client.get_entity(channel)
                async for msg in client.iter_messages(entity, limit=None):
                    if not getattr(msg, 'date', None):
                        continue
                    dt = msg.date.replace(tzinfo=UTC) if msg.date.tzinfo is None else msg.date.astimezone(UTC)
                    if dt < oldest:
                        break
                    count_scanned += 1
                    if count_scanned > MAX_PER_CHANNEL:
                        raise RuntimeError(f'@{channel}: больше {MAX_PER_CHANNEL} сообщений за период; нужен ручной backfill')
                    received.append(record_from_message(channel, msg))
            except FloodWaitError as e:
                raise RuntimeError(f'@{channel}: Telegram требует паузу {e.seconds} секунд; контрольный запуск неуспешен') from e
            merge_records(channel, received)
            # Update checkpoint only after a full fetch for this channel.
            last_success[channel] = now.isoformat()
            print(f'@{channel}: считано {len(received)} доступных сообщений с {oldest.isoformat()}')
    dump_json(STATE, {'last_success': last_success})
    manifest = produce_report(channels, last_success, now)
    print('Отчёт:', manifest['reporting_date_msk'], '| сообщений:', manifest['total_messages'],
          '| все каналы считаны после окончания периода:', manifest['all_channels_fetched_after_window'])
    print('Индекс: reports/latest_index.md; манифест: reports/latest_manifest.json')


def main():
    try:
        asyncio.run(run())
    except Exception as e:
        print('ОШИБКА СБОРА:', type(e).__name__, str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
