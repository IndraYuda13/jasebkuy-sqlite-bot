import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from dotenv import load_dotenv
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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

if not API_HASH:
    raise RuntimeError("Environment variable API_HASH wajib diisi")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN wajib diisi")

SESSION_DIR.mkdir(parents=True, exist_ok=True)

bot = Client("jasebkuy_engine", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
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
    async def ensure():
        await init_db()

    await ensure()

    def work():
        with _connect() as conn:
            row = conn.execute("SELECT * FROM settings WHERE id = 'global'").fetchone()
            return dict(row)

    return await db_exec(work)


async def update_conf(**fields) -> None:
    if not fields:
        return
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


def main_menu_buttons(user_id: int) -> InlineKeyboardMarkup:
    btns = [
        [
            InlineKeyboardButton("🛍️ Order Produk", callback_data="noop"),
            InlineKeyboardButton("💳 Isi Saldo", callback_data="noop"),
        ],
        [InlineKeyboardButton("🚀 Install Userbot", callback_data="ask_num")],
    ]
    if user_id == OWNER_ID:
        btns.append([InlineKeyboardButton("📊 Dashboard Admin", callback_data="admin_stats")])
    return InlineKeyboardMarkup(btns)


async def send_main_menu(message, user_id: int):
    ram_vps = get_ram_info()
    text = (
        "🔥 **UBOT MANAGER V1** 🔥\n\n"
        "Status VPS: ONLINE ✅\n"
        f"RAM: {ram_vps} 🚀\n\n"
        f"✨ **Selamat Datang di {BOT_NAME}** ✨\n"
        "Silakan pilih menu di bawah untuk bertransaksi."
    )
    await message.reply(text, reply_markup=main_menu_buttons(user_id))


@bot.on_message(filters.command("start"))
async def start(_, message):
    await send_main_menu(message, message.from_user.id)


@bot.on_callback_query(filters.regex("^admin_stats$"))
async def admin_stats(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return

    c = await get_conf()
    total_acc = await count_users()
    text = (
        "📊 **PANEL ADMIN TOKO**\n"
        f"Total Akun: `{total_acc}`\n"
        f"Jeda Grup: `{c['jeda_grup']}s` | Putaran: `{c['jeda_loop']}s`\n"
        f"Tujuan Laporan: `{c['report_pm_username']}`"
    )
    btns = [
        [InlineKeyboardButton("📢 Broadcast Massal", callback_data="start_bc")],
        [InlineKeyboardButton("📊 Laporan Broadcast", callback_data="report_menu")],
        [InlineKeyboardButton("⚙️ Set Jeda", callback_data="ask_delay")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="back")],
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))


@bot.on_callback_query(filters.regex("^report_menu$"))
async def report_menu(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return

    c = await get_conf()
    status = f"PM -> @{c['report_pm_username']}" if c["report_dest_type"] == "pm" else "Ke Bot Owner (Default)"
    text = (
        "📊 **Pengaturan Laporan Broadcast**\n\n"
        "Tentukan kemana laporan hasil broadcast akan dikirim.\n\n"
        "📍 **Status Saat Ini:**\n"
        f"💬 {status}"
    )
    btns = [
        [InlineKeyboardButton("🤖 Ke Bot Owner", callback_data="set_dest_bot")],
        [InlineKeyboardButton("💬 Ke PM Username", callback_data="ask_dest_pm")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="admin_stats")],
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))


@bot.on_callback_query(filters.regex("^set_dest_bot$"))
async def set_dest_bot(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return
    await update_conf(report_dest_type="bot", report_pm_id=OWNER_ID, report_pm_username="Owner")
    await callback_query.answer("Tujuan laporan diset ke owner bot")
    await report_menu(_, callback_query)


@bot.on_callback_query(filters.regex("^ask_dest_pm$"))
async def ask_dest_pm(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return
    state_db[callback_query.from_user.id] = {"step": "input_report_user"}
    await callback_query.message.edit_text("💬 Kirim username tujuan laporan. Contoh: `@username`")


@bot.on_callback_query(filters.regex("^ask_delay$"))
async def ask_delay(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return
    state_db[callback_query.from_user.id] = {"step": "input_delay"}
    await callback_query.message.edit_text("⚙️ Kirim jeda dengan format: `jeda_grup jeda_loop`\nContoh: `2 60`")


@bot.on_callback_query(filters.regex("^start_bc$"))
async def bc_btn(_, callback_query):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer("Khusus owner", show_alert=True)
        return
    state_db[callback_query.from_user.id] = {"step": "bc_msg"}
    await callback_query.message.edit_text("📢 **Kirim pesan promosi:**")


@bot.on_callback_query(filters.regex("^back$"))
async def back_btn(_, callback_query):
    text = (
        "🔥 **UBOT MANAGER V1** 🔥\n\n"
        "Status VPS: ONLINE ✅\n"
        f"RAM: {get_ram_info()} 🚀\n\n"
        f"✨ **Selamat Datang di {BOT_NAME}** ✨\n"
        "Silakan pilih menu di bawah untuk bertransaksi."
    )
    await callback_query.message.edit_text(text, reply_markup=main_menu_buttons(callback_query.from_user.id))


@bot.on_callback_query(filters.regex("^ask_num$"))
async def ask_num(_, callback_query):
    await callback_query.answer("Fitur install userbot belum diaktifkan di versi SQLite ini.", show_alert=True)


@bot.on_callback_query(filters.regex("^noop$"))
async def noop(_, callback_query):
    await callback_query.answer("Fitur ini belum tersedia.", show_alert=True)


@asynccontextmanager
async def userbot_client(user: dict):
    session_name = str(SESSION_DIR / f"ubot_{user['id']}")
    ubot = Client(session_name, session_string=user["session"], api_id=API_ID, api_hash=API_HASH)
    try:
        await ubot.start()
        yield ubot
    finally:
        try:
            await ubot.stop()
        except Exception:
            pass


async def safe_send_to_group(ubot: Client, chat_id: int, text: str) -> bool:
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


async def run_broadcast(client: Client, owner_msg, text_bc: str):
    c = await get_conf()
    start_t = time.time()
    users = await get_active_users()
    total_sent = 0
    total_groups = 0
    failed_accounts = 0

    status_prog = await owner_msg.reply(f"⏳ **Memulai Broadcast...**\nAkun aktif: `{len(users)}`")

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
        except Exception:
            failed_accounts += 1

        await status_prog.edit_text(
            "⏳ **Broadcast berjalan...**\n"
            f"Terkirim: `{total_sent}`\n"
            f"Grup dicek: `{total_groups}`\n"
            f"Akun gagal: `{failed_accounts}`\n"
            f"Akun terakhir: `{user.get('label') or user.get('phone') or user['id']}` -> `{sent_by_account}` grup"
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
    await status_prog.edit_text(report)
    await client.send_message(int(c["report_pm_id"]), f"🔔 **NOTIF REPORT**\n\n{report}")


@bot.on_message(filters.text & filters.user(OWNER_ID))
async def handle_inputs(client, message):
    uid = message.from_user.id
    if uid not in state_db:
        return

    step = state_db[uid]["step"]
    if step == "bc_msg":
        asyncio.create_task(run_broadcast(client, message, message.text))
        del state_db[uid]
    elif step == "input_report_user":
        username = message.text.replace("@", "").strip()
        try:
            target = await client.get_users(username)
            await update_conf(report_dest_type="pm", report_pm_id=target.id, report_pm_username=username)
            await message.reply(f"✅ Laporan akan dikirim ke @{username}")
            del state_db[uid]
        except Exception:
            await message.reply("❌ Username tidak ditemukan atau bot belum bisa mengakses user itu.")
    elif step == "input_delay":
        try:
            group_delay, loop_delay = [int(x) for x in message.text.split()[:2]]
            if group_delay < 0 or loop_delay < 0:
                raise ValueError
            await update_conf(jeda_grup=group_delay, jeda_loop=loop_delay)
            await message.reply(f"✅ Jeda disimpan: grup `{group_delay}s`, loop `{loop_delay}s`")
            del state_db[uid]
        except Exception:
            await message.reply("❌ Format salah. Contoh benar: `2 60`")


async def main():
    await init_db()
    print(f">>> {BOT_NAME} SQLITE READY <<<")
    await bot.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
