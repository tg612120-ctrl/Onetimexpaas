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
gp_requests_col = db["gp_requests"]
gp_codes_col = db["gp_codes"]


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
            {
                "$setOnInsert": {
                    "balance": 0.0,
                    "is_verified": 0,
                    "is_banned": 0,
                    "referred_by": None,
                    "referral_count": 0,
                    "referral_rewarded": 0,
                }
            },
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


async def save_star_payment(charge_id: str, user_id: int, stars: int, inr: float) -> bool:
    """Returns False if this charge_id was already processed (duplicate)."""
    try:
        await payments_col.insert_one({
            "_id": charge_id,
            "user_id": user_id,
            "amount": inr,
            "stars": stars,
            "currency": "STARS",
            "status": "TOPUP_SUCCESS",
            "timestamp": datetime.now(timezone.utc),
        })
        return True
    except DuplicateKeyError:
        return False


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

    await get_user(user_id)
    discount = promo["discount"]
    # atomic: sirf tab update hoga jab user ne ye code pehle use nahi kiya
    res = await users_col.update_one(
        {"user_id": user_id, "used_promo_codes": {"$ne": code}},
        {"$inc": {"balance": discount}, "$push": {"used_promo_codes": code}},
    )
    if res.modified_count == 0:
        return False, "You have already used this promo code."
    return True, f"Successfully redeemed! ₹{discount} added to your balance."


async def set_supplier_config(api_url: str, api_key: str, is_active: bool):
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


async def add_category(name: str):
    existing = await categories_col.find_one({"name": name})
    if not existing:
        cat_id = await get_next_sequence("category_id")
        try:
            await categories_col.insert_one({"category_id": cat_id, "name": name})
        except DuplicateKeyError:
            pass


async def add_account(category_id: int, display_name: str, phone_number: str, session_string: str, price: float, two_step: str = "", telegram_user_id=None, two_step_enabled: bool = False):
    acc_id = await get_next_sequence("account_id")
    await accounts_col.insert_one({
        "account_id": acc_id,
        "category_id": category_id,
        "display_name": display_name,
        "phone_number": phone_number,
        "session_string": session_string,
        "price": price,
        "two_step": two_step,
        "two_step_enabled": two_step_enabled,
        "telegram_user_id": telegram_user_id,
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
        return "not_found", None, None, None, None, None, None

    price = account["price"]
    deducted = await users_col.update_one(
        {"user_id": user_id, "balance": {"$gte": price}},
        {"$inc": {"balance": -price}},
    )
    if deducted.modified_count == 0:
        return "low_balance", None, None, None, None, None, None

    claimed = await accounts_col.find_one_and_update(
        {"account_id": account_id, "is_sold": 0},
        {"$set": {"is_sold": 1, "sold_to": user_id}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": price}})
        return "not_found", None, None, None, None, None, None

    await save_payment_record(user_id, price, "INR", "SPEND_BUY_ACCOUNT")
    return (
        "success",
        claimed["phone_number"],
        claimed["session_string"],
        claimed["price"],
        claimed.get("two_step", ""),
        claimed.get("telegram_user_id"),
        claimed.get("two_step_enabled", False),
    )


async def get_all_unsold_accounts():
    cursor = accounts_col.find({"is_sold": 0})
    return [(doc["account_id"], doc.get("display_name", doc["phone_number"]), doc["price"]) async for doc in cursor]


async def delete_account(acc_id: int):
    await accounts_col.delete_one({"account_id": acc_id})



# ---------------- Google Play redeem code requests ----------------
GP_MAX_PENDING = 3


async def create_gp_request(user_id: int, amount: int, code: str, file_id: str):
    """Returns (request_id, 'ok') or (None, 'limit' / 'duplicate')."""
    pending = await gp_requests_col.count_documents({"user_id": user_id, "status": "pending"})
    if pending >= GP_MAX_PENDING:
        return None, "limit"
    try:
        # _id = code, so the same code can never be submitted twice
        await gp_codes_col.insert_one({"_id": code, "user_id": user_id})
    except DuplicateKeyError:
        return None, "duplicate"
    req_id = await get_next_sequence("gp_request_id")
    await gp_requests_col.insert_one({
        "request_id": req_id,
        "user_id": user_id,
        "amount": amount,
        "code": code,
        "file_id": file_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    })
    return req_id, "ok"


async def get_gp_request(req_id: int):
    return await gp_requests_col.find_one({"request_id": req_id})


async def set_gp_status(req_id: int, status: str):
    """Atomic: only a pending request can be approved/rejected, and only once."""
    return await gp_requests_col.find_one_and_update(
        {"request_id": req_id, "status": "pending"},
        {"$set": {"status": status, "reviewed_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


async def release_gp_code(code: str):
    await gp_codes_col.delete_one({"_id": code})
