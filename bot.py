import os
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from pymongo import MongoClient

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
DATABASE_NAME = os.getenv("DATABASE_NAME", "selling_bot")
OWNER_ID = int(os.environ["OWNER_ID"])

MIN_DEPOSIT = 10
UPI_ID = os.getenv("UPI_ID", "your-upi-id@upi")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo[DATABASE_NAME]
users = db.users
deposits = db.deposits
orders = db.orders
products = db.products
product_sessions = db.product_sessions

# Session vault: authentication material is never sent back to users or logged.
# Set SESSION_ENCRYPTION_KEY in Railway to a Fernet key. Keep it private; losing it
# makes encrypted session records unrecoverable. This bot does not implement
# Telegram user-account login or DM monitoring.
SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY")

def encrypt_session_value(session_value: str) -> str:
    if Fernet is None:
        raise RuntimeError("cryptography is required for encrypted session storage")
    if not SESSION_ENCRYPTION_KEY:
        raise RuntimeError("SESSION_ENCRYPTION_KEY is not configured")
    return Fernet(SESSION_ENCRYPTION_KEY.encode()).encrypt(session_value.encode()).decode()

def decrypt_session_value(encrypted_value: str) -> str:
    if Fernet is None:
        raise RuntimeError("cryptography is required for encrypted session storage")
    if not SESSION_ENCRYPTION_KEY:
        raise RuntimeError("SESSION_ENCRYPTION_KEY is not configured")
    return Fernet(SESSION_ENCRYPTION_KEY.encode()).decrypt(encrypted_value.encode()).decode()

def store_product_session_reference(product_id, watcher_user_id: int, session_value: str):
    """Store an encrypted, owner-controlled session credential for a product.

    This is a storage primitive only; no Telegram user-account login/monitoring
    is performed by this bot.
    """
    encrypted = encrypt_session_value(session_value)
    product_sessions.update_one(
        {"product_id": str(product_id)},
        {"$set": {
            "product_id": str(product_id),
            "watcher_user_id": int(watcher_user_id),
            "session_encrypted": encrypted,
            "updated_at": now(),
        }, "$setOnInsert": {"created_at": now()}},
        upsert=True,
    )

def get_product_session_reference(product_id):
    """Return only non-secret metadata; never return the decrypted session to UI/logs."""
    doc = product_sessions.find_one(
        {"product_id": str(product_id)},
        {"_id": 0, "watcher_user_id": 1, "created_at": 1, "updated_at": 1},
    )
    return doc

AMOUNT, UPI_PROOF, REDEEM_PROOF, REDEEM_SCREENSHOT, PRODUCT_NAME = range(5)

def now():
    return datetime.now(timezone.utc)

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
        {"$set": {"name": tg_user.full_name, "username": tg_user.username, "updated_at": now()},
         "$setOnInsert": {"balance": 0, "created_at": now()}},
        upsert=True,
    )

def main_menu(user_id=None):
    buttons = [
        [InlineKeyboardButton("🛒 Buy Now", callback_data="buy"),
         InlineKeyboardButton("💳 Add Funds", callback_data="add_funds")],
        [InlineKeyboardButton("📦 My Orders", callback_data="orders")],
        [InlineKeyboardButton("🆘 Support", callback_data="support"),
         InlineKeyboardButton("👤 My Profile", callback_data="profile")],
    ]
    if user_id == OWNER_ID:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def add_funds_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 UPI (PhonePe/GPay/Paytm)", callback_data="pay_upi")],
        [InlineKeyboardButton("🎁 Google Play Redeem Code", callback_data="pay_redeem")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ])

def back_home():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])

def admin_panel_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"), InlineKeyboardButton("💰 Balance Management", callback_data="admin_balance")],
        [InlineKeyboardButton("💳 Deposit Requests", callback_data="admin_deposits"), InlineKeyboardButton("📦 Orders", callback_data="admin_orders")],
        [InlineKeyboardButton("🛍️ Products / Accounts", callback_data="admin_products"), InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"), InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ])

def admin_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_panel")]])

def admin_products_menu():
    buttons = []
    for product in products.find({"active": {"$ne": False}}).sort("created_at", 1):
        buttons.append([InlineKeyboardButton(
            f"🛍️ {product.get('name', 'Unnamed Product')}",
            callback_data=f"admin_product:{product['_id']}"
        )])
    buttons.append([InlineKeyboardButton("➕ Add New Product", callback_data="admin_add_product")])
    buttons.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def user_products_menu():
    buttons = []
    for product in products.find({"active": {"$ne": False}}).sort("created_at", 1):
        buttons.append([InlineKeyboardButton(
            f"🛍️ {product.get('name', 'Unnamed Product')}",
            callback_data=f"buy_product:{product['_id']}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
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
        [InlineKeyboardButton("✅ I HAVE JOINED ALL", callback_data="verify_join")]
    ])


async def check_force_join(bot, user_id):
    for _, chat_id, _ in FORCE_JOIN_CHATS:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                return False
            if member.status == "restricted" and getattr(member, "is_member", False) is False:
                return False
        except Exception as exc:
            logger.warning("Force-join check failed for %s: %s", chat_id, exc)
            return False
    return True


async def send_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 JOIN TO CONTINUE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Please join all the channels and the support group below to use this bot.\n\n"
        "Once you have joined all three, tap Verify to continue.\n\n"
        "You need to be a member of all 3 for verification to pass."
    )
    return await update.message.reply_text(text, reply_markup=force_join_menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    # Show Force Join only if the user is NOT a member of all 3 required chats.
    # If already joined all 3, go directly to the main menu.
    if await check_force_join(context.bot, update.effective_user.id):
        await update.message.reply_text(
            f"👋 Welcome, {update.effective_user.first_name}!\n\n"
            "🛒 Browse products and manage your wallet from the menu below.",
            reply_markup=main_menu(update.effective_user.id),
        )
        return

    await send_force_join(update, context)


async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    if not await check_force_join(context.bot, q.from_user.id):
        await q.answer(
            "❌ You have not joined all required channels/groups yet.",
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
            "🛒 Browse products and manage your wallet from the menu below."
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
            "Select an option below:",
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
                f"👥 USERS\n━━━━━━━━━━━━━━━━━━\n\nTotal users: {total}\n\n"
                "Search: use the Balance Management option for direct user ID adjustments.",
                reply_markup=admin_back()
            )
        elif action == "admin_balance":
            await q.edit_message_text(
                "💰 BALANCE MANAGEMENT\n━━━━━━━━━━━━━━━━━━\n\n"
                "Use these owner-only commands in DM:\n"
                "Dd 70 USER_ID\n"
                "Ss 70 USER_ID\n\n"
                "In @genzportals, reply to a user's message with:\n"
                "Dd 70\nSs 70",
                reply_markup=admin_back()
            )
        elif action == "admin_deposits":
            pending = deposits.count_documents({"status": "pending"})
            upi = deposits.count_documents({"status": "pending", "method": "UPI"})
            gp = deposits.count_documents({"status": "pending", "method": "Google Play Redeem Code"})
            await q.edit_message_text(
                f"💳 DEPOSIT REQUESTS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Pending: {pending}\n"
                f"UPI: {upi}\n"
                f"Google Play: {gp}\n\n"
                "New requests are sent directly to your owner DM with Approve / Reject buttons.",
                reply_markup=admin_back()
            )
        elif action == "admin_orders":
            total = orders.count_documents({})
            pending = orders.count_documents({"status": "pending"})
            completed = orders.count_documents({"status": "completed"})
            cancelled = orders.count_documents({"status": "cancelled"})
            await q.edit_message_text(
                f"📦 ORDERS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Total: {total}\nPending: {pending}\nCompleted: {completed}\nCancelled: {cancelled}",
                reply_markup=admin_back()
            )
        elif action == "admin_products":
            total = products.count_documents({"active": {"$ne": False}})
            await q.edit_message_text(
                f"🛍️ PRODUCTS / ACCOUNTS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Total products: {total}\n\n"
                "Select a product or add a new one:",
                reply_markup=admin_products_menu()
            )
            return
        elif action == "admin_add_product":
            await q.edit_message_text(
                "➕ ADD NEW PRODUCT\n━━━━━━━━━━━━━━━━━━\n\n"
                "Send the product name.",
                reply_markup=admin_back()
            )
            return PRODUCT_NAME
        elif action.startswith("admin_product:"):
            from bson import ObjectId
            raw_id = action.split(":", 1)[1]
            try:
                product = products.find_one({"_id": ObjectId(raw_id), "active": {"$ne": False}})
            except Exception:
                product = None
            if not product:
                await q.answer("Product not found.", show_alert=True)
                return
            product_id = str(product["_id"])
            await q.edit_message_text(
                f"🛍️ {product.get('name', 'Unnamed Product')}\n\n"
                "Product added successfully.\n\n"
                "Select an option below:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑️ Delete Product", callback_data=f"admin_delete_product:{product_id}")],
                    [InlineKeyboardButton("⬅️ Products / Accounts", callback_data="admin_products")],
                ])
            )
            return
        elif action.startswith("admin_delete_product:"):
            from bson import ObjectId
            raw_id = action.split(":", 1)[1]
            try:
                product = products.find_one({"_id": ObjectId(raw_id), "active": {"$ne": False}})
            except Exception:
                product = None
            if not product:
                await q.answer("Product not found or already deleted.", show_alert=True)
                return
            product_id = str(product["_id"])
            await q.edit_message_text(
                f"⚠️ DELETE PRODUCT\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Are you sure you want to delete:\n\n🛍️ {product.get('name', 'Unnamed Product')}\n\n"
                "This will remove it from the product lists.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ YES, DELETE", callback_data=f"admin_delete_confirm:{product_id}")],
                    [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_product:{product_id}")],
                ])
            )
            return
        elif action.startswith("admin_delete_confirm:"):
            from bson import ObjectId
            raw_id = action.split(":", 1)[1]
            try:
                oid = ObjectId(raw_id)
            except Exception:
                await q.answer("Invalid product.", show_alert=True)
                return
            product = products.find_one({"_id": oid, "active": {"$ne": False}})
            if not product:
                await q.answer("Product not found or already deleted.", show_alert=True)
                return
            products.update_one(
                {"_id": oid},
                {"$set": {"active": False, "deleted_at": now(), "updated_at": now()}}
            )
            await q.edit_message_text(
                f"✅ Product deleted successfully.\n\n🛍️ {product.get('name', 'Unnamed Product')}",
                reply_markup=admin_products_menu()
            )
            return
        elif action == "admin_stats":
            total_users = users.count_documents({})
            total_deposits = deposits.count_documents({"status": "approved"})
            total_orders = orders.count_documents({})
            pending = deposits.count_documents({"status": "pending"})
            sales = list(orders.aggregate([{"$match": {"status": "completed"}}, {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$amount", 0]}}}}]))
            total_sales = sales[0]["total"] if sales else 0
            await q.edit_message_text(
                f"📊 STATISTICS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Total users: {total_users}\n"
                f"Approved deposits: {total_deposits}\n"
                f"Total orders: {total_orders}\n"
                f"Total sales: ₹{total_sales}\n"
                f"Pending deposit requests: {pending}",
                reply_markup=admin_back()
            )
        elif action == "admin_broadcast":
            await q.edit_message_text(
                "📢 BROADCAST\n━━━━━━━━━━━━━━━━━━\n\n"
                "Send the broadcast message in your DM as:\n"
                "BROADCAST: your message here\n\n"
                "Only the owner can use this command.",
                reply_markup=admin_back()
            )
        elif action == "admin_settings":
            await q.edit_message_text(
                "⚙️ SETTINGS\n━━━━━━━━━━━━━━━━━━\n\n"
                f"UPI ID: {UPI_ID}\n"
                "Support: @genzportals\n"
                "Force Join: @zyXzo, @arcfluxx, @genzportals\n\n"
                "Current settings are controlled through Railway environment variables/code.",
                reply_markup=admin_back()
            )
        return

    if q.data == "home":
        await q.edit_message_text(
            f"👋 Welcome, {q.from_user.first_name}!\n\n"
            "🛒 Browse products and manage your wallet from the menu below.",
            reply_markup=main_menu(q.from_user.id)
        )

    elif q.data == "add_funds":
        balance = users.find_one({"user_id": q.from_user.id}).get("balance", 0)
        await q.edit_message_text(
            f"🏦 ADD FUNDS\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Wallet Balance: ₹{balance}\n\n"
            "👇 Select Payment Method:",
            reply_markup=add_funds_menu()
        )

    elif q.data in ("pay_upi", "pay_redeem"):
        method = "UPI" if q.data == "pay_upi" else "Google Play Redeem Code"
        context.user_data["deposit_method"] = method
        await q.edit_message_text(
            "💰 ENTER AMOUNT\n━━━━━━━━━━━━━━━━━━\n\n"
            "Send the amount you want to deposit.\n\n"
            "Example: ₹50, ₹100, ₹500\n\n"
            "⚠️ Minimum deposit: ₹10",
            reply_markup=back_home()
        )
        return AMOUNT

    elif q.data == "buy":
        total = products.count_documents({"active": {"$ne": False}})
        if total == 0:
            await q.edit_message_text(
                "🛒 BUY NOW\n━━━━━━━━━━━━━━━━━━\n\n"
                "No products are available right now.",
                reply_markup=back_home()
            )
        else:
            await q.edit_message_text(
                "🛒 BUY NOW\n━━━━━━━━━━━━━━━━━━\n\n"
                "Select a product:",
                reply_markup=user_products_menu()
            )

    elif q.data.startswith("buy_product:"):
        from bson import ObjectId
        raw_id = q.data.split(":", 1)[1]
        try:
            product = products.find_one({"_id": ObjectId(raw_id), "active": {"$ne": False}})
        except Exception:
            product = None
        if not product:
            await q.answer("Product not found.", show_alert=True)
            return
        await q.edit_message_text(
            f"🛍️ {product.get('name', 'Unnamed Product')}\n\n"
            "Product details and purchase options will be added next.",
            reply_markup=user_products_menu()
        )

    elif q.data == "orders":
        await q.edit_message_text("📦 MY ORDERS\n\nNo orders yet.", reply_markup=back_home())

    elif q.data == "support":
        await q.edit_message_text(
            "🆘 SUPPORT\n\nPlease contact the administrator for assistance.",
            reply_markup=back_home()
        )

    elif q.data == "profile":
        u = users.find_one({"user_id": q.from_user.id}) or {}
        await q.edit_message_text(
            f"👤 MY PROFILE\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 User ID: {q.from_user.id}\n"
            f"👤 Name: {q.from_user.full_name}\n"
            f"💰 Balance: ₹{u.get('balance', 0)}",
            reply_markup=back_home()
        )

    return ConversationHandler.END

async def product_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return ConversationHandler.END

    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("❌ Please send a valid product name.")
        return PRODUCT_NAME

    existing = products.find_one({"name": name, "active": {"$ne": False}})
    if existing:
        await update.message.reply_text(
            "❌ A product with this name already exists.\n\n"
            "Please send a different product name."
        )
        return PRODUCT_NAME

    products.insert_one({
        "name": name,
        "active": True,
        "created_at": now(),
        "updated_at": now(),
    })

    await update.message.reply_text(
        f"✅ Product button added successfully.\n\n"
        f"🛍️ Product: {name}\n\n"
        "The product is now available automatically in Admin Panel → Products / Accounts "
        "and in users' Buy Now menu."
    )
    context.user_data.pop("product_name", None)
    return ConversationHandler.END

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid whole amount.", reply_markup=back_home())
        return AMOUNT

    if amount < MIN_DEPOSIT:
        await update.message.reply_text(
            f"❌ Invalid Amount\n\nMinimum deposit amount is ₹{MIN_DEPOSIT}.\n\nPlease enter an amount of ₹{MIN_DEPOSIT} or more."
        )
        return AMOUNT

    context.user_data["deposit_amount"] = amount
    method = context.user_data.get("deposit_method")

    if method == "UPI":
        await update.message.reply_text(
            f"💳 UPI PAYMENT - ₹{amount}\n━━━━━━━━━━━━━━━━━━\n\n"
            "📷 QR CODE: Add your UPI QR image in the next bot update.\n\n"
            f"UPI ID: {UPI_ID}\n\n"
            "1️⃣ Pay the exact amount.\n"
            "2️⃣ After successful payment, press the button below.\n"
            "3️⃣ Then send a clear payment screenshot.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I HAVE PAID", callback_data="upi_paid")],
                [InlineKeyboardButton("⬅️ Cancel", callback_data="home")]
            ])
        )
    else:
        await update.message.reply_text(
            f"🎁 GOOGLE PLAY REDEEM CODE\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Deposit Amount: ₹{amount}\n\n"
            "📸 First send your redeem code.\n"
            "Then send a screenshot/proof of purchase.\n\n"
            "⚠️ NOTE:\n"
            "• Submit codes only from legitimate/authorized sources.\n"
            "• The code must be unused and valid.\n"
            "• Keep your purchase receipt/proof available for verification.\n\n"
            "After sending the code and screenshot, your request will be reviewed manually.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="home")]])
        )
    return UPI_PROOF if method == "UPI" else REDEEM_PROOF

async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "upi_paid":
        return ConversationHandler.END

    await q.message.delete()
    await q.message.chat.send_message(
        "📸 Now send your payment screenshot.\n\n"
        "Please send a clear screenshot of your successful payment."
    )
    context.user_data["waiting_proof"] = True
    return UPI_PROOF

async def redeem_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = (update.message.text or "").strip()
    if not code:
        await update.message.reply_text("❌ Please send a valid redeem code.")
        return REDEEM_PROOF

    context.user_data["redeem_code"] = code
    await update.message.reply_text(
        "✅ Redeem code received.\n\n"
        "📸 Now send your purchase screenshot.\n"
        "Please send a clear screenshot of the purchase/proof."
    )
    return REDEEM_SCREENSHOT

async def proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("deposit_method") == "Google Play Redeem Code":
        waiting = context.user_data.get("redeem_code")
    else:
        waiting = context.user_data.get("waiting_proof")

    if not waiting:
        return ConversationHandler.END

    method = context.user_data.get("deposit_method")
    amount = context.user_data.get("deposit_amount")

    if not update.message.photo:
        await update.message.reply_text("❌ Please send the screenshot as an image.")
        return UPI_PROOF if method == "UPI" else REDEEM_SCREENSHOT

    file_id = update.message.photo[-1].file_id
    deposit = {
        "user_id": update.effective_user.id,
        "name": update.effective_user.full_name,
        "username": update.effective_user.username,
        "method": method,
        "amount": amount,
        "proof_file_id": file_id,
        "status": "pending",
        "created_at": now(),
    }
    if method == "Google Play Redeem Code":
        deposit["redeem_code"] = context.user_data.get("redeem_code")
    result = deposits.insert_one(deposit)

    await update.message.reply_text(
        "✅ Submitted!\n\n"
        "Your payment screenshot has been submitted successfully.\n\n"
        "⏳ Please wait for admin approval."
    )

    caption = (
        "💳 NEW DEPOSIT REQUEST\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {update.effective_user.full_name}\n"
        f"🆔 User ID: {update.effective_user.id}\n"
        f"💰 Amount: ₹{amount}\n"
        f"📌 Method: {method}\n"
        + (f"🎁 Redeem Code: {context.user_data.get('redeem_code')}\n" if method == "Google Play Redeem Code" else "")
        + f"🆔 Request: {result.inserted_id}\n\n"
        "Review the attached proof and approve/reject manually."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{result.inserted_id}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{result.inserted_id}")
    ]])
    await context.bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=keyboard)

    context.user_data.clear()
    return ConversationHandler.END

async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        await q.answer("Not authorized.", show_alert=True)
        return

    action, raw_id = q.data.split(":", 1)
    from bson import ObjectId
    try:
        oid = ObjectId(raw_id)
    except Exception:
        return

    dep = deposits.find_one({"_id": oid})
    if not dep or dep["status"] != "pending":
        await q.answer("Already processed or not found.", show_alert=True)
        return

    if action == "approve":
        users.update_one({"user_id": dep["user_id"]}, {"$inc": {"balance": dep["amount"]}})
        deposits.update_one({"_id": oid}, {"$set": {"status": "approved", "approved_at": now()}})
        await context.bot.send_message(
            dep["user_id"],
            f"✅ Payment Approved\n\n₹{dep['amount']} has been added to your wallet."
        )
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ APPROVED")
    else:
        deposits.update_one({"_id": oid}, {"$set": {"status": "rejected", "rejected_at": now()}})
        await context.bot.send_message(
            dep["user_id"],
            "❌ Payment Rejected\n\nYour deposit request was not approved."
        )
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n❌ REJECTED")



ADMIN_BALANCE_GROUP = "genzportals"


def parse_admin_balance_command(text):
    parts = (text or "").strip().split()
    if len(parts) not in (2, 3):
        return None
    action = parts[0].lower()
    if action not in ("dd", "ss"):
        return None
    try:
        amount = int(parts[1])
    except ValueError:
        return None
    if amount <= 0:
        return None
    user_id = None
    if len(parts) == 3:
        try:
            user_id = int(parts[2])
        except ValueError:
            return None
    return action, amount, user_id


async def admin_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This command is owner-only. In the support group, the owner can simply
    # reply to a user's message with: Dd 68 or Ss 68.
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    raw_text = (update.message.text or "").strip()
    if raw_text.upper().startswith("BROADCAST:"):
        message = raw_text.split(":", 1)[1].strip()
        if not message:
            await update.message.reply_text("❌ Broadcast message is empty.")
            return
        sent = failed = 0
        for u in users.find({}, {"user_id": 1, "_id": 0}):
            try:
                await context.bot.send_message(u["user_id"], message)
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"📢 Broadcast complete.\n\nSent: {sent}\nFailed: {failed}")
        return

    parsed = parse_admin_balance_command(update.message.text)
    if not parsed:
        return

    action, amount, supplied_user_id = parsed
    chat = update.effective_chat

    if chat.type == "private":
        # DM format: Dd 70 9983456788 / Ss 70 9983456788
        if supplied_user_id is None:
            await update.message.reply_text(
                "❌ Format:\nDd 70 USER_ID\nSs 70 USER_ID"
            )
            return
        target_user_id = supplied_user_id
    else:
        # Group format: owner replies to the target user's message with Dd 68 / Ss 68.
        if chat.username != ADMIN_BALANCE_GROUP or supplied_user_id is not None:
            return
        replied = update.message.reply_to_message
        if not replied or not replied.from_user:
            await update.message.reply_text(
                "❌ Reply to the user's message and send: Dd 68 or Ss 68"
            )
            return
        target_user_id = replied.from_user.id

    if action == "dd":
        result = users.update_one(
            {"user_id": target_user_id},
            {
                "$inc": {"balance": amount},
                "$set": {"updated_at": now()},
                "$setOnInsert": {"created_at": now()},
            },
            upsert=True,
        )
        new_user = users.find_one({"user_id": target_user_id}, {"balance": 1}) or {}
        new_balance = new_user.get("balance", amount)
        await update.message.reply_text(
            f"New balance added - ₹{amount}\n"
            f"💰 New Balance: ₹{new_balance}"
        )
        try:
            await context.bot.send_message(
                target_user_id,
                f"💰 ₹{amount} has been added to your wallet.\n\n"
                f"Current balance: ₹{new_balance}"
            )
        except Exception:
            pass
        return

    # Ss = subtract. Never allow the wallet to go below zero.
    result = users.update_one(
        {"user_id": target_user_id, "balance": {"$gte": amount}},
        {"$inc": {"balance": -amount}, "$set": {"updated_at": now()}},
    )
    if result.matched_count == 0:
        current = users.find_one({"user_id": target_user_id}, {"balance": 1}) or {}
        current_balance = current.get("balance", 0)
        await update.message.reply_text(
            f"❌ Insufficient balance.\n\n"
            f"👤 User ID: {target_user_id}\n"
            f"💰 Current Balance: ₹{current_balance}\n"
            f"➖ Requested: ₹{amount}"
        )
        return

    new_user = users.find_one({"user_id": target_user_id}, {"balance": 1}) or {}
    new_balance = new_user.get("balance", 0)
    await update.message.reply_text(
        f"✅ Balance Deducted\n\n"
        f"👤 User ID: {target_user_id}\n"
        f"➖ Deducted: ₹{amount}\n"
        f"💰 New Balance: ₹{new_balance}"
    )
    try:
        await context.bot.send_message(
            target_user_id,
            f"💳 ₹{amount} has been deducted from your wallet.\n\n"
            f"Current balance: ₹{new_balance}"
        )
    except Exception:
        pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(callbacks, pattern=r"^(pay_upi|pay_redeem|admin_add_product)$"),
        ],
        states={
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received),
            ],
            UPI_PROOF: [
                CallbackQueryHandler(paid_callback, pattern=r"^upi_paid$"),
                MessageHandler(filters.PHOTO, proof_received),
            ],
            REDEEM_PROOF: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_code_received),
            ],
            REDEEM_SCREENSHOT: [
                MessageHandler(filters.PHOTO, proof_received),
            ],
            PRODUCT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_name_received),
            ],
        },
        fallbacks=[CallbackQueryHandler(callbacks)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    # Conversation handlers must run before the catch-all owner balance
    # text handler, otherwise product names / deposit inputs get consumed
    # before their active conversation can receive them.
    app.add_handler(conv, group=0)

    # Owner-only manual balance controls.
    # DM: Dd 70 USER_ID / Ss 70 USER_ID
    # Support group reply: Dd 68 / Ss 68
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_balance_command), group=1)
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^(approve|reject):"), group=1)
    app.add_handler(CallbackQueryHandler(callbacks), group=2)

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
