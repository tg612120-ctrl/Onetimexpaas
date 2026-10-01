from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client["telegram_otp_bot"]

categories_col = db["categories"]
accounts_col = db["accounts"]
users_col = db["users"]
payments_col = db["payments"]
counters_col = db["counters"]
promo_col = db["promo_codes"]
supplier_col = db["supplier_config"]


async def get_next_sequence(name: str) -> int:
    counter = await counters_col.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return counter["seq"]


async def init_db():
    await categories_col.create_index("name", unique=True)
    await accounts_col.create_index("account_id", unique=True)
    await users_col.create_index("user_id", unique=True)
    await promo_col.create_index("code", unique=True)


async def get_user(user_id: int):
    try:
        return await users_col.find_one_and_update(
            {"user_id": user_id},
            {"$setOnInsert": {"balance": 0.0, "is_verified": 0, "is_banned": 0, "referred_by": None, "referral_count": 0}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return await users_col.find_one({"user_id": user_id})


async def update_verification(user_id: int, status: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"is_verified": status}},
        upsert=True,
    )


async def update_balance(user_id: int, amount: float):
    await users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True,
    )


async def save_payment_record(user_id: int, amount: float, currency: str, status: str):
    await payments_col.insert_one({
        "user_id": user_id,
        "amount": amount,
        "currency": currency,
        "status": status,
        "timestamp": datetime.now(timezone.utc),
    })


async def get_user_payments(user_id: int):
    cursor = payments_col.find({"user_id": user_id}).sort("timestamp", -1)
    return await cursor.to_list(length=50)


async def get_categories():
    cursor = categories_col.find({})
    return [(doc["category_id"], doc["name"]) async for doc in cursor]


async def get_categories_with_counts():
    cursor = categories_col.find({})
    categories = await cursor.to_list(length=None)
    result = []
    for cat in categories:
        cat_id = cat["category_id"]
        cat_name = cat["name"]
        available_count = await accounts_col.count_documents({"category_id": cat_id, "is_sold": 0})
        result.append((cat_id, cat_name, available_count))
    return result


async def update_category_name(cat_id: int, new_name: str):
    await categories_col.update_one(
        {"category_id": cat_id},
        {"$set": {"name": new_name}}
    )


async def set_user_ban_status(user_id: int, status: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"is_banned": status}},
        upsert=True
    )


async def get_detailed_stock_stats():
    available_accounts = await accounts_col.count_documents({"is_sold": 0})
    sold_accounts = await accounts_col.count_documents({"is_sold": 1})
    total_users = await users_col.count_documents({})
    total_revenue = 0
    async for p in payments_col.find({"status": "SPEND_BUY_ACCOUNT"}):
        total_revenue += p.get("amount", 0)
    return {
        "available": available_accounts,
        "sold": sold_accounts,
        "users": total_users,
        "revenue": total_revenue
    }


async def get_all_sales_history(limit: int = 10):
    cursor = payments_col.find({"status": "SPEND_BUY_ACCOUNT"}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_all_user_ids():
    cursor = users_col.find({}, {"user_id": 1})
    users = await cursor.to_list(length=None)
    return [doc["user_id"] for doc in users]


# ================= PHASE 2: NEW FUNCTIONS =================

async def add_promo_code(code: str, discount_amount: float):
    await promo_col.update_one(
        {"code": code},
        {"$set": {"discount": discount_amount}},
        upsert=True
    )


async def use_promo_code(user_id: int, code: str):
    promo = await promo_col.find_one({"code": code})
    if not promo:
        return False, "Invalid promo code."
    
    # Check if user already used this code (optional tracking)
    user = await users_col.find_one({"user_id": user_id})
    used_codes = user.get("used_promo_codes", [])
    if code in used_codes:
        return False, "You have already used this promo code."
    
    discount = promo["discount"]
    await users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": discount}, "$push": {"used_promo_codes": code}}
    )
    return True, f"Successfully redeemed! ${discount} added to your balance."


async def set_supplier_config(api_url: str, api_key: str, is_active: bool):
    """Supplier API modular configuration placeholder"""
    await supplier_col.update_one(
        {"_id": "config"},
        {"$set": {"api_url": api_url, "api_key": api_key, "is_active": is_active}},
        upsert=True
    )


async def get_supplier_config():
    config = await supplier_col.find_one({"_id": "config"})
    if not config:
        return {"api_url": "", "api_key": "", "is_active": False}
    return config

# ==========================================================


async def add_category(name: str):
    existing = await categories_col.find_one({"name": name})
    if not existing:
        cat_id = await get_next_sequence("category_id")
        try:
            await categories_col.insert_one({"category_id": cat_id, "name": name})
        except DuplicateKeyError:
            pass


async def add_account(category_id: int, display_name: str, phone_number: str, session_string: str, price: float, two_step: str = ""):
    acc_id = await get_next_sequence("account_id")
    await accounts_col.insert_one({
        "account_id": acc_id,
        "category_id": category_id,
        "display_name": display_name,
        "phone_number": phone_number,
        "session_string": session_string,
        "price": price,
        "two_step": two_step,
        "is_sold": 0,
    })


async def get_available_accounts(category_id: int):
    cursor = accounts_col.find({"category_id": category_id, "is_sold": 0})
    return [(doc["account_id"], doc.get("display_name", doc["phone_number"]), doc["price"]) async for doc in cursor]


async def get_account_by_id(account_id: int):
    return await accounts_col.find_one({"account_id": account_id})


async def buy_account_safely(user_id: int, account_id: int):
    await get_user(user_id)
    account = await accounts_col.find_one({"account_id": account_id, "is_sold": 0})
    if not account:
        return "not_found", None, None, None, None

    price = account["price"]
    deducted = await users_col.update_one(
        {"user_id": user_id, "balance": {"$gte": price}},
        {"$inc": {"balance": -price}},
    )
    if deducted.modified_count == 0:
        return "low_balance", None, None, None, None

    claimed = await accounts_col.find_one_and_update(
        {"account_id": account_id, "is_sold": 0},
        {"$set": {"is_sold": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": price}})
        return "not_found", None, None, None, None

    await save_payment_record(user_id, price, "USD", "SPEND_BUY_ACCOUNT")
    return "success", claimed["phone_number"], claimed["session_string"], claimed["price"], claimed.get("two_step", "")


async def get_all_unsold_accounts():
    cursor = accounts_col.find({"is_sold": 0})
    return [(doc["account_id"], doc.get("display_name", doc["phone_number"]), doc["price"]) async for doc in cursor]


async def delete_account(acc_id: int):
    await accounts_col.delete_one({"account_id": acc_id})
