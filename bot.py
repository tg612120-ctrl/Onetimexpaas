import os
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import SessionPasswordNeeded

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
OWNER_ID = int(os.getenv("OWNER_ID"))

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)


def allowed(user_id: int) -> bool:
    return user_id == OWNER_ID


bot = Client(
    "session_generator_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


@bot.on_message(filters.command("start") & filters.private)
async def start(_, message):
    if not allowed(message.from_user.id):
        await message.reply_text("❌ Unauthorized.")
        return

    await message.reply_text(
        "🔐 Telegram Session Generator\n\n"
        "Use /generate to start.\n\n"
        "⚠️ Only generate sessions for accounts you own."
    )


@bot.on_message(filters.command("generate") & filters.private)
async def generate(_, message):
    if not allowed(message.from_user.id):
        await message.reply_text("❌ Unauthorized.")
        return

    await message.reply_text(
        "Send the phone number of your own Telegram account.\n"
        "Example: +919876543210"
    )

    try:
        phone_msg = await bot.listen(message.chat.id, timeout=120)
    except Exception:
        return

    phone = phone_msg.text.strip()

    if not phone.startswith("+"):
        await message.reply_text("❌ Include the country code.")
        return

    client_name = "user_session"

    user_client = Client(
        client_name,
        api_id=API_ID,
        api_hash=API_HASH,
        workdir=str(SESSION_DIR),
        in_memory=False,
    )

    try:
        await user_client.connect()

        sent = await user_client.send_code(phone)

        await message.reply_text("📩 Enter the Telegram login code:")

        code_msg = await bot.listen(message.chat.id, timeout=180)
        code = code_msg.text.strip().replace(" ", "")

        try:
            await user_client.sign_in(
                phone_number=phone,
                phone_code_hash=sent.phone_code_hash,
                phone_code=code,
            )

        except SessionPasswordNeeded:
            await message.reply_text("🔑 Enter your Telegram 2FA password:")

            password_msg = await bot.listen(
                message.chat.id,
                timeout=180
            )

            await user_client.check_password(
                password=password_msg.text
            )

        session_path = SESSION_DIR / f"{client_name}.session"

        if not session_path.exists():
            await message.reply_text(
                "❌ Session file was not created."
            )
            return

        zip_path = SESSION_DIR / f"{client_name}.zip"

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as z:
            z.write(
                session_path,
                arcname=session_path.name,
            )

        await message.reply_text(
            "✅ Session generated successfully.\n\n"
            f"📦 ZIP created:\n{zip_path.name}\n\n"
            "The ZIP is kept on the server and is not sent back "
            "through Telegram."
        )

    except Exception as e:
        await message.reply_text(
            f"❌ Error:\n{type(e).__name__}: {e}"
        )

    finally:
        try:
            await user_client.disconnect()
        except Exception:
            pass


print("Bot started...")
bot.run()
