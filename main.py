import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import psutil
from dotenv import load_dotenv
from pyrogram import Client as UserClient, enums
from pyrogram.errors import FloodWait, RPCError

load_dotenv()


def env_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        if default is None:
            raise RuntimeError(f"Environment variable {name} wajib diisi")
        return default
    return int(value)


API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH") or ""
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
OWNER_ID = env_int("OWNER_ID")
BOT_NAME = os.getenv("BOT_NAME", "Toko JasebKuy")
DB_PATH = Path(os.getenv("DB_PATH", "jasebkuy.sqlite3"))
SESSION_DIR = Path(os.getenv("SESSION_DIR", "sessions"))
DROP_PENDING_UPDATES = os.getenv("DROP_PENDING_UPDATES", "1") != "0"

if not API_HASH:
    raise RuntimeError("Environment variable API_HASH wajib diisi")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN wajib diisi")

SESSION_DIR.mkdir(parents=True, exist_ok=True)
state_db: dict[int, dict[str, str]] = {}
db_lock = asyncio.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id TEXT PRIMARY KEY,
    jeda_grup INTEGER NOT NULL DEFAULT 2,
    jeda_loop INTEGER NOT NULL DEFAULT 60,
    report_dest_type TEXT NOT NULL DEFAULT 'bot',
    report_pm_id INTEGER NOT NULL,
    report_pm_username TEXT NOT NULL DEFAULT 'Owner'
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    phone TEXT,
    session TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def db_exec(fn):
    async with db_lock:
        return await asyncio.to_thread(fn)


async def init_db() -> None:
    def work():
        with _connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """
                INSERT OR IGNORE INTO settings
                (id, jeda_grup, jeda_loop, report_dest_type, report_pm_id, report_pm_username)
                VALUES ('global', 2, 60, 'bot', ?, 'Owner')
                """,
                (OWNER_ID,),
            )
            conn.commit()

    await db_exec(work)


async def get_conf() -> dict:
    await init_db()

    def work():
        with _connect() as conn:
            row = conn.execute("SELECT * FROM settings WHERE id = 'global'").fetchone()
            return dict(row)

    return await db_exec(work)


async def update_conf(**fields) -> None:
    allowed = {"jeda_grup", "jeda_loop", "report_dest_type", "report_pm_id", "report_pm_username"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return

    def work():
        with _connect() as conn:
            sets = ", ".join(f"{k} = ?" for k in clean)
            conn.execute(f"UPDATE settings SET {sets} WHERE id = 'global'", tuple(clean.values()))
            conn.commit()

    await db_exec(work)


async def count_users() -> int:
    def work():
        with _connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    return await db_exec(work)


async def get_active_users() -> list[dict]:
    def work():
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]

    return await db_exec(work)


def get_ram_info() -> str:
    ram = psutil.virtual_memory()
    return f"{ram.total >> 30}GB"


class BotAPI:
    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    async def call(self, method: str, payload: dict | None = None) -> dict:
        assert self.session is not None
        async with self.session.post(f"{self.base_url}/{method}", json=payload or {}) as resp:
            data = await resp.json(content_type=None)
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API {method} failed: {data}")
            return data["result"]

    async def get_updates(self, offset: int | None = None, timeout: int = 50, limit: int = 50) -> list[dict]:
        payload: dict[str, Any] = {"timeout": timeout, "limit": limit, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return await self.call("getUpdates", payload)

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call("sendMessage", payload)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None):
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.call("editMessageText", payload)

    async def answer_callback(self, callback_query_id: str, text: str = "", show_alert: bool = False):
        return await self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert})

    async def get_me(self):
        return await self.call("getMe")

    async def get_chat(self, chat_id: str | int):
        return await self.call("getChat", {"chat_id": chat_id})


def ik_button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def markup(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def main_menu_buttons(user_id: int) -> dict:
    btns = [
        [ik_button("🛍️ Order Produk", "noop"), ik_button("💳 Isi Saldo", "noop")],
        [ik_button("🚀 Install Userbot", "ask_num")],
    ]
    if user_id == OWNER_ID:
        btns.append([ik_button("📊 Dashboard Admin", "admin_stats")])
    return markup(btns)


def main_menu_text() -> str:
    return (
        "🔥 **UBOT MANAGER V1** 🔥\n\n"
        "Status VPS: ONLINE ✅\n"
        f"RAM: {get_ram_info()} 🚀\n\n"
        f"✨ **Selamat Datang di {BOT_NAME}** ✨\n"
        "Silakan pilih menu di bawah untuk bertransaksi."
    )


async def send_main_menu(api: BotAPI, chat_id: int, user_id: int):
    await api.send_message(chat_id, main_menu_text(), reply_markup=main_menu_buttons(user_id))


async def show_admin_stats(api: BotAPI, chat_id: int, message_id: int):
    c = await get_conf()
    total_acc = await count_users()
    text = (
        "📊 **PANEL ADMIN TOKO**\n"
        f"Total Akun: `{total_acc}`\n"
        f"Jeda Grup: `{c['jeda_grup']}s` | Putaran: `{c['jeda_loop']}s`\n"
        f"Tujuan Laporan: `{c['report_pm_username']}`"
    )
    btns = markup([
        [ik_button("📢 Broadcast Massal", "start_bc")],
        [ik_button("📊 Laporan Broadcast", "report_menu")],
        [ik_button("⚙️ Set Jeda", "ask_delay")],
        [ik_button("⬅️ Kembali", "back")],
    ])
    await api.edit_message_text(chat_id, message_id, text, reply_markup=btns)


async def show_report_menu(api: BotAPI, chat_id: int, message_id: int):
    c = await get_conf()
    status = f"PM -> @{c['report_pm_username']}" if c["report_dest_type"] == "pm" else "Ke Bot Owner (Default)"
    text = (
        "📊 **Pengaturan Laporan Broadcast**\n\n"
        "Tentukan kemana laporan hasil broadcast akan dikirim.\n\n"
        "📍 **Status Saat Ini:**\n"
        f"💬 {status}"
    )
    btns = markup([
        [ik_button("🤖 Ke Bot Owner", "set_dest_bot")],
        [ik_button("💬 Ke PM Username", "ask_dest_pm")],
        [ik_button("🔙 Kembali", "admin_stats")],
    ])
    await api.edit_message_text(chat_id, message_id, text, reply_markup=btns)


@asynccontextmanager
async def userbot_client(user: dict):
    session_name = str(SESSION_DIR / f"ubot_{user['id']}")
    ubot = UserClient(session_name, session_string=user["session"], api_id=API_ID, api_hash=API_HASH)
    try:
        await ubot.start()
        yield ubot
    finally:
        try:
            await ubot.stop()
        except Exception:
            pass


async def safe_send_to_group(ubot: UserClient, chat_id: int, text: str) -> bool:
    try:
        await ubot.send_message(chat_id, text)
        return True
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 1)
    except RPCError:
        return False
    except Exception:
        return False
    return False


async def run_broadcast(api: BotAPI, chat_id: int, text_bc: str):
    c = await get_conf()
    start_t = time.time()
    users = await get_active_users()
    total_sent = 0
    total_groups = 0
    failed_accounts = 0

    status_msg = await api.send_message(chat_id, f"⏳ **Memulai Broadcast...**\nAkun aktif: `{len(users)}`")
    status_chat_id = status_msg["chat"]["id"]
    status_msg_id = status_msg["message_id"]

    for user in users:
        sent_by_account = 0
        try:
            async with userbot_client(user) as ubot:
                async for dialog in ubot.get_dialogs():
                    if dialog.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
                        total_groups += 1
                        if await safe_send_to_group(ubot, dialog.chat.id, text_bc):
                            total_sent += 1
                            sent_by_account += 1
                        await asyncio.sleep(int(c["jeda_grup"]))
        except Exception as e:
            failed_accounts += 1
            print(f"broadcast account failed {user.get('id')}: {e}", flush=True)

        await api.edit_message_text(
            status_chat_id,
            status_msg_id,
            "⏳ **Broadcast berjalan...**\n"
            f"Terkirim: `{total_sent}`\n"
            f"Grup dicek: `{total_groups}`\n"
            f"Akun gagal: `{failed_accounts}`\n"
            f"Akun terakhir: `{user.get('label') or user.get('phone') or user['id']}` -> `{sent_by_account}` grup",
        )
        await asyncio.sleep(int(c["jeda_loop"]))

    durasi = f"{int(time.time() - start_t)}s"
    report = (
        "✅ **Broadcast Selesai!**\n"
        f"Total terkirim: `{total_sent}` grup\n"
        f"Total grup dicek: `{total_groups}`\n"
        f"Akun gagal: `{failed_accounts}`\n"
        f"Durasi: `{durasi}`"
    )
    await api.edit_message_text(status_chat_id, status_msg_id, report)
    await api.send_message(int(c["report_pm_id"]), f"🔔 **NOTIF REPORT**\n\n{report}")


async def handle_message(api: BotAPI, message: dict):
    chat_id = message["chat"]["id"]
    from_user = message.get("from") or {}
    user_id = from_user.get("id")
    text = message.get("text") or ""
    print(f"message from {user_id}: {text!r}", flush=True)

    if text.startswith("/start"):
        await send_main_menu(api, chat_id, user_id)
        return

    if user_id != OWNER_ID:
        return

    if user_id not in state_db:
        return

    step = state_db[user_id]["step"]
    if step == "bc_msg":
        asyncio.create_task(run_broadcast(api, chat_id, text))
        del state_db[user_id]
    elif step == "input_report_user":
        username = text.replace("@", "").strip()
        try:
            target = await api.get_chat(f"@{username}")
            await update_conf(report_dest_type="pm", report_pm_id=target["id"], report_pm_username=username)
            await api.send_message(chat_id, f"✅ Laporan akan dikirim ke @{username}")
            del state_db[user_id]
        except Exception as e:
            print(f"get_chat failed: {e}", flush=True)
            await api.send_message(chat_id, "❌ Username tidak ditemukan atau bot belum bisa mengakses user itu.")
    elif step == "input_delay":
        try:
            group_delay, loop_delay = [int(x) for x in text.split()[:2]]
            if group_delay < 0 or loop_delay < 0:
                raise ValueError
            await update_conf(jeda_grup=group_delay, jeda_loop=loop_delay)
            await api.send_message(chat_id, f"✅ Jeda disimpan: grup `{group_delay}s`, loop `{loop_delay}s`")
            del state_db[user_id]
        except Exception:
            await api.send_message(chat_id, "❌ Format salah. Contoh benar: `2 60`")


async def handle_callback(api: BotAPI, cq: dict):
    cq_id = cq["id"]
    from_user = cq.get("from") or {}
    user_id = from_user.get("id")
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    print(f"callback from {user_id}: {data}", flush=True)

    if user_id != OWNER_ID and data in {"admin_stats", "report_menu", "set_dest_bot", "ask_dest_pm", "ask_delay", "start_bc"}:
        await api.answer_callback(cq_id, "Khusus owner", show_alert=True)
        return

    if data == "admin_stats":
        await api.answer_callback(cq_id)
        await show_admin_stats(api, chat_id, message_id)
    elif data == "report_menu":
        await api.answer_callback(cq_id)
        await show_report_menu(api, chat_id, message_id)
    elif data == "set_dest_bot":
        await update_conf(report_dest_type="bot", report_pm_id=OWNER_ID, report_pm_username="Owner")
        await api.answer_callback(cq_id, "Tujuan laporan diset ke owner bot")
        await show_report_menu(api, chat_id, message_id)
    elif data == "ask_dest_pm":
        state_db[user_id] = {"step": "input_report_user"}
        await api.answer_callback(cq_id)
        await api.edit_message_text(chat_id, message_id, "💬 Kirim username tujuan laporan. Contoh: `@username`")
    elif data == "ask_delay":
        state_db[user_id] = {"step": "input_delay"}
        await api.answer_callback(cq_id)
        await api.edit_message_text(chat_id, message_id, "⚙️ Kirim jeda dengan format: `jeda_grup jeda_loop`\nContoh: `2 60`")
    elif data == "start_bc":
        state_db[user_id] = {"step": "bc_msg"}
        await api.answer_callback(cq_id)
        await api.edit_message_text(chat_id, message_id, "📢 **Kirim pesan promosi:**")
    elif data == "back":
        await api.answer_callback(cq_id)
        await api.edit_message_text(chat_id, message_id, main_menu_text(), reply_markup=main_menu_buttons(user_id))
    elif data == "ask_num":
        await api.answer_callback(cq_id, "Fitur install userbot belum diaktifkan di versi SQLite ini.", show_alert=True)
    else:
        await api.answer_callback(cq_id, "Fitur ini belum tersedia.", show_alert=True)


async def drop_pending_updates(api: BotAPI) -> int | None:
    updates = await api.get_updates(timeout=0, limit=100)
    if not updates:
        return None
    last_id = updates[-1]["update_id"]
    await api.get_updates(offset=last_id + 1, timeout=0, limit=1)
    return last_id + 1


async def poll_loop(api: BotAPI):
    offset = await drop_pending_updates(api) if DROP_PENDING_UPDATES else None
    if offset:
        print(f">>> Dropped pending updates, starting offset {offset} <<<", flush=True)

    while True:
        try:
            updates = await api.get_updates(offset=offset, timeout=50, limit=50)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    await handle_message(api, update["message"])
                elif "callback_query" in update:
                    await handle_callback(api, update["callback_query"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"poll error: {e}", flush=True)
            await asyncio.sleep(3)


async def main():
    await init_db()
    print(f">>> {BOT_NAME} SQLITE READY <<<", flush=True)
    async with BotAPI(BOT_TOKEN) as api:
        me = await api.get_me()
        print(f">>> Bot API polling as @{me.get('username') or me.get('id')} <<<", flush=True)
        await poll_loop(api)


if __name__ == "__main__":
    asyncio.run(main())
