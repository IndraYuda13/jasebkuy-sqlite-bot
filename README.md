# JasebKuy SQLite Bot

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Storage](https://img.shields.io/badge/storage-SQLite-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

Bot Telegram sederhana untuk panel toko/userbot manager dengan penyimpanan SQLite. Bot utama memakai Bot API polling, sedangkan Pyrogram tetap dipakai untuk userbot broadcast. Versi ini adalah refactor dari script awal yang memakai MongoDB.

## Fitur

- Menu `/start`
- Dashboard admin
- Pengaturan tujuan laporan broadcast
- Broadcast massal lewat session string userbot yang tersimpan di SQLite
- Bot-side memakai Bot API polling supaya `/start` dan callback stabil
- Pengaturan jeda broadcast dari panel admin
- Konfigurasi aman via `.env`, tidak hardcode token/API key

## Quickstart

### 1. Clone repo

```bash
git clone https://github.com/IndraYuda13/jasebkuy-sqlite-bot
cd jasebkuy-sqlite-bot
```

### 2. Buat virtualenv dan install dependency

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Buat file `.env`

```bash
cp .env.example .env
nano .env
```

Isi contoh:

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:your_bot_token
OWNER_ID=123456789
BOT_NAME=Toko JasebKuy
DB_PATH=jasebkuy.sqlite3
```

### 4. Jalankan

```bash
python3 main.py
```

### 5. Jalankan di screen

```bash
screen -S jasebkuy
source .venv/bin/activate
python3 main.py
```

Detach screen dengan `CTRL+A` lalu `D`.

Cek lagi:

```bash
screen -r jasebkuy
```

## Struktur Database

Database default: `jasebkuy.sqlite3`

Tabel utama:

- `settings`: konfigurasi global bot
- `users`: daftar session string userbot untuk broadcast

Minimal kolom `users` yang dipakai broadcast:

- `session`: Pyrogram session string
- `active`: `1` untuk aktif, `0` untuk nonaktif
- `label` atau `phone`: opsional untuk penanda akun

Contoh insert manual:

```bash
sqlite3 jasebkuy.sqlite3 "INSERT INTO users(label, phone, session, active) VALUES('akun1', '+628xxx', 'SESSION_STRING_DI_SINI', 1);"
```

## Catatan Keamanan

Jangan commit file berikut:

- `.env`
- `*.session`
- `*.db`
- session string userbot asli

Kalau token bot pernah terkirim ke pihak lain, rotate token di BotFather sebelum dipakai production.

## Operasional

Screen aktif yang dipakai saat smoke test VPS:

```bash
screen -ls
screen -r jasebkuy_sqlite
```

Log runtime lokal:

```bash
tail -f bot.log
```

## Migrasi dari MongoDB

Script lama membaca koleksi MongoDB `users` dan `settings`. Versi ini memakai SQLite. Data akun userbot perlu dimasukkan ulang ke tabel `users` dengan kolom `session` dan `active=1`.
