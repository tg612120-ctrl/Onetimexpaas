import asyncio
import os
import sqlite3
import zipfile
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import GetPasswordRequest, GetAuthorizationsRequest, ResetAuthorizationRequest
from config import API_ID, API_HASH

active_clients = {}


async def get_devices(phone_number: str):
    """Returns (devices, None) or (None, error_message).

    devices is a list of dicts, sorted with the current (bot) session first:
    {hash, current, label}
    """
    client = active_clients.get(phone_number)
    if not client or not client.is_connected():
        return None, "Session not connected. Please tap 'Get Code' once first to initialize it."

    try:
        result = await client(GetAuthorizationsRequest())
        devices = []
        for auth in result.authorizations:
            model = getattr(auth, "device_model", "") or ""
            platform = getattr(auth, "platform", "") or ""
            app_name = getattr(auth, "app_name", "") or ""
            label = " - ".join([p for p in [app_name, model or platform] if p]) or "Unknown device"
            devices.append({
                "hash": auth.hash,
                "current": bool(auth.current),
                "label": label,
                "country": getattr(auth, "country", "") or "",
                "ip": getattr(auth, "ip", "") or "",
            })
        devices.sort(key=lambda d: not d["current"])
        return devices, None
    except Exception as e:
        return None, str(e)


async def terminate_device(phone_number: str, auth_hash: int):
    """Terminates one non-current session by its hash."""
    client = active_clients.get(phone_number)
    if not client or not client.is_connected():
        return False, "Session not connected."
    try:
        await client(ResetAuthorizationRequest(hash=auth_hash))
        return True, None
    except Exception as e:
        return False, str(e)


# =====================================================================
# Universal session loader: accepts a Telethon string, a Pyrogram string,
# a .session file (Telethon or Pyrogram format), or a .zip containing
# a .session file or a Telegram Desktop "tdata" folder.
#
# Everything converges into ONE Telethon TelegramClient, so the rest of
# the bot (OTP fetching, Devices list, etc.) never needs to know which
# format the admin originally uploaded.
#
# NOTE: Pyrogram and tdata support need extra packages that are NOT
# part of Telethon:
#   pip install telegram-session-converter opentele --break-system-packages
# If these are not installed, string/.session (Telethon format) still
# works; Pyrogram/tdata inputs will fail with a clear message instead
# of crashing the bot.
# =====================================================================

def _sqlite_session_kind(path: str) -> str:
    """Returns 'telethon', 'pyrogram', or 'unknown' by inspecting the
    table names inside a .session SQLite file."""
    try:
        con = sqlite3.connect(path)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except Exception:
        return "unknown"
    if {"sessions", "entities"} <= tables:
        return "telethon"
    if {"sessions", "peers"} <= tables:
        return "pyrogram"
    return "unknown"


async def _client_from_telethon_file(session_file_path: str):
    session_name = session_file_path[:-8] if session_file_path.endswith(".session") else session_file_path
    client = TelegramClient(session_name, API_ID, API_HASH)
    await client.connect()
    return client


async def _client_from_pyrogram_file(pyrogram_file_path: str, work_dir: str):
    try:
        from session_converter import SessionManager
    except ImportError:
        raise RuntimeError("Pyrogram session support is not installed on this server (pip install telegram-session-converter).")
    sm = SessionManager.from_pyrogram_session_file(pyrogram_file_path)
    out_path = os.path.join(work_dir, "converted_telethon.session")
    sm.telethon_file(out_path)
    return await _client_from_telethon_file(out_path)


async def _client_from_pyrogram_string(pyrogram_string: str, work_dir: str):
    try:
        from session_converter import SessionManager
    except ImportError:
        raise RuntimeError("Pyrogram session support is not installed on this server (pip install telegram-session-converter).")
    sm = SessionManager.from_pyrogram_string_session(pyrogram_string)
    out_path = os.path.join(work_dir, "converted_telethon.session")
    sm.telethon_file(out_path)
    return await _client_from_telethon_file(out_path)


async def _client_from_tdata(tdata_dir: str, work_dir: str):
    try:
        from opentele.td import TDesktop
        from opentele.api import UseCurrentSession
    except ImportError:
        raise RuntimeError("Tdata session support is not installed on this server (pip install opentele).")
    tdesk = TDesktop(tdata_dir)
    if not tdesk.isLoaded():
        raise RuntimeError("Could not read tdata (missing or corrupted files).")
    session_path = os.path.join(work_dir, "converted_telethon.session")
    client = await tdesk.ToTelethon(session=session_path, flag=UseCurrentSession)
    return client


def _find_tdata_dir(root: str):
    """Looks for a folder named 'tdata', or a folder containing the
    tdata-specific 'key_datas' file, anywhere inside `root`."""
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath).lower() == "tdata":
            return dirpath
        if "key_datas" in filenames:
            return dirpath
    return None


def _find_session_files(root: str):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            if f.lower().endswith(".session"):
                found.append(os.path.join(dirpath, f))
    return found


async def _resolve_any_session(kind: str, data, work_dir: str):
    """kind: 'telethon_string' | 'pyrogram_string' | 'file' | 'zip'.
    data: the string itself, or a local file path already saved to disk.
    Returns (connected_telethon_client, None) or (None, error_message).
    """
    try:
        if kind == "telethon_string":
            client = TelegramClient(StringSession(data), API_ID, API_HASH)
            await client.connect()

        elif kind == "pyrogram_string":
            client = await _client_from_pyrogram_string(data, work_dir)

        elif kind == "file":
            sess_kind = _sqlite_session_kind(data)
            if sess_kind == "telethon":
                client = await _client_from_telethon_file(data)
            elif sess_kind == "pyrogram":
                client = await _client_from_pyrogram_file(data, work_dir)
            else:
                return None, "Unrecognized .session file format."

        elif kind == "zip":
            extract_dir = os.path.join(work_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(data, "r") as zf:
                zf.extractall(extract_dir)

            tdata_dir = _find_tdata_dir(extract_dir)
            if tdata_dir:
                client = await _client_from_tdata(tdata_dir, work_dir)
            else:
                session_files = _find_session_files(extract_dir)
                if not session_files:
                    return None, "No valid session (.session file or tdata folder) found inside the zip."
                path = session_files[0]
                sess_kind = _sqlite_session_kind(path)
                if sess_kind == "telethon":
                    client = await _client_from_telethon_file(path)
                elif sess_kind == "pyrogram":
                    client = await _client_from_pyrogram_file(path, work_dir)
                else:
                    return None, "Unrecognized .session file format inside the zip."
        else:
            return None, "Unknown session type."

        if not await client.is_user_authorized():
            await client.disconnect()
            return None, "Session is not authorized or has expired."

        return client, None
    except Exception as e:
        return None, str(e)


async def process_uploaded_session(kind: str, data, work_dir: str):
    """High-level entry point used by bot.py.

    Returns (info_dict, None) on success, or (None, error_message) on failure.
    info_dict = {phone_number, user_id, two_step_enabled, session_string}
    session_string is always a Telethon StringSession, regardless of the
    original input format.
    """
    client, error = await _resolve_any_session(kind, data, work_dir)
    if error:
        return None, error

    try:
        me = await client.get_me()

        two_step_enabled = False
        try:
            pwd = await client(GetPasswordRequest())
            two_step_enabled = bool(getattr(pwd, "has_password", False))
        except Exception:
            two_step_enabled = False

        return {
            "phone_number": me.phone or "Unknown",
            "user_id": me.id,
            "two_step_enabled": two_step_enabled,
            "session_string": StringSession.save(client.session),
        }, None
    except Exception as e:
        return None, str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def extract_account_info(session_string: str):
    """Connects with the given session string and reads phone number,
    user id, and whether 2-Step Verification is enabled.

    IMPORTANT: Telegram never exposes the actual 2-Step password through
    the API (it only stores a salted hash, never the plain text), so this
    can only tell you ON/OFF, never the password itself.

    Returns (info_dict, None) on success, or (None, error_message) on failure.
    """
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    try:
        await client.connect()

        if not await client.is_user_authorized():
            return None, "Session is not authorized or has expired."

        me = await client.get_me()

        two_step_enabled = False
        try:
            pwd = await client(GetPasswordRequest())
            two_step_enabled = bool(getattr(pwd, "has_password", False))
        except Exception:
            two_step_enabled = False

        return {
            "phone_number": me.phone or "Unknown",
            "user_id": me.id,
            "two_step_enabled": two_step_enabled,
        }, None
    except Exception as e:
        return None, str(e)
    finally:
        await client.disconnect()


async def start_userbot_for_account(phone_number: str, session_string: str, bot_instance, customer_chat_id: int):
    """Connects the account and keeps the client in active_clients.

    No OTP listener here: the OTP is sent to the user only when they
    press the "Get Code" button (see get_otp_handler in bot.py).
    """
    try:
        # already connected -> don't create a duplicate client
        existing = active_clients.get(phone_number)
        if existing and existing.is_connected():
            return

        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            await bot_instance.send_message(
                customer_chat_id,
                f"⚠️ Account {phone_number} session is not authorized or expired."
            )
            await client.disconnect()
            return

        active_clients[phone_number] = client
    except Exception as e:
        print(f"Error starting userbot for {phone_number}: {e}")
