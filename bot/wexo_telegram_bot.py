#!/usr/bin/env python3
"""Sae Uploader Telegram bot + localhost pairing endpoint.

Setup:
  Windows PowerShell: $env:WEXO_BOT_TOKEN="TOKEN_FROM_BOTFATHER"; $env:WEXO_BOT_USERNAME="sakoportal_bot"; python wexo_telegram_bot.py

Bot commands (only these three):  /start   /me   /pruimer
"""
from __future__ import annotations
import json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, 'wexo_token.txt')   # paste the BotFather token here ONCE (or let START_SAKO.bat ask once)
TOKEN_RE = re.compile(r'^\d{5,}:[A-Za-z0-9_-]{20,}$')

def _read_token_file() -> str:
    try:
        with open(TOKEN_FILE, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'): return re.sub(r'\s+', '', line)
    except Exception:
        pass
    return ''

TOKEN = re.sub(r'\s+', '', os.environ.get('WEXO_BOT_TOKEN', '')) or _read_token_file()
BOT_USERNAME = os.environ.get('WEXO_BOT_USERNAME', 'sakoportal_bot').strip().lstrip('@')
PREMIUM_URL = 'https://t.me/Sakoaep'
SUPPORT_URL = 'https://t.me/Sakoaep'
# Where the pairing service listens. Local PC: 127.0.0.1:8765. On a server / hosting: set WEXO_HOST=0.0.0.0 (PORT is read automatically).
HOST = os.environ.get('WEXO_HOST', '127.0.0.1').strip() or '127.0.0.1'
PORT = int(os.environ.get('PORT') or os.environ.get('WEXO_PORT') or 8765)
AUTH_TTL = 15 * 60  # match the extension's 15-minute login window
API = f'https://api.telegram.org/bot{TOKEN}' if TOKEN else ''
DB_FILE = os.path.join(BASE_DIR, 'wexo_users.json')
LOCK = threading.RLock()
SESSIONS: dict[str, dict[str, Any]] = {}
PENDING: dict[str, dict[str, Any]] = {}   # nonce -> {'user_id':..., 'profile':...}
AUTH_STATE_FILE = os.path.join(BASE_DIR, 'wexo_auth_state.json')
OFFSET = 0

# Persist login challenges/results so a stale or duplicate polling process cannot
# lose the nonce between Telegram's button click and the extension's status poll.
def load_auth_state() -> dict[str, Any]:
    try:
        with open(AUTH_STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {'pending': {}, 'sessions': {}}

def save_auth_state() -> None:
    try:
        tmp = AUTH_STATE_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'pending': PENDING, 'sessions': SESSIONS}, f, ensure_ascii=False)
        os.replace(tmp, AUTH_STATE_FILE)
    except Exception as exc:
        print('Could not save login state:', type(exc).__name__, str(exc)[:120])

def refresh_auth_state() -> None:
    data = load_auth_state()
    with LOCK:
        PENDING.clear(); PENDING.update(data.get('pending') or {})
        SESSIONS.clear(); SESSIONS.update(data.get('sessions') or {})


KEYBOARD = None   # no reply keyboard any more: the bot only has /start, /me and /pruimer
REMOVE_KEYBOARD = {'remove_keyboard': True}   # clears the old Start / Download / Premium keyboard on people's phones

# ---------------------------------------------------------------- storage
def load_db() -> dict:
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception:
        return {'users': {}, 'admins': []}

def save_db(db: dict) -> None:
    with open(DB_FILE, 'w', encoding='utf-8') as f: json.dump(db, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- telegram helpers
def fatal(message: str) -> None:
    """Configuration problem that restarting cannot fix: START_SAKO.bat sees exit code 3 and does not loop."""
    print('\n' + message)
    sys.exit(3)

def ensure_token() -> None:
    """Token order: WEXO_BOT_TOKEN env -> bot/wexo_token.txt -> ask once and remember it in bot/wexo_token.txt."""
    global TOKEN, API
    if TOKEN and TOKEN_RE.match(TOKEN):
        API = f'https://api.telegram.org/bot{TOKEN}'; return
    if TOKEN:
        print('The saved token has a wrong format. Please enter it again.')
    if not sys.stdin or not sys.stdin.isatty():
        fatal('No valid bot token. Put the token from @BotFather in bot/wexo_token.txt (one line) and start again.')
    for _ in range(5):
        try: entered = re.sub(r'\s+', '', input('Paste the Telegram bot token from @BotFather (asked only once): '))
        except (EOFError, KeyboardInterrupt): fatal('No token entered.')
        if TOKEN_RE.match(entered):
            TOKEN = entered; API = f'https://api.telegram.org/bot{TOKEN}'
            try:
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f: f.write(TOKEN + '\n')
                print('Token saved to bot/wexo_token.txt - you will not be asked again.')
            except Exception as exc:
                print('Could not save the token file (' + str(exc)[:80] + '). It will be asked again next time.')
            return
        print('Token format looks incorrect. Copy the full API token from @BotFather.')
    fatal('Too many invalid attempts.')

def api(method: str, payload: dict[str, Any] | None = None) -> Any:
    if not API: raise RuntimeError('Set WEXO_BOT_TOKEN first.')
    body = json.dumps(payload or {}).encode()
    req = urllib.request.Request(API + '/' + method, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=40) as response: return json.loads(response.read().decode())

def btn(text: str, data: str | None = None, url: str | None = None, style: str | None = None) -> dict:
    b: dict[str, Any] = {'text': text}
    if url: b['url'] = url
    if data: b['callback_data'] = data
    if style: b['style'] = style  # 'success' = green, 'danger' = red, 'primary' = blue
    return b

# ---------------------------------------------------------------- Telegram (animated / custom) emoji
# Custom emoji are used when available; otherwise the normal emoji are shown, so nothing ever breaks.
# Telegram only lets bots send custom emoji if the bot owns an extra @username bought on Fragment.
# Source of the emoji (any of them):  WEXO_EMOJI_SETS=packname1,packname2   or a file  wexo_emoji.json  {"❤️": "emoji_id"}
EMOJI_MAP: dict[str, str] = {}
_EMOJI_PAT: Any = None
EMOJI_FILE = os.path.join(BASE_DIR, 'wexo_emoji.json')

def _rebuild_emoji_pattern() -> None:
    global _EMOJI_PAT
    if EMOJI_MAP:
        keys = sorted(EMOJI_MAP, key=len, reverse=True)
        _EMOJI_PAT = re.compile('(' + '|'.join(re.escape(k) for k in keys) + ')\ufe0f?')
    else:
        _EMOJI_PAT = None

def save_custom_emoji() -> None:
    try:
        tmp = EMOJI_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f: json.dump(EMOJI_MAP, f, ensure_ascii=False, indent=1)
        os.replace(tmp, EMOJI_FILE)
    except Exception as exc:
        print('Could not save wexo_emoji.json:', str(exc)[:100])

def load_custom_emoji() -> None:
    try:
        with open(EMOJI_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
        if isinstance(data, dict): EMOJI_MAP.update({str(k).replace('\ufe0f', ''): str(v) for k, v in data.items() if v})
    except Exception: pass
    packs = [n.strip() for n in os.environ.get('WEXO_EMOJI_SETS', '').split(',') if n.strip()]
    try:
        with open(os.path.join(BASE_DIR, 'wexo_emoji_packs.txt'), 'r', encoding='utf-8-sig') as f:
            packs += [ln.strip().rsplit('/', 1)[-1] for ln in f if ln.strip() and not ln.strip().startswith('#')]
    except Exception: pass
    for name in packs:
        try: learn_pack(name, override=False)
        except Exception as exc: print('Emoji pack skipped:', name, str(exc)[:120])
    _rebuild_emoji_pattern()
    if EMOJI_MAP: print(f'Custom Telegram emoji ready: {len(EMOJI_MAP)}')

def learn_pack(name: str, override: bool = True) -> int:
    """Reads a custom-emoji pack (t.me/addemoji/NAME) and maps each normal emoji to the pack's emoji."""
    n = 0
    for st in api('getStickerSet', {'name': name}).get('result', {}).get('stickers', []):
        base = (st.get('emoji') or '').replace('\ufe0f', '')
        if st.get('custom_emoji_id') and base and (override or base not in EMOJI_MAP):
            EMOJI_MAP[base] = st['custom_emoji_id']; n += 1
    return n

PACKS_FILE = os.path.join(BASE_DIR, 'wexo_emoji_packs.txt')
PACK_IDS_FILE = os.path.join(BASE_DIR, 'wexo_emoji_pack_ids.json')

def collect_pack_ids() -> dict[str, list[dict[str, str]]]:
    """Fetch the actual Telegram custom_emoji_id values for every configured pack."""
    packs: list[str] = []
    try:
        with open(PACKS_FILE, 'r', encoding='utf-8-sig') as f:
            packs = [ln.strip().rsplit('/', 1)[-1] for ln in f if ln.strip() and not ln.lstrip().startswith('#')]
    except OSError:
        pass
    report: dict[str, list[dict[str, str]]] = {}
    for name in packs:
        result = api('getStickerSet', {'name': name}).get('result', {})
        entries = []
        for sticker in result.get('stickers', []):
            emoji = (sticker.get('emoji') or '▫️').replace('\ufe0f', '')
            emoji_id = sticker.get('custom_emoji_id')
            if emoji_id:
                entries.append({'emoji': emoji, 'custom_emoji_id': str(emoji_id)})
        report[name] = entries
    tmp = PACK_IDS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PACK_IDS_FILE)
    return report

def format_pack_report(report: dict[str, list[dict[str, str]]]) -> str:
    lines = ['<b>Wexo · Telegram Custom Emoji IDs</b>']
    logos = {'TgAndroidIcons': '🤖', 'tgmacicons': '🍎', 'relynth_by_reTikTok_bot': '✨', 'adaptiveqp_by_emsetbot': '➜'}
    for name, entries in report.items():
        lines.append('\n' + logos.get(name, '🔹') + ' <b>' + html_escape(name) + '</b>')
        if not entries:
            lines.append('  لا توجد معرفات مسترجعة من هذه الحزمة.')
            continue
        for item in entries:
            lines.append(f"{html_escape(item['emoji'])}  <code>{html_escape(item['custom_emoji_id'])}</code>")
    return '\n'.join(lines)

def learn_from_message(msg: dict) -> int:
    """Admin sends a message containing Telegram (custom) emoji -> the bot remembers which emoji to use for each normal emoji."""
    n = 0
    for key in ('text', 'caption'):
        text = msg.get(key) or ''
        ents = msg.get('entities' if key == 'text' else 'caption_entities') or []
        raw = text.encode('utf-16-le')
        for e in ents:
            if e.get('type') != 'custom_emoji' or not e.get('custom_emoji_id'): continue
            base = raw[e['offset'] * 2:(e['offset'] + e['length']) * 2].decode('utf-16-le', 'ignore').replace('\ufe0f', '')
            if base: EMOJI_MAP[base] = e['custom_emoji_id']; n += 1
    return n

# Every emoji the bot writes is replaced by a Telegram custom emoji (never the phone's emoji):
#   1) exact emoji found in the packs  2) a close alias that exists in the packs  3) one default icon.
_EMOJI_RANGES = '\U0001F000-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2900-\u297F'
_ANY_EMOJI = re.compile('[' + _EMOJI_RANGES + '](?:\ufe0f|[\U0001F3FB-\U0001F3FF])?(?:\u200d[' + _EMOJI_RANGES + '](?:\ufe0f)?)*\ufe0f?')
EMOJI_ALIASES: dict[str, list[str]] = {
    '💞': ['❤', '💕', '💗', '💖'], '💕': ['❤', '💞'], '❤': ['💞', '💗'], '🚀': ['⚡', '✈', '🔥'], '❎': ['❌', '✖'],
    '🔐': ['🔒', '🔑'], '🔒': ['🔐', '🔑'], '🚫': ['❌', '⛔'], '❌': ['❎', '🚫', '✖'], '⏳': ['⌛', '🕒', '⏰'],
    '🔎': ['🔍'], '🛡': ['🔒', '✅'], '🛠': ['🔧', '⚙'], '📅': ['🗓', '📆'], '❔': ['❓'], '⬇': ['⬇', '⬆', '➡'],
    '➡': ['➜', '▶'], '↗': ['➜', '➡'], '➜': ['➡', '↗'], '🎶': ['🎵', '🎧'], '🎧': ['🎵', '🎶'], '🎵': ['🎶', '🎧'],
    '🎥': ['🎬', '📹'], '🎬': ['🎥', '📹'], '📹': ['🎥', '🎬'], '🎉': ['✨', '⭐'], '👑': ['⭐', '✨'], '⚠': ['❗', '❕'],
    '🔖': ['📌', '🏷'], '🌍': ['🌐', '🌎'], '📰': ['📄', '📃'], '📁': ['📂', '🗂'], '👁': ['👀'], '🆔': ['🔖', '📌'],
    '🔗': ['📎', '🌐'], '✨': ['⭐', '🌟'], '⭐': ['🌟', '✨'], '👤': ['👥', '🙂'], '🕒': ['⏰', '⌛'], '📊': ['📈', '📉'],
    '🛒': ['💳', '🛍'], '👉': ['➡', '➜'], '💬': ['📩', '✉'], '🤖': ['⚙', '🔧'], '🍎': ['🍏'], '✅': ['✔', '☑'],
    '💎': ['⭐', '✨'], '🔓': ['🔑', '🔒'],
}
def _norm(e: str) -> str:
    return re.sub('[\ufe0f\U0001F3FB-\U0001F3FF]', '', e)

_EMOJI_OFF_UNTIL = 0.0   # when Telegram refuses custom emoji, stop trying for a while (avoids sending every message twice = slow)

def emoji_active() -> bool:
    return bool(EMOJI_MAP) and time.time() >= _EMOJI_OFF_UNTIL

def default_emoji_id() -> str | None:
    if EMOJI_MAP.get('_default'): return EMOJI_MAP['_default']
    for k in ('⭐', '✨', '✅', '🔹', '➜'):
        if EMOJI_MAP.get(k): return EMOJI_MAP[k]
    return next((v for k, v in EMOJI_MAP.items() if not k.startswith('_')), None)

def resolve_emoji(e: str) -> str | None:
    """Telegram custom-emoji id for ANY emoji (exact -> alias -> default icon)."""
    if not EMOJI_MAP: return None
    base = _norm(e)
    if base in EMOJI_MAP: return EMOJI_MAP[base]
    first = base[:1]
    for cand in [first] + EMOJI_ALIASES.get(base, []) + EMOJI_ALIASES.get(first, []):
        if cand in EMOJI_MAP: return EMOJI_MAP[cand]
    return default_emoji_id()

def emojify(html_text: str) -> str:
    if not EMOJI_MAP: return html_text
    parts = re.split(r'(<[^>]*>)', html_text)
    for i, part in enumerate(parts):
        if part.startswith('<'): continue
        def _sub(m):
            eid = resolve_emoji(m.group(0))
            return f'<tg-emoji emoji-id="{eid}">{m.group(0)}</tg-emoji>' if eid else m.group(0)
        parts[i] = _ANY_EMOJI.sub(_sub, part)
    return ''.join(parts)

def prep_markup(markup: dict | None) -> dict | None:
    """Inline buttons: the emoji in a button label becomes the button's Telegram custom-emoji icon (no phone emoji is left in the text)."""
    if not markup or not emoji_active() or 'inline_keyboard' not in markup: return markup
    rows, changed = [], False
    for row in markup['inline_keyboard']:
        new_row = []
        for b in row:
            nb = dict(b); text = nb.get('text', '')
            found = _ANY_EMOJI.search(text)
            if found:
                eid = resolve_emoji(found.group(0))
                rest = re.sub(r'\s{2,}', ' ', _ANY_EMOJI.sub('', text)).strip()
                if eid and rest:
                    nb['text'] = rest; nb['icon_custom_emoji_id'] = eid; changed = True
            new_row.append(nb)
        rows.append(new_row)
    return {**markup, 'inline_keyboard': rows} if changed else markup

def rich(text: str, parse_mode: str | None = None) -> tuple[str, str | None]:
    """Returns (text, parse_mode) with custom emoji applied when they are available."""
    if not emoji_active(): return text, parse_mode
    if parse_mode != 'HTML': text = html_escape(text)
    return emojify(text), 'HTML'

def _send_with_fallback(method: str, base: dict, text: str, parse_mode: str | None) -> None:
    """1st try: Telegram custom emoji (text + button icons). 2nd try: normal emoji, so nothing ever breaks."""
    attempts = [(rich(text, parse_mode), prep_markup(base.get('reply_markup')))]
    if attempts[0][0] != (text, parse_mode) or attempts[0][1] is not base.get('reply_markup'):
        attempts.append(((text, parse_mode), base.get('reply_markup')))
    for i, ((t, pm), markup) in enumerate(attempts):
        data = {**base, 'text': t}
        if markup is not None: data['reply_markup'] = markup
        if pm: data['parse_mode'] = pm
        try:
            api(method, data)
            if i == 1:
                global _EMOJI_OFF_UNTIL
                _EMOJI_OFF_UNTIL = time.time() + 900   # custom emoji were refused but plain text worked -> skip them for 15 min
                print('Telegram refused custom emoji (bot needs a Fragment username). Using normal emoji for 15 minutes.')
            return
        except Exception:
            if i == len(attempts) - 1: raise

def send_message(chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> None:
    base: dict[str, Any] = {'chat_id': chat_id}
    if reply_markup is not None: base['reply_markup'] = reply_markup
    _send_with_fallback('sendMessage', base, text, parse_mode)

def edit_message(chat_id: int, message_id: int, text: str) -> None:
    try: _send_with_fallback('editMessageText', {'chat_id': chat_id, 'message_id': message_id}, text, None)
    except Exception: pass

def answer_cb(cb_id: str, text: str = '') -> None:
    try: api('answerCallbackQuery', {'callback_query_id': cb_id, 'text': text})
    except Exception: pass

def html_escape(value: str) -> str:
    import html
    return html.escape(str(value), quote=False)

# ---------------------------------------------------------------- admin panel
def get_photo_id(user_id: int) -> str | None:
    try:
        r = api('getUserProfilePhotos', {'user_id': user_id, 'limit': 1}).get('result', {})
        if r.get('total_count'): return r['photos'][0][-1]['file_id']
    except Exception: pass
    return None

def _fmt_time(ts: Any) -> str:
    try: return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(float(ts)))
    except Exception: return 'Unknown'

LEGEND_LABEL = 'Legende'   # the word shown to the user (extension profile + bot). Change it here if you want another spelling.

def is_legend(u: dict) -> bool:
    return u.get('role') == 'legend'

def is_premium(u: dict) -> bool:
    if is_legend(u):
        return True
    enabled = bool(u.get('premium')) or u.get('role') == 'premium'
    if not enabled:
        return False
    expires = u.get('premium_expires_at')
    try:
        if expires and float(expires) <= time.time():
            return False
    except (TypeError, ValueError):
        return False
    return True

def user_text(uid: str, u: dict) -> str:
    """All the account information as text (no profile picture)."""
    uname = f"@{html_escape(u['username'])}" if u.get('username') else 'no username'
    premium = is_premium(u)
    lines = [f"👤 <b>{html_escape(u.get('first_name') or 'User')}</b>",
             f'🆔 ID: <code>{html_escape(uid)}</code>',
             f'🔗 Username: {uname}',
             f"⭐ Plan: {'Pruimer ✅' if premium and not is_legend(u) else ('Legend ∞' if is_legend(u) else 'Free 🔒 (closed)')}",
             f"🛠 Role: {LEGEND_LABEL if is_legend(u) else 'User'}",
             f"🕒 Last login: {_fmt_time(u.get('at'))}"]
    if u.get('premium_at'): lines.append(f"📅 Premium since: {_fmt_time(u['premium_at'])}")
    return '\n'.join(lines)

def user_markup(uid: str, u: dict) -> dict:
    label = 'Pruimer ✅' if is_premium(u) else 'Pruimer'
    legend = 'Legend ✅' if is_legend(u) else 'Legend'
    return {'inline_keyboard': [[btn(label, f'role:p:{uid}', style='success'), btn(legend, f'role:d:{uid}', style='primary')]]}

def show_users(chat_id: int) -> None:
    users = load_db()['users']
    if not users:
        send_message(chat_id, 'No registered users yet.'); return
    for uid, u in users.items():
        send_message(chat_id, user_text(uid, u), user_markup(uid, u), parse_mode='HTML')
        time.sleep(0.05)  # stay under Telegram's flood limit

ROLE_NAMES = {'p': 'Pruimer', 'd': 'Legend'}
def pruimer_welcome(label: str, until: Any) -> str:
    return ('🎉 Welcome to Sae Pro!\n\n🚀 Your ' + label + ' Pruimer access is now active.\n'
            '📅 Until: ' + _fmt_time(until) + '\n💞 Thank you for supporting Sae Uploader!')

ROLE_WELCOME = {
    'p': pruimer_welcome('10-day', time.time() + 10 * 86400),
    'd': '👑 Welcome to Sae Uploader ' + LEGEND_LABEL + '!\n\n🚀 Your Legend lifetime access is now active.\n💞 Thank you for being part of Sae Uploader!',
}

def is_admin(user_id: int) -> bool:
    return user_id in load_db().get('admins', [])

def handle_callback(cq: dict) -> None:
    data = cq.get('data') or ''
    user = cq.get('from') or {}
    uid = user.get('id')
    msg = cq.get('message') or {}
    chat_id = (msg.get('chat') or {}).get('id')
    msg_id = msg.get('message_id')
    answer_cb(cq['id'])
    if not chat_id: return

    if data.startswith('login:yes:') or data.startswith('login:no:') or data in ('login:yes', 'login:no'):
        # Carry the exact nonce in the button callback. This avoids expired/mismatched
        # requests when a user starts a second login or has multiple extension windows open.
        parts = data.split(':', 2)
        action = parts[1] if len(parts) > 1 else ''
        nonce = parts[2] if len(parts) > 2 else ''
        with LOCK:
            refresh_auth_state()
            pending = PENDING.get(nonce) if nonce else None
            # Backward compatibility for old callback buttons without the nonce.
            if pending is None and data in ('login:yes', 'login:no'):
                pair = next(((k, v) for k, v in PENDING.items() if v.get('user_id') == uid), None)
                if pair:
                    nonce, pending = pair
            # Do not remove a pending request until its Telegram user has been verified.
            callback_nonce = nonce
            expired = bool(pending and time.time() - float(pending.get('created', 0)) > AUTH_TTL)
            if (not pending or pending.get('user_id') != uid or expired) and uid is not None:
                # If a user tapped an older confirmation message, recover only to that same
                # Telegram user's newest unexpired pending request. Never cross account IDs.
                candidates = [(k, v) for k, v in PENDING.items()
                              if v.get('user_id') == uid and time.time() - float(v.get('created', 0)) <= AUTH_TTL]
                if candidates:
                    nonce, pending = max(candidates, key=lambda pair: float(pair[1].get('created', 0)))
                    expired = False
            if not pending or pending.get('user_id') != uid or expired:
                if pending and expired:
                    PENDING.pop(nonce, None); save_auth_state()
                send_message(chat_id, 'This login request expired or belongs to another Telegram account. Return to the extension, click Login again, and confirm the newest message from the same Telegram account within 15 minutes.', KEYBOARD)
                return
            PENDING.pop(nonce, None)
            profile = pending['profile']
            if action == 'yes':
                # Persist the verified Telegram identity and the nonce result together.
                db = load_db()
                old = db['users'].get(str(uid), {})
                db['users'][str(uid)] = {**old, **profile, 'at': time.time()}
                save_db(db)
                SESSIONS[nonce] = {'authenticated': True, 'user': profile, 'at': time.time()}
                # If an old button was tapped, also satisfy the exact nonce being polled by
                # that extension window, but only after the same Telegram user confirms.
                if callback_nonce and callback_nonce != nonce:
                    SESSIONS[callback_nonce] = {'authenticated': True, 'user': profile, 'at': time.time()}
            else:
                SESSIONS[nonce] = {'authenticated': False, 'denied': True, 'at': time.time()}
                if callback_nonce and callback_nonce != nonce:
                    SESSIONS[callback_nonce] = {'authenticated': False, 'denied': True, 'at': time.time()}
            save_auth_state()
        if action == 'yes':
            # Photo lookup is optional and must not block the successful login result.
            try:
                photo = get_photo_id(uid)
                if photo:
                    with LOCK:
                        db = load_db(); old = db['users'].get(str(uid), {})
                        old['photo'] = photo; db['users'][str(uid)] = old; save_db(db)
            except Exception as exc:
                print('Optional profile photo lookup failed:', type(exc).__name__)
            send_message(chat_id, "✅ You're signed in to Sae Uploader — 💞 please go back to the extension.", KEYBOARD)
        else:
            send_message(chat_id, '🚫 Login was cancelled. If this was not you, you can ignore it.', KEYBOARD)
        try: api('editMessageReplyMarkup', {'chat_id': chat_id, 'message_id': msg_id, 'reply_markup': {'inline_keyboard': []}})
        except Exception: pass
        return

    if data.startswith('gp:'):
        # Owner picked 1month / 3month for the ID he typed.
        if not owner_unlocked(user): return
        try: _, k, target = data.split(':')
        except ValueError: return
        if k not in PLAN_CHOICES: return
        label, days = PLAN_CHOICES[k]
        notify_until = None
        with LOCK:
            db = load_db()
            u = db['users'].get(target)
            if u is None:
                result = f'❌ ID {target} not found.'
            elif is_legend(u):
                result = f'👑 ID {target} is Legend already (lifetime).'
            else:
                now = time.time()
                try: current = float(u.get('premium_expires_at') or 0)
                except (TypeError, ValueError): current = 0.0
                start = current if (is_premium(u) and current > now) else now   # a renewal is added after the time that is left
                u['premium'] = True
                u['premium_at'] = u.get('premium_at') if (is_premium(u) and u.get('premium_at')) else now
                u['premium_expires_at'] = start + days * 86400
                u['premium_plan'] = label
                save_db(db)
                notify_until = u['premium_expires_at']
                result = f'✅ Pruimer {label} is active for ID {target}.\n📅 Until: {_fmt_time(notify_until)}'
        if notify_until:
            try: send_message(int(target), pruimer_welcome(label, notify_until), KEYBOARD)
            except Exception: pass
        edit_message(chat_id, msg_id, result)
        try: api('editMessageReplyMarkup', {'chat_id': chat_id, 'message_id': msg_id, 'reply_markup': {'inline_keyboard': []}})
        except Exception: pass
        return

    if not is_admin(uid): return

    if data.startswith('role:'):
        _, r, target = data.split(':')
        u = load_db()['users'].get(target) or {}
        who = f"@{u['username']}" if u.get('username') else f"{u.get('first_name') or 'User'} ({target})"
        if r == 'p':
            send_message(chat_id, f'هل يريد المؤسس @Sakoaep فتح بريميوم لحساب {who} ؟',
                         {'inline_keyboard': [[btn('Unlock', f'pr:y:{target}', style='success'), btn('No unlock', f'pr:n:{target}', style='danger')]]})
        else:
            if is_legend(u):
                send_message(chat_id, f'{who} هو {LEGEND_LABEL} حاليا. هل تريد إزالة رتبة {LEGEND_LABEL} وإغلاق Premium له ؟',
                             {'inline_keyboard': [[btn('Remove', f'rm:d:{target}', style='danger'), btn('Keep', 'cancel', style='success')]]})
            else:
                send_message(chat_id, f'هل يريد المؤسس @Sakoaep جعل {who} {LEGEND_LABEL} وفتح Premium له ؟',
                             {'inline_keyboard': [[btn('Unlock', f'ok:{r}:{target}', style='success'), btn('No unlock', 'cancel', style='danger')]]})
    elif data.startswith('pr:'):
        _, choice, target = data.split(':')
        notify = False
        with LOCK:
            db = load_db()
            u = db['users'].get(target)
            if u is None:
                result = '❌ User not found.'
            elif choice == 'y':
                notify = not is_premium(u)
                u['premium'] = True; u['premium_at'] = time.time(); u['premium_expires_at'] = time.time() + 10 * 86400; save_db(db)
                result = f'✅ Premium is open for ID {target}.'
            else:
                u['premium'] = False; u.pop('premium_at', None); u.pop('premium_expires_at', None)
                if u.get('role') == 'premium': u['role'] = None  # clear the older-style premium flag too
                save_db(db)
                result = f'🔒 Premium is closed for ID {target}.'
        if notify:
            try: send_message(int(target), pruimer_welcome('10-day', time.time() + 10 * 86400), KEYBOARD)
            except Exception: pass
        edit_message(chat_id, msg_id, result)
    elif data.startswith('ok:'):
        _, r, target = data.split(':')
        found = False
        with LOCK:
            db = load_db()
            if target in db['users']:
                usr = db['users'][target]
                usr['role'] = ROLE_NAMES[r].lower()
                if r == 'd':  # Legende => Premium is opened automatically
                    usr['premium'] = True; usr.setdefault('premium_at', time.time())
                save_db(db); found = True
        if found:
            try: send_message(int(target), ROLE_WELCOME[r], KEYBOARD)
            except Exception: pass
        edit_message(chat_id, msg_id, f'✅ {LEGEND_LABEL if r == "d" else ROLE_NAMES[r]} opened for ID {target}' + (' (Premium is open too).' if r == 'd' else '.'))
    elif data.startswith('rm:'):
        _, r, target = data.split(':')
        with LOCK:
            db = load_db(); usr = db['users'].get(target)
            if usr is not None:
                usr['role'] = None; usr['premium'] = False; usr.pop('premium_at', None); save_db(db)
        edit_message(chat_id, msg_id, f'🔒 {LEGEND_LABEL} and Premium are closed for ID {target}.')
    elif data == 'cancel':
        edit_message(chat_id, msg_id, '❎ Cancelled.')

# ---------------------------------------------------------------- owner tools (@Sakoaep only)
OWNER_USERNAME = 'sakoaep'   # the Telegram account @Sakoaep
OWNER_CODE = 'superrida5'
STATUS_ICON = {'free': '🔒', 'pruimer': '💎', 'legend': '👑'}
PLAN_CHOICES = {'1': ('1month', 30), '3': ('3month', 90)}   # callback key -> (button text, days)

def user_status(u: dict) -> str:
    if is_legend(u): return 'legend'
    return 'pruimer' if is_premium(u) else 'free'

def is_owner(user: dict) -> bool:
    """Only the Telegram account @Sakoaep. The numeric ID is pinned the first time he unlocks; a pin that was made for a
    different username (e.g. the previous owner) is ignored, so it can never lock @Sakoaep out."""
    if (user.get('username') or '').lower() != OWNER_USERNAME: return False
    db = load_db()
    pinned = db.get('owner_id')
    if pinned and (db.get('owner_username') or '').lower() == OWNER_USERNAME:
        try: return int(pinned) == user.get('id')
        except (TypeError, ValueError): return True
    return True

def owner_unlocked(user: dict) -> bool:
    return is_owner(user) and user.get('id') in load_db().get('admins', [])

def unlock_owner(user: dict) -> None:
    with LOCK:
        db = load_db()
        db['owner_id'] = user.get('id')
        db['owner_username'] = OWNER_USERNAME
        db.setdefault('admins', [])
        if user.get('id') not in db['admins']: db['admins'].append(user.get('id'))
        save_db(db)

def send_users_list(chat_id: int) -> None:
    users = load_db().get('users', {})
    if not users:
        send_message(chat_id, '👥 No registered users yet.'); return
    counts = {'free': 0, 'pruimer': 0, 'legend': 0}
    lines = []
    for uid, u in users.items():
        st = user_status(u); counts[st] += 1
        lines.append(f"ID : <code>{html_escape(uid)}</code> / {st} {STATUS_ICON[st]}")
    head = f"👥 <b>Registered users: {len(users)}</b>\n🔒 free {counts['free']}  ·  💎 pruimer {counts['pruimer']}  ·  👑 legend {counts['legend']}\n"
    chunk, size, first = [head], len(head), True
    for ln in lines:
        if size + len(ln) > 3600:
            send_message(chat_id, '\n'.join(chunk), parse_mode='HTML'); chunk, size = [], 0
        chunk.append(ln); size += len(ln) + 1
    if chunk: send_message(chat_id, '\n'.join(chunk), parse_mode='HTML')

def owner_card(uid: str, u: dict) -> str:
    st = user_status(u)
    name = ((u.get('first_name') or '') + (' ' + u['last_name'] if u.get('last_name') else '')).strip() or 'User'
    lines = [f"👤 Name: <b>{html_escape(name)}</b>",
             f"🔗 Username: {'@' + html_escape(u['username']) if u.get('username') else 'no username'}",
             f"🆔 ID: <code>{html_escape(uid)}</code>",
             f"⭐ Status: <b>{st}</b> {STATUS_ICON[st]}",
             f"🕒 Last login: {_fmt_time(u.get('at'))}"]
    if st == 'pruimer' and u.get('premium_expires_at'): lines.append(f"📅 Pruimer until: {_fmt_time(u['premium_expires_at'])}")
    elif st == 'legend': lines.append('📅 Legend: lifetime')
    if u.get('premium_at') and st != 'free': lines.append(f"📅 Since: {_fmt_time(u['premium_at'])}")
    return '\n'.join(lines)

def send_owner_card(chat_id: int, uid: str, u: dict) -> None:
    caption, markup = owner_card(uid, u), user_markup(uid, u)
    photo = get_photo_id(int(uid)) or u.get('photo')
    if photo:
        base = {'chat_id': chat_id, 'photo': photo, 'reply_markup': markup}
        for cap, pm, mk in ((*rich(caption, 'HTML'), prep_markup(markup)), (caption, 'HTML', markup)):
            try: api('sendPhoto', {**base, 'caption': cap, 'parse_mode': pm, 'reply_markup': mk}); return
            except Exception: continue
    send_message(chat_id, caption, markup, parse_mode='HTML')

ID_QUERY_RE = re.compile(r'^(?:id\s*[:：]?\s*)?(\d{5,16})(?:\s*/.*)?$', re.I)

def handle_owner_text(msg: dict, user: dict, text: str) -> bool:
    """Owner flow (only the Telegram account @Sakoaep):
         1) he sends the code           -> the bot checks that the account really is @Sakoaep
         2) the bot asks                -> "write the ID you want to give Pruimer to"
         3) he writes the ID            -> "Choose" + two green buttons: 1month / 3month
         4) he taps one                 -> that user gets Pruimer for that long (see handle_callback, 'gp:')
       Returns True when the message was handled."""
    chat_id = msg['chat']['id']
    if text.strip().lower() == OWNER_CODE.lower():
        if not is_owner(user):
            shown = ('@' + user['username']) if user.get('username') else 'an account without a username'
            send_message(chat_id, f'⛔ The code is right, but this is {shown}. Only @Sakoaep can use it (set a Telegram username first if you have none).')
            return True
        unlock_owner(user)
        try: api('deleteMessage', {'chat_id': chat_id, 'message_id': msg.get('message_id')})
        except Exception: pass
        send_message(chat_id, '✅ Verified: @Sakoaep\n\n🆔 Write the ID of the user you want to give Pruimer to.')
        return True
    m = ID_QUERY_RE.match(text)
    if m and is_owner(user) and owner_unlocked(user):
        uid = m.group(1)
        u = load_db().get('users', {}).get(uid)
        if not u:
            send_message(chat_id, f'❌ ID : <code>{uid}</code> not found.\nThe user must press Connect in the extension first.', parse_mode='HTML')
        else:
            who = f"@{u['username']}" if u.get('username') else (u.get('first_name') or 'User')
            send_message(chat_id, f'Choose\n👤 {html_escape(who)}  ·  🆔 <code>{uid}</code>',
                         {'inline_keyboard': [[btn('1month', f'gp:1:{uid}', style='success'), btn('3month', f'gp:3:{uid}', style='success')]]},
                         parse_mode='HTML')
        return True
    return False

# ---------------------------------------------------------------- the three commands
LINE = '━━━━━━━━━━━━━━━━━━'
HELP_TEXT = ('✦ <b>SAE METHOD</b>\n' + LINE + '\n'
             'Available commands:\n'
             '▫️ /start — welcome\n'
             '▫️ /me — your account\n'
             '▫️ /pruimer — Pruimer plan')

def _full_name(user: dict) -> str:
    name = ((user.get('first_name') or '') + (' ' + user['last_name'] if user.get('last_name') else '')).strip()
    return name or 'User'

def plan_name(user_id: Any) -> str:
    """Real plan of this Telegram account, read from the same database the admin panel / extension use."""
    u = load_db().get('users', {}).get(str(user_id)) or {}
    st = user_status(u)
    return {'legend': 'Legend', 'pruimer': 'Pruimer'}.get(st, 'Free')

def send_welcome(chat_id: int, user: dict) -> None:
    text = ('Welcome to Sako L_bot 👋\n'
            '──────────────\n'
            '✅ You are successfully registered in the add-on.\n'
            '──────────────\n'
            'Check your account information, and if you want Premium, contact me '
            '<a href="https://t.me/Sakoaep">here</a>. 🚀')
    send_message(chat_id, text, REMOVE_KEYBOARD, parse_mode='HTML')

def send_me(chat_id: int, user: dict) -> None:
    """Account card built from the Telegram user who sent the command (never fixed / fake data)."""
    uname = f"@{html_escape(user['username'])}" if user.get('username') else 'No username'
    text = ('👤 <b>My Account</b>\n' + LINE + '\n'
            f"<b>Name:</b> {html_escape(_full_name(user))}\n"
            f'<b>Username:</b> {uname}\n'
            f"<b>Telegram ID:</b> <code>{html_escape(user.get('id'))}</code>\n"
            f"<b>Plan:</b> {plan_name(user.get('id'))}")
    send_message(chat_id, text, parse_mode='HTML')

def send_pruimer_menu(chat_id: int, user: dict) -> None:
    """The existing Pruimer menu (same Buy button / same wording as the old Premium menu)."""
    text = ('✨ <b>SAE METHOD — Pruimer</b>\n' + LINE + '\n'
            '🚀 Unlock Pruimer access and extra features.\n'
            '🔒 Pruimer is activated by the Sae team.\n'
            f"⭐ Your plan: <b>{plan_name(user.get('id'))}</b>\n\n"
            '💞 Thank you for supporting Sae Uploader!')
    send_message(chat_id, text, {'inline_keyboard': [[btn('Buy Pruimer 🛒', url=PREMIUM_URL, style='success')]]}, parse_mode='HTML')

# ---------------------------------------------------------------- messages
def handle_message(msg: dict) -> None:
    chat = msg.get('chat') or {}; chat_id = chat.get('id')
    if not chat_id: return
    text = (msg.get('text') or '').strip()
    user = msg.get('from') or {}

    if handle_owner_text(msg, user, text): return
    # Admin access is granted only through the persisted admin ID list. The only enrollment is the owner code, and it works only for @Sakoaep.

    # Admin sends Telegram emoji (or a t.me/addemoji/... link) -> the bot uses exactly those emoji from now on
    if user.get('id') in load_db().get('admins', []):
        pack = re.search(r't\.me/add(?:emoji|stickers)/([A-Za-z0-9_]+)', text)
        learned = 0
        try:
            if pack: learned = learn_pack(pack.group(1))
            learned += learn_from_message(msg)
        except Exception as exc:
            send_message(chat_id, '⚠️ Could not read that emoji pack: ' + str(exc)[:120]); return
        if learned:
            _rebuild_emoji_pattern(); save_custom_emoji()
            send_message(chat_id, f'✅ Telegram emoji saved: {learned} (total {len(EMOJI_MAP)}). The bot now uses them in every message and button.', KEYBOARD)
            return

    # The bot understands ONLY these three commands (also "/cmd@sakoportal_bot").
    command = ''
    if text.startswith('/'):
        command = text.split(maxsplit=1)[0].split('@', 1)[0].lower()
    payload = text.split(maxsplit=1)[1].strip() if command and len(text.split(maxsplit=1)) > 1 else ''

    if command == '/start':
        if payload.startswith('login_'):
            nonce = payload[len('login_'):]
            if 24 <= len(nonce) <= 100 and all(c in '0123456789abcdef' for c in nonce.lower()):
                profile = {'id': user.get('id'), 'username': user.get('username'), 'first_name': user.get('first_name') or 'Wexo User', 'last_name': user.get('last_name'), 'language_code': user.get('language_code'), 'is_premium': bool(user.get('is_premium'))}
                with LOCK:
                    refresh_auth_state()
                    PENDING[nonce] = {'nonce': nonce, 'user_id': user.get('id'), 'profile': profile, 'created': time.time()}
                    save_auth_state()
                send_message(chat_id, '🔐 Are you the one trying to sign in to the Sae Uploader extension?',
                             {'inline_keyboard': [[btn('✅ Yes, it\'s me', 'login:yes:' + nonce, style='success')],
                                                  [btn('❌ No, it\'s not me', 'login:no:' + nonce, style='danger')]]})
                return
        if payload.lower() == 'pruimer':   # the extension's "Buy Pruimer" button opens t.me/<bot>?start=pruimer
            send_pruimer_menu(chat_id, user); return
        send_welcome(chat_id, user); return
    if command == '/me':
        send_me(chat_id, user); return
    if command == '/pruimer':
        send_pruimer_menu(chat_id, user); return

    # anything else (old keyboard buttons, links, files...): no extra features, just point to the three commands
    send_message(chat_id, 'Please use the available commands or check the menu to continue 🔄', REMOVE_KEYBOARD)


# ---------------------------------------------------------------- local pairing endpoint (unchanged)
class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS'); self.send_header('Access-Control-Allow-Headers', 'Content-Type'); self.end_headers()
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path in ('/', '/health'): self.send_json(200, {'ok': True, 'service': 'wexo-bot'}); return
        if parsed.path == '/auth/photo':
            # Serve the authenticated Telegram user's avatar through the local bot service.
            # The bot token remains server-side and is never returned to the extension.
            try:
                uid = int(params.get('user_id', ['0'])[0])
                if uid <= 0: self.send_json(400, {'error': 'invalid_user'}); return
                if str(uid) not in load_db()['users']: self.send_response(404); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); return
                file_id = get_photo_id(uid)
                if not file_id: self.send_response(404); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); return
                file_info = api('getFile', {'file_id': file_id}).get('result', {})
                file_path = file_info.get('file_path')
                if not file_path: self.send_response(404); self.end_headers(); return
                request = urllib.request.Request(f'https://api.telegram.org/file/bot{TOKEN}/{file_path}', headers={'User-Agent': 'Wexo-Enhancer/1.5.4'})
                with urllib.request.urlopen(request, timeout=8) as response: image_data = response.read(5 * 1024 * 1024 + 1)
                if not image_data or len(image_data) > 5 * 1024 * 1024: self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header('Content-Type', 'image/jpeg'); self.send_header('Cache-Control', 'no-store'); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Content-Length', str(len(image_data))); self.end_headers(); self.wfile.write(image_data)
            except Exception:
                self.send_response(404); self.end_headers()
            return
        if parsed.path == '/auth/role':
            # The extension asks whether Premium is open for this Telegram user (Yes/No in the admin panel).
            try: uid = str(int(params.get('user_id', ['0'])[0]))
            except Exception: self.send_json(400, {'error': 'invalid_user'}); return
            db = load_db()
            u = db['users'].get(uid)
            leg = bool(u and is_legend(u))
            if u and not leg and (bool(u.get('premium')) or u.get('role') == 'premium') and not u.get('premium_expires_at'):
                start = float(u.get('premium_at') or time.time())
                u['premium_expires_at'] = start + 10 * 86400
                save_db(db)
            self.send_json(200, {'known': bool(u), 'premium': bool(u and is_premium(u)), 'legend': leg, 'developer': False,
                                 'expires_at': (u.get('premium_expires_at') if u and not leg and is_premium(u) else None),
                                 'label': LEGEND_LABEL if leg else ''})
            return
        if parsed.path != '/auth/status': self.send_json(404, {'error': 'not_found'}); return
        nonce = params.get('nonce', [''])[0]
        with LOCK:
            refresh_auth_state()
            record = SESSIONS.get(nonce)
            if record and time.time() - record['at'] > AUTH_TTL:
                SESSIONS.pop(nonce, None); save_auth_state(); record = None
        self.send_json(200, record or {'authenticated': False})
    def send_json(self, code, obj):
        data = json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *_): pass

def poll_telegram():
    global OFFSET
    while True:
        try:
            result = api('getUpdates', {'offset': OFFSET, 'timeout': 25, 'allowed_updates': ['message', 'callback_query']})
            for update in result.get('result', []):
                OFFSET = max(OFFSET, update['update_id'] + 1)
                try:
                    if update.get('message'): handle_message(update['message'])
                    elif update.get('callback_query'): handle_callback(update['callback_query'])
                except Exception as exc:
                    print('Handler error:', exc)
        except Exception as exc:
            print('Telegram polling error:', exc); time.sleep(3)

def main():
    ensure_token()
    # Optional proxy/VPN for networks where Telegram is slow or blocked:  WEXO_PROXY=http://127.0.0.1:10809
    proxy = os.environ.get('WEXO_PROXY', '').strip()
    if proxy:
        os.environ['HTTPS_PROXY'] = proxy; os.environ['HTTP_PROXY'] = proxy
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy})))
    me: dict = {}
    for attempt in range(1, 1000):  # do not crash when the connection to Telegram is slow: keep retrying
        try:
            me = api('getMe', {}).get('result', {}); break
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                try: os.remove(TOKEN_FILE)
                except Exception: pass
                fatal('Invalid bot token (401). The saved token was removed - start again and paste the full token from @BotFather.')
            print(f'Telegram answered {exc.code}. Retrying...'); time.sleep(5)
        except Exception as exc:
            print(f'Cannot reach Telegram (attempt {attempt}): {type(exc).__name__}. Check your internet / VPN. Retrying in 5s...'); time.sleep(5)
    actual = (me.get('username') or '').lower()
    if not actual:
        fatal('Telegram getMe did not return a bot username. Check the token in BotFather.')
    if actual != BOT_USERNAME.lower():
        fatal(f'Wrong bot token: this token belongs to @{actual}, but this build opens @{BOT_USERNAME}. Keep your existing token file safe and replace its contents with the token created for @{BOT_USERNAME} in BotFather.')
    print(f'Bot token verified: @{actual}')
    try: api('deleteWebhook', {'drop_pending_updates': False})  # getUpdates fails (409) if a webhook is set
    except Exception as exc: print('deleteWebhook skipped:', exc)
    try:   # the command menu in Telegram shows exactly these three commands and nothing else
        api('setMyCommands', {'commands': [
            {'command': 'start', 'description': 'Welcome to SAE METHOD'},
            {'command': 'me', 'description': 'Your account info'},
            {'command': 'pruimer', 'description': 'Pruimer plan'}]})
    except Exception as exc: print('setMyCommands skipped:', exc)
    refresh_auth_state()
    load_custom_emoji()
    try:
        pack_report = collect_pack_ids()
        print('Telegram custom emoji IDs saved:', sum(len(v) for v in pack_report.values()))
    except Exception as exc:
        print('Could not export custom emoji pack IDs:', type(exc).__name__, str(exc)[:120])
    threading.Thread(target=poll_telegram, daemon=True).start()
    try: server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc: fatal(f'Port {PORT} is already in use ({exc.__class__.__name__}). Close the other Wexo window (only one may run) and start again.')
    print(f'Sae Telegram pairing service is running for @{actual or BOT_USERNAME} on http://{HOST}:{PORT}')
    print('Keep this running: the extension signs users in through this service. Press Ctrl+C to stop.')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
