# -*- coding: utf-8 -*-
"""微信原生接入模块 —— 通过 WeChat Bot API 直接收发消息。"""
import json
import os
import threading
import time
import urllib.request
import urllib.error
import uuid
import random as _random
import base64 as _b64

FIXED_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
WECHAT_DIR = None


def init(data_dir):
    global WECHAT_DIR
    WECHAT_DIR = os.path.join(data_dir, "微信配置")
    os.makedirs(WECHAT_DIR, exist_ok=True)


def _account_file():
    return os.path.join(WECHAT_DIR, "account.json")


def _load_account():
    p = _account_file()
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_account(data):
    with open(_account_file(), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
#  登录
# ============================================================
_qr_session = {"qrcode": "", "started_at": 0}


def login_start():
    try:
        body = json.dumps({"local_token_list": []}).encode('utf-8')
        req = urllib.request.Request(
            f"{FIXED_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}",
            data=body, method='POST'
        )
        req.add_header('Content-Type', 'application/json')
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode('utf-8'))
        _qr_session["qrcode"] = data.get("qrcode", "")
        _qr_session["started_at"] = time.time()
        return {
            "ok": True,
            "qrcode_url": data.get("qrcode_img_content", ""),
            "qrcode": data.get("qrcode", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def login_poll():
    qrcode = _qr_session.get("qrcode", "")
    if not qrcode:
        return {"status": "error", "error": "无进行中的登录"}
    if time.time() - _qr_session.get("started_at", 0) > 480:
        return {"status": "expired", "error": "登录超时"}
    try:
        url = f"{FIXED_BASE_URL}/ilink/bot/get_qrcode_status?qrcode={qrcode}"
        req = urllib.request.Request(url, method='GET')
        resp = urllib.request.urlopen(req, timeout=35)
        data = json.loads(resp.read().decode('utf-8'))
        status = data.get("status", "wait")
        if status == "confirmed":
            acct = {
                "bot_token": data.get("bot_token", ""),
                "account_id": data.get("ilink_bot_id", ""),
                "base_url": data.get("baseurl", ""),
                "user_id": data.get("ilink_user_id", ""),
            }
            _save_account(acct)
            return {"status": "confirmed", **acct}
        return {"status": status}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def login_cancel():
    _qr_session["qrcode"] = ""
    _qr_session["started_at"] = 0


# ============================================================
#  绑定管理
# ============================================================
def get_account():
    return _load_account()


def unbind():
    stop()
    p = _account_file()
    if os.path.exists(p):
        os.remove(p)
    s = _state_file()
    if os.path.exists(s):
        os.remove(s)
    return True


# ============================================================
#  消息收发
# ============================================================
_agent_running = False
_agent_thread = None
_agent_state = {"last_inbound": None, "last_outbound": None, "last_error": None}


def _post_api(base_url, endpoint, body_dict, token, timeout=15):
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/{endpoint}",
        data=json.dumps(body_dict).encode('utf-8'),
        method='POST'
    )
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('AuthorizationType', 'ilink_bot_token')
    req.add_header('iLink-App-Id', 'bot')
    req.add_header('iLink-App-ClientVersion', '132099')
    uin = str(_random.randint(1000000000, 9999999999))
    req.add_header('X-WECHAT-UIN', _b64.b64encode(uin.encode()).decode())
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode('utf-8')
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        print(f"[wechat] HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        raise


def send_message(to_user, text, account=None, context_token=""):
    if account is None:
        account = _load_account()
    if not account.get("bot_token"):
        return False
    try:
        try:
            cfg_resp = _post_api(account["base_url"], "ilink/bot/getconfig", {
                "ilink_user_id": to_user,
                "context_token": context_token,
                "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
            }, account["bot_token"])
            typing_ticket = cfg_resp.get("typing_ticket", "")
            if typing_ticket:
                _post_api(account["base_url"], "ilink/bot/sendtyping", {
                    "ilink_user_id": to_user,
                    "typing_ticket": typing_ticket,
                    "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
                }, account["bot_token"])
        except Exception:
            pass

        msg_body = {
            "from_user_id": "",
            "to_user_id": to_user,
            "client_id": f"openclaw-weixin-{uuid.uuid4().hex[:32]}",
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        if context_token:
            msg_body["context_token"] = context_token
        body = {
            "msg": msg_body,
            "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
        }
        _post_api(account["base_url"], "ilink/bot/sendmessage", body, account["bot_token"])
        _agent_state["last_outbound"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return True
    except Exception as e:
        _agent_state["last_error"] = str(e)[:200]
        return False


def _state_file():
    return os.path.join(WECHAT_DIR, "agent_state.json")


def save_running_state(running):
    with open(_state_file(), 'w', encoding='utf-8') as f:
        json.dump({"was_running": running}, f)


def load_running_state():
    p = _state_file()
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f).get("was_running", False)
    return False


def get_updates(account=None):
    if account is None:
        account = _load_account()
    if not account.get("bot_token"):
        return []
    try:
        data = _post_api(account["base_url"], "ilink/bot/getupdates", {
            "get_updates_buf": "",
            "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
        }, account["bot_token"], timeout=10)
        return data.get("msgs", [])
    except Exception:
        return []


def _agent_loop(inbound_callback):
    global _agent_running
    account = _load_account()
    if not account.get("bot_token"):
        return
    try:
        _post_api(account["base_url"], "ilink/bot/msg/notifystart", {
            "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
        }, account["bot_token"])
    except Exception:
        pass
    while _agent_running:
        try:
            msgs = get_updates(account)
            if msgs:
                _agent_state["last_inbound"] = time.strftime("%Y-%m-%d %H:%M:%S")
                for msg in msgs:
                    inbound_callback(msg, account)
        except Exception as e:
            _agent_state["last_error"] = str(e)[:200]
            time.sleep(5)
    try:
        _post_api(account["base_url"], "ilink/bot/msg/notifystop", {
            "base_info": {"channel_version": "2.4.3", "bot_agent": "OpenClaw"},
        }, account["bot_token"])
    except Exception:
        pass


def start(inbound_callback):
    global _agent_running, _agent_thread
    if _agent_running:
        return False
    account = _load_account()
    if not account.get("bot_token"):
        return False
    _agent_running = True
    _agent_state["last_error"] = None
    _agent_thread = threading.Thread(target=_agent_loop, args=(inbound_callback,), daemon=True)
    _agent_thread.start()
    save_running_state(True)
    return True


def stop():
    global _agent_running, _agent_thread
    _agent_running = False
    _agent_thread = None
    save_running_state(False)


def get_state():
    return {
        "running": _agent_running,
        "bound": bool(_load_account().get("bot_token")),
        "account": _load_account(),
        "last_inbound": _agent_state["last_inbound"],
        "last_outbound": _agent_state["last_outbound"],
        "last_error": _agent_state["last_error"],
    }
