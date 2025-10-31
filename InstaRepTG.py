#!/usr/bin/env python3
"""
InstaRepPro – Telegram-only bot (fixed config crash)

Run:
    export BOT_TOKEN=123456:ABCDEF
    python core.py
"""

import json
import os
import random
import re
import threading
import time
import sys
import logging
from typing import List, Optional, Tuple
import requests
from rich.console import Console
from user_agent import generate_user_agent
from threading import Thread

# === Keep-alive web server ===

# ----------------------------------------------------------------------
# Logging & console
# ----------------------------------------------------------------------
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
CREDENTIALS_FILE = "stored_credentials.json"
CONFIG_FILE = "instarep_config.json"
DEFAULT_GLOBAL = {"human_delay": [1.5, 4.0]}
REPORT_OPTIONS = {
    1: "Spam",
    2: "Self-harm / Suicide",
    3: "Drugs",
    4: "Nudity",
    5: "Violence",
    6: "Hate Speech",
    7: "Bullying",
    8: "Impersonation",
}

# ----------------------------------------------------------------------
# ----------  FIXED CONFIG HANDLING  -----------------------------------
# ----------------------------------------------------------------------
def load_admins(path="admins.json"):
    """
    Load admin chat ids from admins.json (preferred) or admins.txt (one id per line).
    Returns list of strings.
    """
    base = os.path.dirname(__file__)
    jpath = os.path.join(base, path)
    try:
        with open(jpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Accept either ["123","456"] or {"admins": ["123", ...]}
        if isinstance(data, dict):
            admins = data.get("admins") or data.get("admin_ids") or []
        else:
            admins = data
        return [str(a) for a in admins if a]
    except Exception:
        # fallback to admins.txt (one id per line)
        tpath = os.path.join(base, "admins.txt")
        try:
            with open(tpath, "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            return []


def save_credentials(username: str, password: str, chat_id: str, ig_userid: str):
    try:
        if os.path.exists(CREDENTIALS_FILE):
            with open(CREDENTIALS_FILE, 'r') as f:
                creds = json.load(f)
        else:
            creds = {}
            
        creds[chat_id] = {
            "username": username,
            "password": password,
            "tg_chatid": chat_id,
            "ig_userid": ig_userid
        }
        
        with open(CREDENTIALS_FILE, 'w') as f:
            json.dump(creds, f)
        return True
    except:
        return False

def get_stored_credentials(chat_id: str):
    try:
        if os.path.exists(CREDENTIALS_FILE):
            with open(CREDENTIALS_FILE, 'r') as f:
                creds = json.load(f)
                return creds.get(chat_id, {}).get("username"), creds.get(chat_id, {}).get("password")
    except:
        pass
    return None, None

def _load_raw() -> dict:
    """Return the whole JSON file (or a fresh skeleton)."""
    if not os.path.exists(CONFIG_FILE):
        fresh = {"global": DEFAULT_GLOBAL.copy(), "chats": {}}
        _save_raw(fresh)
        return fresh
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # make sure top-level keys exist
        if "global" not in data:
            data["global"] = DEFAULT_GLOBAL.copy()
        if "chats" not in data:
            data["chats"] = {}
        return data
    except Exception as e:
        logger.error(f"Config load error: {e}")
        fresh = {"global": DEFAULT_GLOBAL.copy(), "chats": {}}
        _save_raw(fresh)
        return fresh


def _save_raw(data: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Config save error: {e}")


def get_human_delay() -> List[float]:
    return _load_raw()["global"].get("human_delay", [1.5, 4.0])


def get_chat_conf(chat_id: str) -> dict:
    """Return (and create if needed) the per-chat dict."""
    data = _load_raw()
    cid = str(chat_id)
    if cid not in data["chats"]:
        data["chats"][cid] = {
            "session": {},
            "targets": [],
            "settings": {"telegram_chat_id": cid},
        }
        _save_raw(data)
    return data["chats"][cid]


def save_chat_conf(conf: dict, chat_id: str):
    data = _load_raw()
    data["chats"][str(chat_id)] = conf
    _save_raw(data)


def get_session(chat_id: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    s = get_chat_conf(chat_id).get("session", {})
    return s.get("sessionid"), s.get("csrftoken"), s.get("user_agent"), s.get("user_id")


def save_session(chat_id: str, sessionid: str, csrftoken: str, user_agent: str, user_id: str):
    conf = get_chat_conf(chat_id)
    conf["session"] = {
        "sessionid": sessionid,
        "csrftoken": csrftoken,
        "user_agent": user_agent,
        "user_id": user_id,
    }
    save_chat_conf(conf, chat_id)


def get_targets(chat_id: str) -> List[str]:
    return get_chat_conf(chat_id).get("targets", [])


def add_targets(chat_id: str, new_targets: List[str]) -> int:
    conf = get_chat_conf(chat_id)
    existing = set(conf.get("targets", []))
    added = 0
    for t in new_targets:
        t = t.strip()
        if t and t not in existing:
            conf.setdefault("targets", []).append(t)
            existing.add(t)
            added += 1
    save_chat_conf(conf, chat_id)
    return added

# ----------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------
def human_sleep():
    lo, hi = get_human_delay()
    delay = random.uniform(lo, hi)
    time.sleep(delay)

# ----------------------------------------------------------------------
# Instagram core (unchanged – only minor type hints)
# ----------------------------------------------------------------------
def test_session(sessionid: str, user_agent: str) -> Tuple[bool, str]:
    try:
        human_sleep()
        r = requests.get(
            "https://i.instagram.com/api/v1/accounts/current_user/",
            headers={
                "User-Agent": user_agent,
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "en-US",
                "X-IG-Capabilities": "3brTvw==",
                "X-IG-Connection-Type": "WIFI",
                "Cookie": f"sessionid={sessionid}",
            },
            timeout=10,
        )
        if r.status_code == 200 and "user" in r.json():
            return True, r.cookies.get("csrftoken", "")
        return False, ""
    except Exception:
        return False, ""


def login_with_requests(username: str, password: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    s = requests.Session()
    user_agent = generate_user_agent()
    s.headers.update({"user-agent": user_agent, "x-requested-with": "XMLHttpRequest"})

    try:
        human_sleep()
        r = s.get("https://www.instagram.com/accounts/login/", timeout=10)
        csrf = s.cookies.get_dict().get("csrftoken")
        if not csrf:
            console.print("[red]Failed to retrieve CSRF token.[/red]")
            return None, None, None, None

        timestamp = str(int(time.time()))
        data = {
            "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{timestamp}:{password}",
            "optIntoOneTap": "false",
            "queryParams": "{}",
            "trustedDeviceRecords": "{}",
            "username": username,
        }
        headers = {
            "x-csrftoken": csrf,
            "x-instagram-ajax": "1008686036",
            "x-ig-app-id": "936619743392459",
            "x-asbd-id": "129477",
            "referer": "https://www.instagram.com/accounts/login/",
            "content-type": "application/x-www-form-urlencoded",
        }
        s.headers.update(headers)
        r = s.post("https://i.instagram.com/accounts/login/ajax/", data=data, timeout=10)
        res_json = r.json()

        if res_json.get("authenticated"):
            cookies = s.cookies.get_dict()
            sessionid = cookies.get("sessionid")
            csrftoken = cookies.get("csrftoken")
            user_id = str(res_json.get("userId", ""))
            if sessionid and csrftoken and user_id:
                return sessionid, csrftoken, user_agent, user_id
        elif res_json.get("message") in ["checkpoint_required", "challenge_required"]:
            console.print("[yellow]Challenge required – complete it in the Instagram app then try again.[/yellow]")
            return None, None, None, None
        else:
            console.print(f"[red]Login failed: {res_json.get('message','Unknown')}[/red]")
            
            return None, None, None, None
    except Exception as e:
        console.print(f"[red]Login error: {e}[/red]")
        return None, None, None, None


def fetch_target_id(username_or_id: str, sessionid: str, csrftoken: str) -> str:
    if re.fullmatch(r"\d+", username_or_id):
        return username_or_id
    # … (same as before) …
    try:
        human_sleep()
        response = requests.post(
            "https://i.instagram.com/api/v1/users/lookup/",
            headers={
                "User-Agent": "Instagram 99.4.0",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": f"csrftoken={csrftoken}; sessionid={sessionid}",
            },
            data={"signed_body": f"random.{username_or_id}"},
            timeout=10,
        )
        if response.status_code == 200:
            j = response.json()
            target_id = j.get("user_id") or j.get("user", {}).get("pk")
            if target_id:
                return str(target_id)
    except Exception:
        pass
    try:
        human_sleep()
        r = requests.get(f"https://www.instagram.com/{username_or_id}/", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        m = re.search(r'"profile_id":"(\d+)"', r.text)
        if m:
            return m.group(1)
    except Exception as e:
        raise ValueError(f"Could not fetch id for {username_or_id}: {e}")
    raise ValueError(f"Could not resolve target {username_or_id}")


def report_instagram(target_id: str, sessionid: str, csrftoken: str, report_type: int) -> Tuple[bool, str]:
    for attempt in range(5):
        human_sleep()
        try:
            r = requests.post(
                f"https://i.instagram.com/users/{target_id}/flag/",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Host": "i.instagram.com",
                    "Cookie": f"sessionid={sessionid}",
                    "X-CSRFToken": csrftoken,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=f"source_name=&reason_id={report_type}&frx_context=",
                timeout=10,
            )
            if r.status_code == 429:
                wait = 20 * (2 ** attempt)
                console.print(f"[yellow]Rate limited (attempt {attempt+1}), waiting {wait}s...[/yellow]")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return True, "ok"
            return False, f"status_{r.status_code}"
        except Exception as e:
            return False, f"error_{e}"
    return False, "rate_limited"

# ----------------------------------------------------------------------
# Reporting modes (unchanged – only pass the bot instance)
# ----------------------------------------------------------------------
def _notify(bot, chat_id: str, username: str, target_id: str, report_type: int, ok: bool, status: str):
    name = REPORT_OPTIONS.get(report_type, report_type)
    msg = f"Report → {username} (id: {target_id}), type: {name} => {status}"
    if ok:
        console.print(f"[green][{chat_id}] {msg}[/green]")
    else:
        console.print(f"[red][{chat_id}] {msg}[/red]")
    bot.send_message(chat_id, msg)


def mode_one_per_target_per_type(bot, chat_id: str, targets: List[str], report_type: int, runs_per_target: int, event: threading.Event):
    sessionid, csrftoken, _, _ = get_session(chat_id)
    if not sessionid:
        bot.send_message(chat_id, "No active session – login first.")
        return
    for t in targets:
        if event.is_set():
            bot.send_message(chat_id, "Stopped by user.")
            bot.send_main_menu(chat_id)  # Add menu after stop
            return
        try:
            tid = fetch_target_id(t, sessionid, csrftoken)
        except Exception as e:
            _notify(bot, chat_id, t, "N/A", report_type, False, f"resolve_failed: {e}")
            continue
        for _ in range(runs_per_target):
            if event.is_set():
                bot.send_message(chat_id, "Stopped by user.")
                return
            ok, status = report_instagram(tid, sessionid, csrftoken, report_type)
            _notify(bot, chat_id, t, tid, report_type, ok, status)
            if "rate_limited" in status:
                bot.send_message(chat_id, "Rate limited – job stopped.")
                return
    bot.send_message(chat_id, "One-mode job finished.")

def mode_many_per_type(bot, chat_id: str, targets: List[str], report_type: int, count_per_target: int, event: threading.Event):
    mode_one_per_target_per_type(bot, chat_id, targets, report_type, count_per_target, event)
    bot.send_message(chat_id, "Many-mode job finished.")


def mode_ordered_report(bot, chat_id: str, targets: List[str], order: List[int], event: threading.Event):
    sessionid, csrftoken, _, _ = get_session(chat_id)
    if not sessionid:
        bot.send_message(chat_id, "No active session – login first.")
        return
    for t in targets:
        if event.is_set():
            bot.send_message(chat_id, "Stopped by user.")
            return
        try:
            tid = fetch_target_id(t, sessionid, csrftoken)
        except Exception as e:
            _notify(bot, chat_id, t, "N/A", 0, False, f"resolve_failed: {e}")
            continue
        for rt in order:
            if event.is_set():
                bot.send_message(chat_id, "Stopped by user.")
                return
            ok, status = report_instagram(tid, sessionid, csrftoken, rt)
            _notify(bot, chat_id, t, tid, rt, ok, status)
            if "rate_limited" in status:
                bot.send_message(chat_id, "Rate limited – job stopped.")
                return
    bot.send_message(chat_id, "Ordered job finished.")

# ----------------------------------------------------------------------
# Telegram Bot (polling + inline keyboards)
# ----------------------------------------------------------------------
class SimpleTelegramBot:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base = f"https://api.telegram.org/bot{bot_token}"
        self.offset = None
        self.user_data: dict = {}          # chat_id → temporary state
        self.job_events: dict = {}         # chat_id → threading.Event

    # ---------- API helpers ----------
    def _api(self, method: str, **params):
        url = f"{self.base}/{method}"
        try:
            r = requests.post(url, data=params, timeout=30)
            return r.json()
        except Exception as e:
            logger.error(f"Telegram API error ({method}): {e}")
            return {"ok": False}

    def send_message(self, chat_id: str, text: str, reply_markup=None, parse_mode=None):
        params = {"chat_id": chat_id, "text": text}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            params["parse_mode"] = parse_mode
        self._api("sendMessage", **params)
    def report_menu_text(self):
        text = "*Available Report Types:*\n"
        for num, name in REPORT_OPTIONS.items():
            text += f"{num}. {name}\n"
        text += "\n*Choose report mode:*"
        return text

    def edit_message_text(self, chat_id: str, message_id: int, text: str, reply_markup=None, parse_mode=None):
        params = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        if parse_mode:
            params["parse_mode"] = parse_mode
        self._api("editMessageText", **params)

    def answer_callback(self, query_id: str, text: Optional[str] = None):
        params = {"callback_query_id": query_id}
        if text:
            params["text"] = text
            params["show_alert"] = True
        self._api("answerCallbackQuery", **params)

    # ---------- Keyboards ----------
    def main_menu_markup(self):
        return {
            "inline_keyboard": [
                [{"text": "Login", "callback_data": "login_prompt"}],
                [{"text": "Add Targets", "callback_data": "add_targets_prompt"}],
                [{"text": "List Targets", "callback_data": "list_targets"}],
                [{"text": "Status", "callback_data": "status"}],
                [{"text": "Start Report", "callback_data": "report_menu"}],
                [{"text": "Stop Job", "callback_data": "stop_job"}],
                [{"text": "Logout", "callback_data": "logout"}],
            ]
        }

    def back_markup(self):
        return {"inline_keyboard": [[{"text": "Back", "callback_data": "main_menu"}]]}

    def modes_markup(self):
        return {
            "inline_keyboard": [
                [{"text": "One per target", "callback_data": "mode_one"}],
                [{"text": "Many per target", "callback_data": "mode_many"}],
                [{"text": "Ordered sequence", "callback_data": "mode_ordered"}],
                [{"text": "Back", "callback_data": "main_menu"}]
            ]
        }

    def types_markup(self):
        rows = []
        row = []
        for num, name in REPORT_OPTIONS.items():
            row.append({"text": name, "callback_data": f"rt_{num}"})
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return {"inline_keyboard": rows}

    # ---------- Menu helpers ----------
    def send_main_menu(self, chat_id: str, message_id: Optional[int] = None):
        text = "*InstaRepPro Menu*\nSelect an action:"
        markup = self.main_menu_markup()
        if message_id:
            self.edit_message_text(chat_id, message_id, text, markup, "Markdown")
        else:
            self.send_message(chat_id, text, markup, "Markdown")

    def get_status_text(self, chat_id: str) -> str:
        sessionid, _, _, _ = get_session(chat_id)
        targets_count = len(get_targets(chat_id))
        job_running = chat_id in self.job_events
        return (
            f"*Status*\n"
            f"• Session: {'Active' if sessionid else 'None'}\n"
            f"• Targets: {targets_count}\n"
            f"• Job: {'Running' if job_running else 'Idle'}"
        )

    # ---------- Job control ----------
    def stop_job(self, chat_id: str):
        if chat_id in self.job_events:
            self.job_events[chat_id].set()
            self.send_message(chat_id, "Stop signal sent (will finish current report).")
            console.print(f"[yellow][{chat_id}] Stop requested[/yellow]")
        else:
            self.send_message(chat_id, "No job running.")

    def start_report_job(self, chat_id: str, mode: str, report_type: Optional[int], num: Optional[int], order: Optional[List[int]]):
        targets = get_targets(chat_id)
        if not targets:
            self.send_message(chat_id, "No targets – add some first.")
            return
            
        if not get_session(chat_id)[0]:
            self.send_message(chat_id, "No session – login first.")
            return

        # Show available report types before starting
        report_info = "*Selected Report Types:*\n"
        if mode in ["one", "many"]:
            report_info += f"• {REPORT_OPTIONS.get(report_type, 'Unknown')}\n"
        elif mode == "ordered":
            report_info += "\n".join(f"• {REPORT_OPTIONS.get(rt, 'Unknown')}" for rt in (order or []))
        
        # For multiple targets, ask for selection
        if len(targets) > 1:
            markup = {
                "inline_keyboard": [
                    [{"text": "All Targets", "callback_data": f"run_all_{mode}_{report_type}_{num}"}],
                    [{"text": "Select Specific", "callback_data": "select_targets"}]
                ]
            }
            self.user_data[chat_id] = {
                "mode": mode,
                "report_type": report_type,
                "num": num,
                "order": order
            }
            targets_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(targets))
            self.send_message(
                chat_id,
                f"{report_info}\n\n*Available Targets:*\n{targets_list}\n\nChoose targets to report:",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return

        # Single target - proceed directly
        event = threading.Event()
        self.job_events[chat_id] = event
        
        if mode == "one":
            t = threading.Thread(target=mode_one_per_target_per_type,
                            args=(self, chat_id, targets, report_type, 1, event))
        elif mode == "many":
            t = threading.Thread(target=mode_many_per_type,
                            args=(self, chat_id, targets, report_type, num, event))
        elif mode == "ordered":
            t = threading.Thread(target=mode_ordered_report,
                            args=(self, chat_id, targets, order, event))
        else:
            self.send_message(chat_id, "Invalid mode.")
            return

        t.start()
        self.send_message(
            chat_id, 
            f"{report_info}\n\nStarting {mode} mode report...\nUse *Stop* button or /stop to cancel.",
            parse_mode="Markdown"
        )
        console.print(f"[green][{chat_id}] {mode.capitalize()} job started[/green]")

    # ---------- Polling ----------
    def _get_updates(self) -> List[dict]:
        params = {"timeout": 20}
        if self.offset:
            params["offset"] = self.offset
        try:
            r = requests.get(f"{self.base}/getUpdates", params=params, timeout=30)
            j = r.json()
            if j.get("ok"):
                return j.get("result", [])
        except Exception as e:
            logger.error(f"getUpdates error: {e}")
        return []


    def _process_update(self, u: dict):
        """
        Robust update processor:
        - updates offset
        - cleanly separates callback_query vs message handling
        - initializes/normalizes `data` so "local variable 'data' not associated" cannot occur
        - fixes report menu/types handling and multi-target selection
        """
        try:
            self.offset = max(self.offset or 0, u.get("update_id", 0) + 1)

            # ---------- Callback Query (inline buttons) ----------
            if "callback_query" in u:
                query = u["callback_query"]
                qid = query.get("id")
                # normalize callback data
                data = query.get("data") or query.get("callback_data") or ""
                # safe guards / message references
                msg = query.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                message_id = msg.get("message_id")

                # answer the callback to remove "loading" state
                if qid:
                    # do not show alert by default
                    self.answer_callback(qid)

                # clear any pending simple input (we'll use explicit states below)
                # keep persistent user_data like 'selected_targets' as needed
                if chat_id in self.user_data:
                    # preserve selection state if present, otherwise clear states that expect textual replies
                    ud = self.user_data.get(chat_id, {})
                    keep = {}
                    if "selected_targets" in ud:
                        keep["selected_targets"] = ud["selected_targets"]
                    self.user_data[chat_id] = keep

                # Stop (generic)
                if data in ("stop", "stop_job"):
                    self.stop_job(chat_id)
                    if qid:
                        self.answer_callback(qid, "Stopping...")
                    if message_id is not None:
                        try:
                            self.edit_message_text(
                                chat_id,
                                message_id,
                                "Job stopped.\n" + self.get_status_text(chat_id),
                                self.main_menu_markup()
                            )
                        except Exception:
                            pass
                    return

                # Main menu
                if data == "main_menu":
                    self.send_main_menu(chat_id, message_id)
                    return

                # Show report menu (keep types visible)
                if data == "report_menu":
                    types_text = self.report_menu_text()
                    self.edit_message_text(chat_id, message_id, types_text, self.modes_markup(), "Markdown")
                    return

                # Mode selection
                if data.startswith("mode_"):
                    mode = data[5:]
                    if mode == "ordered":
                        types_text = self.report_menu_text()
                        # ask for ordered list; keep types visible in message text
                        self.user_data[chat_id] = {"mode": mode, "awaiting_order": True}
                        self.edit_message_text(chat_id, message_id, types_text + "\n\nSend comma-separated types (e.g. 4,1,8):", None, "Markdown")
                    else:
                        # store chosen mode and present types selection
                        self.user_data[chat_id] = {"mode": mode}
                        self.edit_message_text(chat_id, message_id, "*Choose report type:*", self.types_markup(), "Markdown")
                    return

                # Use stored login
                if data == "use_stored_login":
                    username, password = get_stored_credentials(chat_id)
                    if username and password:
                        self.send_message(chat_id, "Logging in with stored credentials...")
                        sess = login_with_requests(username, password)
                        if sess and sess[0]:
                            save_session(chat_id, *sess)
                            self.send_message(chat_id, f"*Login successful* ✅\nUser: `{username}`", parse_mode="Markdown")
                            self.send_main_menu(chat_id)
                        else:
                            self.send_message(chat_id, "Stored login failed.")
                    else:
                        self.send_message(chat_id, "No stored credentials found.")
                    if qid:
                        self.answer_callback(qid)
                    return

                # # Save credentials callback (legacy: expects "save_login_<user>_<pass>")
                # if data.startswith("save_login_"):
                #     parts = data.split("_", 2)
                #     if len(parts) == 3:
                #         username = parts[1]
                #         password = parts[2]
                #         chat_id = parts[3]
                #         ig_userid = parts[4] if len(parts) > 4 else ""
                #         ig_userid = ""
                #         if save_credentials(username, password, chat_id, ig_userid):
                #             self.send_message(chat_id, "Credentials are not stored anywhere. ❤")
                #         else:
                #             self.send_message(chat_id, "Failed to save credentials.")
                #     self.send_main_menu(chat_id)
                #     return
                # In SimpleTelegramBot class, replace the save_login_ handler with:

                # Save credentials callback (now with proper data handling)
                if data.startswith("save_login_"):
                    try:
                        parts = data.split("_", 2)  # split into ['save', 'login', 'rest']
                        if len(parts) >= 3:
                            username = parts[1]
                            password = parts[2]
                            # Get current session info for the user ID
                            sess_id, csrf, ua, ig_userid = get_session(chat_id)
                            
                            if save_credentials(username, password, chat_id, ig_userid or ""):
                                self.send_message(
                                    chat_id, 
                                    "✅ Credentials saved successfully!\n" + 
                                    f"Username: `{username}`\n" +
                                    f"User ID: `{ig_userid or 'N/A'}`",
                                    parse_mode="Markdown"
                                )
                            else:
                                self.send_message(chat_id, "❌ Failed to save credentials.")
                    except Exception as e:
                        logger.error(f"Save credentials error: {e}")
                        self.send_message(chat_id, "Failed to save credentials due to an error.")
                    
                    # Return to main menu
                    self.send_main_menu(chat_id)
                    return
                # Report type chosen (callback rt_<num>)
                if data.startswith("rt_"):
                    try:
                        rt = int(data[3:])
                    except Exception:
                        self.send_message(chat_id, "Invalid report type.")
                        return
                    ud = self.user_data.get(chat_id, {}) or {}
                    mode = ud.get("mode")
                    ud["report_type"] = rt
                    ud["awaiting_number"] = True if mode == "many" else True  # we ask number for 'many', for 'one' we still ask runs (keeps behavior)
                    self.user_data[chat_id] = ud
                    runs_label = "counts" if mode == "many" else "runs"
                    self.edit_message_text(chat_id, message_id, f"Send number of {runs_label} per target:", self.back_markup())
                    return

                # Run on all targets (run_all_<mode>_<rt>_<num>)
                if data.startswith("run_all_"):
                    try:
                        _, mode, rt_str, num_str = data.split("_")
                        rt = int(rt_str) if rt_str != "None" else None
                        num = int(num_str) if num_str != "None" else None
                    except Exception:
                        self.send_message(chat_id, "Invalid run_all parameters.")
                        return
                    # call start_report_job (it will ask for login/targets checks)
                    self.start_report_job(chat_id, mode, rt, num, None)
                    return

                # Start selection of specific targets
                if data == "select_targets":
                    targets = get_targets(chat_id)
                    if not targets:
                        self.send_message(chat_id, "No targets available.")
                        return
                    markup = {"inline_keyboard": []}
                    for i, t in enumerate(targets):
                        markup["inline_keyboard"].append([{"text": t, "callback_data": f"target_{i}"}])
                    markup["inline_keyboard"].append([{"text": "Start", "callback_data": "run_selected"}])
                    # initialize selected_targets list if not exists
                    ud = self.user_data.get(chat_id, {}) or {}
                    ud["selected_targets"] = ud.get("selected_targets", [])
                    self.user_data[chat_id] = ud
                    self.edit_message_text(chat_id, message_id, "*Select targets:*\nClick targets to toggle selection, then press Start", reply_markup=markup, parse_mode="Markdown")
                    return

                # Toggle a target selection "target_<index>"
                if data.startswith("target_"):
                    try:
                        idx = int(data.split("_", 1)[1])
                    except Exception:
                        return
                    ud = self.user_data.get(chat_id, {}) or {}
                    sel = ud.get("selected_targets", [])
                    # toggle
                    if idx in sel:
                        sel.remove(idx)
                    else:
                        sel.append(idx)
                    ud["selected_targets"] = sel
                    self.user_data[chat_id] = ud
                    # simple feedback
                    self.answer_callback(qid, f"Selected {len(sel)} target(s)")
                    return

                # Run selected targets
                if data == "run_selected":
                    ud = self.user_data.get(chat_id, {}) or {}
                    sel = ud.get("selected_targets", [])
                    targets_all = get_targets(chat_id)
                    if not sel:
                        self.send_message(chat_id, "No targets selected.")
                        return
                    selected_targets = [targets_all[i] for i in sel if 0 <= i < len(targets_all)]
                    mode = ud.get("mode")
                    rt = ud.get("report_type")
                    num = ud.get("num")
                    order = ud.get("order")
                    # start job inline (bypass start_report_job which uses get_targets)
                    event = threading.Event()
                    self.job_events[chat_id] = event
                    if mode == "one":
                        t = threading.Thread(target=mode_one_per_target_per_type, args=(self, chat_id, selected_targets, rt, 1, event))
                    elif mode == "many":
                        t = threading.Thread(target=mode_many_per_type, args=(self, chat_id, selected_targets, rt, num or 1, event))
                    elif mode == "ordered":
                        t = threading.Thread(target=mode_ordered_report, args=(self, chat_id, selected_targets, order or [], event))
                    else:
                        self.send_message(chat_id, "Invalid mode selected.")
                        return
                    t.start()
                    self.send_message(chat_id, "Started job on selected targets. Use Stop to cancel.")
                    return

                # Status/list/login/add targets/logout handlers
                if data == "status":
                    self.edit_message_text(chat_id, message_id, self.get_status_text(chat_id), self.back_markup(), "Markdown")
                    return
                if data == "list_targets":
                    tgts = get_targets(chat_id)
                    txt = "*Targets*:\n" + ("\n".join(f"{i+1}. `{t}`" for i, t in enumerate(tgts)) if tgts else "None")
                    self.edit_message_text(chat_id, message_id, txt, self.back_markup(), "Markdown")
                    return
                if data == "login_prompt":
                    self.edit_message_text(chat_id, message_id, "Send: `/login <username> <password>`", self.back_markup(), "Markdown")

                    return
                if data == "add_targets_prompt":
                    self.edit_message_text(chat_id, message_id, "Send: `/addtargets target1 target2 …`", self.back_markup(), "Markdown")
                    return
                if data == "logout":
                    conf = get_chat_conf(chat_id)
                    conf["session"] = {}
                    save_chat_conf(conf, chat_id)
                    self.edit_message_text(chat_id, message_id, "Logged out – session cleared.", self.back_markup())
                    return

                return  # end callback handling

            # ---------- Text message (including edited_message) ----------
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                return
            chat_id = str(msg["chat"]["id"])
            text = (msg.get("text") or "").strip()
            if not text:
                return

            # ensure chat exists
            get_chat_conf(chat_id)

            # Awaiting numeric input or ordered list
            if chat_id in self.user_data:
                state = self.user_data[chat_id]
                if state.get("awaiting_number"):
                    try:
                        num = int(text)
                        if num <= 0:
                            raise ValueError
                        rt = state.pop("report_type", None)
                        mode = state.pop("mode", None)
                        # clear state
                        self.user_data.pop(chat_id, None)
                        self.start_report_job(chat_id, mode, rt, num, None)
                    except Exception:
                        self.send_message(chat_id, "Invalid number – send a positive integer.")
                    return
                if state.get("awaiting_order"):
                    try:
                        order = [int(x.strip()) for x in text.split(",") if x.strip().isdigit()]
                        if not order:
                            raise ValueError
                        self.user_data.pop(chat_id, None)
                        self.start_report_job(chat_id, "ordered", None, None, order)
                    except Exception:
                        self.send_message(chat_id, "Invalid format – example: `4,1,8`")
                    return

            # ---------- Commands ----------
            if not text.startswith("/"):
                self.send_message(chat_id, "Unknown command. Send /start for the menu.")
                return

            parts = text.split()
            cmd = parts[0][1:].lower()
            args = parts[1:]

            if cmd in ("start", "help"):
                self.send_main_menu(chat_id)
                return

            if cmd == "login":
                def loign_procedure():
                    if len(args) < 2:
                        stored_user, stored_pass = get_stored_credentials(chat_id)
                        if stored_user and stored_pass:
                            markup = {"inline_keyboard": [[
                                {"text": "Use Stored Login", "callback_data": "use_stored_login"},
                                {"text": "New Login", "callback_data": "login_prompt"}
                            ]]}
                            self.send_message(chat_id, f"Found stored login for: `{stored_user}`\nChoose an option:", reply_markup=markup, parse_mode="Markdown")
                            return
                        self.send_message(chat_id, "Usage: /login <username> <password>")
                        return

                    username = args[0]
                    password = " ".join(args[1:])
                    self.send_message(chat_id, "Logging in…")
                    sess = login_with_requests(username, password)
                    # In the login procedure, update the success message:

                    if sess and sess[0]:
                        save_session(chat_id, *sess)
                        markup = {"inline_keyboard": [[
                            {"text": "Yes", "callback_data": f"save_login_{username}_{password}"},
                            {"text": "No", "callback_data": "main_menu"}
                        ]]}
                        self.send_message(
                            chat_id,
                            f"*Login successful* ✅\n" +
                            f"User: `{username}`\n" +
                            f"ID: `{sess[3]}`\n\n" +
                            "Would you like to save these credentials for next time?",
                            reply_markup=markup,
                            parse_mode="Markdown"
                        )
                        console.print(f"[green][{chat_id}] Login: {username}[/green]")
                    else:
                        self.send_message(chat_id, "Login failed.")
                        self.send_main_menu(chat_id)
                    return
                loign_procedure()
                return

            if cmd == "addtargets":
                if not args:
                    self.send_message(chat_id, "Usage: /addtargets <target1> <target2> …")
                    return
                added = add_targets(chat_id, args)
                total = len(get_targets(chat_id))
                self.send_message(chat_id, f"Added {added} new target(s). Total: {total}")
                self.send_main_menu(chat_id)
                return

            if cmd == "listtargets":
                tgts = get_targets(chat_id)
                txt = "*Targets*:\n" + ("\n".join(f"{i+1}. `{t}`" for i, t in enumerate(tgts)) if tgts else "None")
                self.send_message(chat_id, txt, "Markdown")
                return

            if cmd == "status":
                self.send_message(chat_id, self.get_status_text(chat_id), "Markdown")
                return

            if cmd == "stop":
                self.stop_job(chat_id)
                return

            if cmd == "logout":
                conf = get_chat_conf(chat_id)
                conf["session"] = {}
                save_chat_conf(conf, chat_id)
                self.send_message(chat_id, "Logged out.")
                return

            self.send_message(chat_id, "Unknown command. Send /start.")

        except Exception as e:
            logger.exception("Update processing error: %s", e)
            return

    # ---------- Main loop ----------
    def start(self):
        """Start the bot polling loop and show main menu."""
        console.print("[bold green]Telegram bot polling started[/bold green]")
        logger.info("Bot started")
        
        self.running = True
        
        while self.running:
            try:
                updates = self._get_updates()
                for u in updates:
                    # Extract chat_id from update if possible
                    chat_id = None
                    if "message" in u:
                        chat_id = str(u["message"]["chat"]["id"])
                    elif "callback_query" in u:
                        chat_id = str(u["callback_query"]["message"]["chat"]["id"])
                    
                    # Send main menu to new chats
                    # if chat_id:
                    #     try:
                    #         self.send_main_menu(chat_id)
                    #     except Exception as e:
                    #         logger.error(f"Failed to send menu: {e}")
                    
                    # Process the update
                    try:
                        self._process_update(u)
                    except Exception as e:
                        logger.error(f"Update processing error: {e}")
                        
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(3)  # Prevent tight loop on errors
# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    bot_token = os.getenv("BOT_TOKEN")      # <<< SET THIS IN YOUR ENVIRONMENT
    if not bot_token:
        console.print("[red]Error: export BOT_TOKEN=your:token before running[/red]")
        sys.exit(1)
    # Start the webserver before the bot
    bot = SimpleTelegramBot(bot_token)
    try:
        bot.start()
    except KeyboardInterrupt:
        console.print("[yellow]Bot stopped (Ctrl+C)[/yellow]")
        logger.info("Shutdown")