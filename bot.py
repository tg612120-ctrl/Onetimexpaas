import os
import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from pymongo import MongoClient
from bson import ObjectId

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    TelegramClient = None
    StringSession = None


BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
DATABASE_NAME = os.getenv("DATABASE_NAME", "selling_bot")
OWNER_ID = int(os.environ["OWNER_ID"])

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

MIN_DEPOSIT = 10
UPI_ID = os.getenv("UPI_ID", "your-upi-id@upi")
DEFAULT_WATCHER_ID = int(os.getenv("DEFAULT_WATCHER_ID", "0"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo[DATABASE_NAME]

users = db.users
deposits = db.deposits
orders = db.orders
products = db.products
product_sessions = db.product_sessions

SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY")


def now():
    return datetime.now(timezone.utc)


def get_fernet_instance():
    if Fernet is None:
        raise RuntimeError("cryptography package is required.")
    
    key = SESSION_ENCRYPTION_KEY
    if not key or len(key.strip()) != 44:
        key = "bXlzdXBlcnNlY3JldGtleTFmZXJuZXQzMmJ5dGVzMTI9"
        
    return Fernet(key.strip().encode())

def encrypt_session_value(session_value: str) -> str:
    f = get_fernet_instance()
    return f.encrypt(session_value.encode()).decode()

def decrypt_session_value(encrypted_value: str) -> str:
    f = get_fernet_instance()
    return f.decrypt(encrypted_value.encode()).decode()


def store_product_session_reference(product_id, watcher_user_id: int, session_value: str):
    encrypted = encrypt_session_value(session_value)
    product_sessions.update_one(
        {"product_id": str(product_id)},
        {
            "$set": {
                "product_id": str(product_id),
                "watcher_user_id": int(watcher_user_id),
                "session_encrypted": encrypted,
                "updated_at": now(),
            },
            "$setOnInsert": {"created_at": now()},
        },
        upsert=True,
    )


def get_product_session_reference(product_id):
    return product_sessions.find_one({"product_id": str(product_id)})


async def fetch_latest_watcher_message(product_id: str) -> str:
    """Read the latest text message from the stored watcher session."""
    if not TelegramClient or not StringSession:
        return "❌ Telethon package is required to read Watcher DMs."

    if not API_ID or not API_HASH:
        return "❌ API_ID and API_HASH environment variables are not set."

    session_doc = get_product_session_reference(product_id)
    if not session_doc or "session_encrypted" not in session_doc:
        return "❌ Watcher session not found for this product."

    try:
        raw_session = decrypt_session_value(session_doc["session_encrypted"])
    except Exception as exc:
        logger.error("Failed to decrypt session: %s", exc)
        return "❌ Failed to decrypt session credential."

    client = TelegramClient(StringSession(raw_session), API_ID, API_HASH)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return "❌ Watcher session is expired or invalid."

        messages = await client.get_messages("me", limit=5)

        latest_msg = None
        for message in messages:
            if message.text:
                latest_msg = message.text
                break

        await client.disconnect()

        if latest_msg:
            return latest_msg

        return "❌ No recent messages found on Watcher account."

    except Exception as exc:
        logger.exception("Error fetching message from Watcher Account")
        if client.is_connected():
            await client.disconnect()
        return f"❌ Error reading Watcher DM: {exc}"


async def delete_message_later(bot, chat_id, message_id, delay=120):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


AMOUNT, UPI_PROOF, REDEEM_PROOF, REDEEM_SCREENSHOT, PRODUCT_NAME, PRODUCT_DETAILS, PRODUCT_PRICE, PRODUCT_SESSION = range(8)


def user_doc(tg_user):
    return {
        "user_id": tg_user.id,
        "name": tg_user.full_name,
        "username": tg_user.username,
        "balance": 0,
        "created_at": now(),
        "updated_at": now(),
    }


def ensure_user(tg_user):
    users.update_one(
        {"user_id": tg_user.id},
        {
            "$set": {
                "name": tg_user.full_name,
                "username": tg_user.username,
                "updated_at": now(),
            },
            "$setOnInsert": {
                "balance": 0,
                "created_at": now(),
            },
        },
        upsert=True,
    )


def main_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton("🛒 Buy Now", callback_data="buy"),
            InlineKeyboardButton("💳 Add Funds", callback_data="add_funds"),
        ],
        [InlineKeyboardButton("📦 My Orders", callback_data="orders")],
        [
            InlineKeyboardButton("🆘 Support", callback_data="support"),
            InlineKeyboardButton("👤 My Profile", callback_data="profile"),
        ],
    ]

    if user_id == OWNER_ID:
        buttons.append(
            [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")]
        )

    return InlineKeyboardMarkup(buttons)


def add_funds_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🇮🇳 UPI (PhonePe/GPay/Paytm)",
                callback_data="pay_upi"
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 Google Play Redeem Code",
                callback_data="pay_redeem"
            )
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ])


def back_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ])


def admin_panel_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance"),
        ],
        [
            InlineKeyboardButton("💳 Deposit Requests", callback_data="admin_deposits"),
            InlineKeyboardButton("📦 Orders", callback_data="admin_orders"),
        ],
        [
            InlineKeyboardButton("🛍️ Products / Accounts", callback_data="admin_products"),
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ])


def admin_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_panel")]
    ])


def admin_products_menu():
    buttons = []

    for product in products.find({"active": {"$ne": False}}).sort("created_at", 1):
        buttons.append([
            InlineKeyboardButton(
                f"🛍️ {product.get('name', 'Unnamed Product')}",
                callback_data=f"admin_product:{product['_id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "➕ Add New Sub-Product",
            callback_data="admin_add_product"
        )
    ])
    buttons.append([
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_panel")
    ])

    return InlineKeyboardMarkup(buttons)


def user_products_menu():
    buttons = []

    for product in products.find({"active": {"$ne": False}}).sort("created_at", 1):
        buttons.append([
            InlineKeyboardButton(
                f"🛍️ {product.get('name', 'Unnamed Product')} — ₹{product.get('price', 0)}",
                callback_data=f"buy_product:{product['_id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Back", callback_data="home")
    ])

    return InlineKeyboardMarkup(buttons)


FORCE_JOIN_CHATS = [
    ("🇮🇳 Support Channel", "@zyXzo", "https://t.me/zyXzo"),
    ("📋 Logs Channel", "@arcfluxx", "https://t.me/arcfluxx"),
    ("👥 Support Group", "@genzportals", "https://t.me/genzportals"),
]


def force_join_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(name, url=url)]
        for name, _, url in FORCE_JOIN_CHATS
    ] + [
        [InlineKeyboardButton(
            "✅ I HAVE JOINED ALL",
            callback_data="verify_join"
        )]
    ])


async def check_force_join(bot, user_id):
    for _, chat_id, _ in FORCE_JOIN_CHATS:
        try:
            member = await bot.get_chat_member(chat_id, user_id)

            if member.status in ("left", "kicked"):
                return False

            if (
                member.status == "restricted"
                and getattr(member, "is_member", False) is False
            ):
                return False

        except Exception as exc:
            logger.warning(
                "Force-join check failed for %s: %s",
                chat_id,
                exc
            )
            return False

    return True


async def send_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 JOIN TO CONTINUE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Bot use karne ke liye neeche diye saare channels aur group join karein.\n\n"
        "Join karne ke baad Verify button dabayein."
    )

    return await update.message.reply_text(
        text,
        reply_markup=force_join_menu()
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    if await check_force_join(context.bot, update.effective_user.id):
        await update.message.reply_text(
            f"👋 Welcome, {update.effective_user.first_name}!\n\n"
            "🛒 Products dekhne aur wallet manage karne ke liye menu explore karein.",
            reply_markup=main_menu(update.effective_user.id),
        )
        return

    await send_force_join(update, context)


async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    if not await check_force_join(context.bot, q.from_user.id):
        await q.answer(
            "❌ Aapne abhi tak saare required channels/groups join nahi kiye hain.",
            show_alert=True,
        )
        return

    try:
        await q.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=q.from_user.id,
        text=(
            f"👋 Welcome, {q.from_user.first_name}!\n\n"
            "🛒 Products dekhne aur wallet manage karne ke liye menu explore karein."
        ),
        reply_markup=main_menu(q.from_user.id),
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ensure_user(q.from_user)

    if q.data == "verify_join":
        await verify_join_callback(update, context)
        return

    if q.data == "admin_panel":
        if q.from_user.id != OWNER_ID:
            await q.answer("Not authorized.", show_alert=True)
            return

        await q.edit_message_text(
            "🔐 ADMIN PANEL\n━━━━━━━━━━━━━━━━━━\n\n"
            "Option choose karein:",
            reply_markup=admin_panel_menu()
        )
        return

    if q.data.startswith("admin_"):
        if q.from_user.id != OWNER_ID:
            await q.answer("Not authorized.", show_alert=True)
            return

        action = q.data

        if action == "admin_users":
            total = users.count_documents({})
            await q.edit_message_text(
                f"👥 USERS\n━━━━━━━━━━━━━━━━━━\n\nTotal users: {total}",
                reply_markup=admin_back()
            )

        elif action == "admin_balance":
            await q.edit_message_text(
                "💰 BALANCE MANAGEMENT\n━━━━━━━━━━━━━━━━━━\n\n"
                "DM Commands:\nDd 70 USER_ID\nSs 70 USER_ID\n\n"
                "Group me reply karke:\nDd 70\nSs 70",
                reply_markup=admin_back()
            )

        elif action == "admin_deposits":
            pending = deposits.count_documents({"status": "pending"})
            await q.edit_message_text(
                f"💳 DEPOSIT REQUESTS\n━━━━━━━━━━━━━━━━━━\n\nPending: {pending}",
                reply_markup=admin_back()
            )

        elif action == "admin_orders":
            total = orders.count_documents({})
            await q.edit_message_text(
                f"📦 ORDERS\n━━━━━━━━━━━━━━━━━━\n\nTotal: {total}",
                reply_markup=admin_back()
            )

        elif action == "admin_products":
            total = products.count_documents({"active": {"$ne": False}})
            await q.edit_message_text(
                f"🛍️ PRODUCTS / ACCOUNTS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Total products: {total}",
                reply_markup=admin_products_menu()
            )
            return

        elif action == "admin_add_product":
            await q.edit_message_text(
                "➕ ADD NEW SUB-PRODUCT\n━━━━━━━━━━━━━━━━━━\n\n"
                "Sub-product ka naam bhejein.",
                reply_markup=admin_back()
            )
            return PRODUCT_NAME

        elif action.startswith("admin_product:"):
            raw_id = action.split(":", 1)[1]

            try:
                product = products.find_one({
                    "_id": ObjectId(raw_id),
                    "active": {"$ne": False}
                })
            except Exception:
                product = None

            if not product:
                await q.answer("Product not found.", show_alert=True)
                return

            product_id = str(product["_id"])
            meta = get_product_session_reference(product_id) or {}

            await q.edit_message_text(
                f"🛍️ {product.get('name', 'Unnamed Product')}\n\n"
                f"Details: {product.get('details', '—')}\n"
                f"Price: ₹{product.get('price', 0)}\n"
                f"Watcher ID: "
                f"{meta.get('watcher_user_id', DEFAULT_WATCHER_ID) or 'Not set'}\n",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🗑️ Delete Sub-Product",
                            callback_data=f"admin_delete_product:{product_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Products / Accounts",
                            callback_data="admin_products"
                        )
                    ],
                ])
            )
            return

        elif action.startswith("admin_delete_product:"):
            raw_id = action.split(":", 1)[1]

            await q.edit_message_text(
                "⚠️ Are you sure you want to delete this product?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ YES, DELETE",
                            callback_data=f"admin_delete_confirm:{raw_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"admin_product:{raw_id}"
                        )
                    ],
                ])
            )
            return

        elif action.startswith("admin_delete_confirm:"):
            raw_id = action.split(":", 1)[1]

            try:
                oid = ObjectId(raw_id)
            except Exception:
                await q.answer("Invalid product.", show_alert=True)
                return

            products.update_one(
                {"_id": oid},
                {"$set": {"active": False, "deleted_at": now()}}
            )

            await q.edit_message_text(
                "✅ Product deleted successfully.",
                reply_markup=admin_products_menu()
            )
            return

        elif action == "admin_stats":
            total_users = users.count_documents({})
            await q.edit_message_text(
                f"📊 STATISTICS\n\nTotal Users: {total_users}",
                reply_markup=admin_back()
            )

        elif action == "admin_broadcast":
            await q.edit_message_text(
                "📢 Send broadcast as: BROADCAST: message",
                reply_markup=admin_back()
            )

        elif action == "admin_settings":
            await q.edit_message_text(
                f"⚙️ SETTINGS\n\nUPI: {UPI_ID}",
                reply_markup=admin_back()
            )

        return

    if q.data == "home":
        await q.edit_message_text(
            f"👋 Welcome, {q.from_user.first_name}!",
            reply_markup=main_menu(q.from_user.id)
        )

    elif q.data == "add_funds":
        user = users.find_one({"user_id": q.from_user.id}) or {}
        balance = user.get("balance", 0)

        await q.edit_message_text(
            f"🏦 ADD FUNDS\n\n💰 Balance: ₹{balance}\n\n"
            "Payment Method chunein:",
            reply_markup=add_funds_menu()
        )

    elif q.data in ("pay_upi", "pay_redeem"):
        method = (
            "UPI"
            if q.data == "pay_upi"
            else "Google Play Redeem Code"
        )

        context.user_data["deposit_method"] = method

        await q.edit_message_text(
            "💰 Amount enter karein (Min ₹10):",
            reply_markup=back_home()
        )
        return AMOUNT

    elif q.data == "buy":
        total = products.count_documents({"active": {"$ne": False}})

        if total == 0:
            await q.edit_message_text(
                "🛒 Abhi koi product available nahi hai.",
                reply_markup=back_home()
            )
        else:
            await q.edit_message_text(
                "🛒 Product select karein:",
                reply_markup=user_products_menu()
            )

    elif q.data.startswith("buy_product:"):
        raw_id = q.data.split(":", 1)[1]

        try:
            product = products.find_one({
                "_id": ObjectId(raw_id),
                "active": {"$ne": False}
            })
        except Exception:
            product = None

        if not product:
            await q.answer("Product not found.", show_alert=True)
            return

        price = int(product.get("price", 0) or 0)

        await q.edit_message_text(
            f"🛍️ {product.get('name')}\n\n"
            f"💰 Price: ₹{price}\n\n"
            "Purchase confirm karein:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Confirm Purchase",
                        callback_data=f"confirm_buy:{raw_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="buy"
                    )
                ],
            ])
        )

    elif q.data.startswith("confirm_buy:"):
        raw_id = q.data.split(":", 1)[1]

        try:
            oid = ObjectId(raw_id)
        except Exception:
            await q.answer("Invalid product.", show_alert=True)
            return

        product = products.find_one({
            "_id": oid,
            "active": {"$ne": False}
        })

        if not product:
            await q.answer(
                "Product sell ho gaya ya available nahi hai.",
                show_alert=True
            )
            return

        price = int(product.get("price", 0) or 0)
        user = users.find_one({"user_id": q.from_user.id}) or {}
        balance = int(user.get("balance", 0) or 0)

        if balance < price:
            await q.answer("❌ Balance kam hai.", show_alert=True)
            return

        updated = users.update_one(
            {
                "user_id": q.from_user.id,
                "balance": {"$gte": price}
            },
            {
                "$inc": {"balance": -price},
                "$set": {"updated_at": now()}
            }
        )

        if updated.modified_count != 1:
            await q.answer("❌ Transaction failed.", show_alert=True)
            return

        sold = products.update_one(
            {
                "_id": oid,
                "active": {"$ne": False}
            },
            {
                "$set": {
                    "active": False,
                    "sold_to": q.from_user.id,
                    "sold_at": now()
                }
            }
        )

        if sold.modified_count != 1:
            users.update_one(
                {"user_id": q.from_user.id},
                {"$inc": {"balance": price}}
            )
            await q.answer(
                "❌ Product pehle hi bik chuka hai. Balance refund kar diya.",
                show_alert=True
            )
            return

        order = orders.insert_one({
            "user_id": q.from_user.id,
            "product_id": str(oid),
            "product_name": product.get("name"),
            "amount": price,
            "status": "completed",
            "created_at": now()
        })

        await q.edit_message_text(
            f"✅ Purchase successful!\n\n"
            f"🛍️ {product.get('name')}\n"
            f"💰 Paid: ₹{price}\n"
            "Neeche **Get** button dabayein.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📥 Get Message/OTP",
                        callback_data=f"get_product:{order.inserted_id}"
                    )
                ]
            ]),
            parse_mode="Markdown"
        )

    elif q.data.startswith("get_product:"):
        raw_order_id = q.data.split(":", 1)[1]

        try:
            order = orders.find_one({
                "_id": ObjectId(raw_order_id),
                "user_id": q.from_user.id
            })
        except Exception:
            order = None

        if not order:
            await q.answer("Order nahi mila.", show_alert=True)
            return

        msg = await q.message.reply_text(
            "⏳ Watcher account ke messages fetch kar rahe hain..."
        )

        fetched_text = await fetch_latest_watcher_message(
            order["product_id"]
        )

        delivery_text = (
            "📥 **WATCHER ACCOUNT MESSAGE**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🛍️ **Product:** {order.get('product_name')}\n\n"
            "💬 **Received Message:**\n"
            f"`{fetched_text}`\n\n"
            "⚠️ *Yeh message 2 minutes (120 sec) me auto-delete ho jayega.*"
        )

        try:
            await msg.edit_text(
                delivery_text,
                parse_mode="Markdown"
            )
        except Exception:
            await msg.edit_text(delivery_text)

        asyncio.create_task(
            delete_message_later(
                context.bot,
                q.from_user.id,
                msg.message_id,
                120
            )
        )

        await q.answer(
            "Message deliver kar diya gaya!",
            show_alert=True
        )

    elif q.data == "orders":
        user_orders = list(
            orders.find(
                {"user_id": q.from_user.id}
            ).sort("created_at", -1)
        )

        if not user_orders:
            await q.edit_message_text(
                "📦 MY ORDERS\n━━━━━━━━━━━━━━━━━━\n\n"
                "Koi orders nahi hain.",
                reply_markup=back_home()
            )
        else:
            text = "📦 MY ORDERS\n━━━━━━━━━━━━━━━━━━\n\n"
            buttons = []

            for ord_item in user_orders:
                text += (
                    f"🛍️ {ord_item.get('product_name')} — "
                    f"₹{ord_item.get('amount')}\n"
                )

                buttons.append([
                    InlineKeyboardButton(
                        f"📥 Get: {ord_item.get('product_name')}",
                        callback_data=f"get_product:{ord_item['_id']}"
                    )
                ])

            buttons.append([
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="home"
                )
            ])

            await q.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

    elif q.data == "support":
        await q.edit_message_text(
            "🆘 Support ke liye admin ko contact karein.",
            reply_markup=back_home()
        )

    elif q.data == "profile":
        user = users.find_one({"user_id": q.from_user.id}) or {}

        await q.edit_message_text(
            f"👤 MY PROFILE\n\n"
            f"🆔 ID: {q.from_user.id}\n"
            f"💰 Balance: ₹{user.get('balance', 0)}",
            reply_markup=back_home()
        )

    return ConversationHandler.END


async def product_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    name = (update.message.text or "").strip()

    if not name:
        await update.message.reply_text("❌ Sahi name bhejein.")
        return PRODUCT_NAME

    context.user_data["product_name"] = name

    await update.message.reply_text(
        "📝 Product ki details bhejein."
    )

    return PRODUCT_DETAILS


async def product_details_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    details = (update.message.text or "").strip()
    context.user_data["product_details"] = details

    await update.message.reply_text(
        "💰 Price enter karein ₹ me. Example: 16"
    )

    return PRODUCT_PRICE


async def product_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    try:
        price = int(
            (update.message.text or "")
            .strip()
            .replace("₹", "")
        )

        if price < 0:
            raise ValueError

    except Exception:
        await update.message.reply_text("❌ Invalid price.")
        return PRODUCT_PRICE

    context.user_data["product_price"] = price

    await update.message.reply_text(
        "🔐 Ab Watcher ID ka Telethon StringSession send karein."
    )

    return PRODUCT_SESSION


async def product_session_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    session_value = (update.message.text or "").strip()

    try:
        name = context.user_data["product_name"]
        details = context.user_data["product_details"]
        price = context.user_data["product_price"]
        watcher = DEFAULT_WATCHER_ID

        result = products.insert_one({
            "name": name,
            "details": details,
            "price": price,
            "active": True,
            "created_at": now()
        })

        store_product_session_reference(
            result.inserted_id,
            watcher,
            session_value
        )

        try:
            await update.message.delete()
        except Exception:
            pass

        await update.effective_chat.send_message(
            f"✅ Product add ho gaya: {name}"
        )

    except Exception as exc:
        logger.exception("Error while adding product")
        await update.message.reply_text(
            f"❌ Error: {exc}"
        )

    return ConversationHandler.END


async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())

        if amount < MIN_DEPOSIT:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            f"❌ Minimum deposit ₹{MIN_DEPOSIT} hai."
        )
        return AMOUNT

    context.user_data["deposit_amount"] = amount
    method = context.user_data.get("deposit_method")

    if method == "UPI":
        await update.message.reply_text(
            f"💳 UPI PAYMENT - ₹{amount}\n\n"
            f"UPI ID: {UPI_ID}\n"
            "Payment ke baad screenshot bhejein.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ I HAVE PAID",
                        callback_data="upi_paid"
                    )
                ]
            ])
        )
    else:
        await update.message.reply_text(
            "🎁 Redeem code bhejein:"
        )

    return UPI_PROOF if method == "UPI" else REDEEM_PROOF


async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    await q.message.reply_text(
        "📸 Ab payment screenshot image bhejein."
    )

    context.user_data["waiting_proof"] = True

    return UPI_PROOF


async def redeem_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["redeem_code"] = (
        update.message.text or ""
    ).strip()

    await update.message.reply_text(
        "📸 Ab redeem code/purchase screenshot bhejein."
    )

    return REDEEM_SCREENSHOT


async def proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Screenshot photo ki tarah bhejein."
        )
        return UPI_PROOF

    file_id = update.message.photo[-1].file_id
    method = context.user_data.get("deposit_method")
    amount = context.user_data.get("deposit_amount")

    result = deposits.insert_one({
        "user_id": update.effective_user.id,
        "name": update.effective_user.full_name,
        "method": method,
        "amount": amount,
        "proof_file_id": file_id,
        "status": "pending",
        "created_at": now()
    })

    await update.message.reply_text(
        "✅ Payment request review ke liye bhej di gayi hai."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ APPROVE",
            callback_data=f"approve:{result.inserted_id}"
        ),
        InlineKeyboardButton(
            "❌ REJECT",
            callback_data=f"reject:{result.inserted_id}"
        )
    ]])

    await context.bot.send_photo(
        OWNER_ID,
        file_id,
        caption=f"Deposit Request: ₹{amount}",
        reply_markup=keyboard
    )

    return ConversationHandler.END


async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        await q.answer("Not authorized.", show_alert=True)
        return

    action, raw_id = q.data.split(":", 1)

    try:
        dep = deposits.find_one({"_id": ObjectId(raw_id)})
    except Exception:
        dep = None

    if not dep or dep["status"] != "pending":
        return

    if action == "approve":
        users.update_one(
            {"user_id": dep["user_id"]},
            {"$inc": {"balance": dep["amount"]}}
        )

        deposits.update_one(
            {"_id": ObjectId(raw_id)},
            {"$set": {"status": "approved"}}
        )

        await context.bot.send_message(
            dep["user_id"],
            f"✅ ₹{dep['amount']} wallet me add kar diye gaye hain."
        )

        await q.edit_message_caption(
            caption="✅ APPROVED"
        )

    else:
        deposits.update_one(
            {"_id": ObjectId(raw_id)},
            {"$set": {"status": "rejected"}}
        )

        await context.bot.send_message(
            dep["user_id"],
            "❌ Deposit reject ho gaya."
        )

        await q.edit_message_caption(
            caption="❌ REJECTED"
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                callbacks,
                pattern=r"^(pay_upi|pay_redeem|admin_add_product)$"
            )
        ],
        states={
            AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    amount_received
                )
            ],
            UPI_PROOF: [
                CallbackQueryHandler(
                    paid_callback,
                    pattern=r"^upi_paid$"
                ),
                MessageHandler(
                    filters.PHOTO,
                    proof_received
                )
            ],
            REDEEM_PROOF: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    redeem_code_received
                )
            ],
            REDEEM_SCREENSHOT: [
                MessageHandler(
                    filters.PHOTO,
                    proof_received
                )
            ],
            PRODUCT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    product_name_received
                )
            ],
            PRODUCT_DETAILS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    product_details_received
                )
            ],
            PRODUCT_PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    product_price_received
                )
            ],
            PRODUCT_SESSION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    product_session_received
                )
            ],
        },
        fallbacks=[
            CallbackQueryHandler(callbacks)
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv, group=0)
    app.add_handler(
        CallbackQueryHandler(
            admin_decision,
            pattern=r"^(approve|reject):"
        ),
        group=1
    )
    app.add_handler(
        CallbackQueryHandler(callbacks),
        group=2
    )

    logger.info("Bot successfully started")
    app.run_polling()


if __name__ == "__main__":
    main()
