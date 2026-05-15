import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# PyInstaller --noconsole 下 stdout 可能为 None 或受限，print 含 emoji 会崩
import builtins as _bi
_real_print = _bi.print
def _safe_print(*args, **kwargs):
    try:
        _real_print(*args, **kwargs)
    except Exception:
        try:
            _real_print(*(str(a).encode('ascii', errors='replace').decode('ascii') for a in args), **kwargs)
        except Exception:
            pass
import builtins
builtins.print = _safe_print

import http.server
import socketserver
import json
import os
import urllib.parse
import urllib.request
import urllib.error
import re
import threading
import time
import mimetypes
import webbrowser
import base64
import random
from datetime import datetime
try:
    import wechat_agent
    WECHAT_AVAILABLE = True
except ImportError:
    WECHAT_AVAILABLE = False
    print("⚠️ wechat_agent 模块未安装，微信功能将不可用")

# PyInstaller 打包后资源路径处理
if getattr(sys, 'frozen', False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_pywebview_window = None

def _app_path(rel_path):
    """优先用外部文件（支持热更新），不存在则用打包内资源。"""
    ext = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _BASE_DIR, rel_path)
    if os.path.exists(ext):
        return ext
    bundled = os.path.join(_BASE_DIR, rel_path)
    return bundled

PORT = 5622
APP_VERSION = "1.1.0"
# 打包后 Program Files 无写权限，数据存用户目录
if getattr(sys, 'frozen', False):
    DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'LangAgent')
else:
    DATA_DIR = "Data"

if WECHAT_AVAILABLE:
    wechat_agent.init(DATA_DIR)
MEM_DIR = os.path.join(DATA_DIR, "记忆核心")
CONFIG_DIR = os.path.join(DATA_DIR, "模型配置")
AGENT_AVATAR_DIR = os.path.join(DATA_DIR, "人物头像")
USER_AVATAR_DIR = os.path.join(DATA_DIR, "用户头像")
AGENT_PROFILE_DIR = os.path.join(DATA_DIR, "人物档案") 
USER_PROFILE_DIR = os.path.join(DATA_DIR, "用户档案")
INNER_THOUGHTS_DIR = os.path.join(DATA_DIR, "人物内心")

ALLOWED_FOLDERS = ["记忆核心", "模型配置", "人物档案", "用户档案", "人物内心"]

for d in [MEM_DIR, CONFIG_DIR, AGENT_AVATAR_DIR, USER_AVATAR_DIR, AGENT_PROFILE_DIR, USER_PROFILE_DIR, INNER_THOUGHTS_DIR]:
    os.makedirs(d, exist_ok=True)

file_lock = threading.RLock()
global_state_lock = threading.Lock() 

api_cooldown_until = 0
consecutive_failures = 0
last_interaction_time = time.time()
next_proactive_delay = random.uniform(120, 240) * 60
def update_interaction_time(cfg):
    global last_interaction_time, next_proactive_delay
    with global_state_lock:
        last_interaction_time = time.time()
        next_proactive_delay = random.uniform(int(cfg.get("proactive_min", 120)), int(cfg.get("proactive_max", 240))) * 60

def safe_json_read(filepath, default_val):
    if not os.path.exists(filepath):
        return default_val
    with file_lock:
        content = ""
        try:
            with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
        except UnicodeDecodeError:
            try:
                with open(filepath, 'r', encoding='gbk') as f: content = f.read()
            except: pass
        except Exception: pass

        if not content.strip(): return default_val

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            corrupted_path = f"{filepath}.corrupted_{int(time.time())}"
            os.rename(filepath, corrupted_path)
            print(f"⚠️ [系统降级] 检测到损坏数据，已隔离并重新初始化: {filepath}")
            return default_val

def atomic_json_write(filepath, data):
    with file_lock:
        tmp_path = filepath + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(tmp_path):
            os.replace(tmp_path, filepath)

with file_lock:
    if not os.path.exists(os.path.join(MEM_DIR, "chat_history.json")):
        atomic_json_write(os.path.join(MEM_DIR, "chat_history.json"), [])
    if not os.path.exists(os.path.join(MEM_DIR, "memory_summary.json")):
        atomic_json_write(os.path.join(MEM_DIR, "memory_summary.json"), {"items": []})

def get_decay_score(item):
    imp = max(1, min(10, float(item.get('importance', 5))))
    try:
        dt = datetime.strptime(item.get('time', ''), "%Y-%m-%d %H:%M:%S")
        hours_elapsed = (datetime.now() - dt).total_seconds() / 3600.0
    except:
        hours_elapsed = 0
    # 指数衰减（艾宾浩斯遗忘曲线）：重要度越高，半衰期越长
    # imp=1  half=24h   imp=5  half=96h   imp=10 half≈543h(23天)
    half_life = 24.0 * (2.0 ** ((imp - 1.0) / 2.0))
    return round(imp * (2.0 ** (-hours_elapsed / half_life)), 2)

CONTENT_REJECT_KW = ['content_filter', '内容安全', '违规', 'safety system',
                     'content policy', 'content management', '无法处理该请求']

_MODEL_ERR = '__MODEL_ERROR__'

def _is_rejected(reply_text):
    if not reply_text:
        return False
    return any(kw in reply_text.lower() for kw in CONTENT_REJECT_KW)

def _strip_think(text):
    """去除 <｜end▁of▁thinking｜><｜end▁of▁thinking｜><｜end▁of▁thinking｜> 思考内容，返回纯正文。聊天记录只存正文。"""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

def _split_think(text):
    """分离思考内容和正文，返回 (think_parts, main_text)。"""
    thinks = []
    main = text
    for m in re.finditer(r'<think>(.*?)</think>', text, re.DOTALL):
        thinks.append(m.group(1).strip())
    main = _strip_think(text)
    return thinks, main

_conn_status = "connecting"

def build_chat_context(cfg):
    """构建聊天上下文（人设 + 档案 + 记忆 + 历史），返回 (system_prompt, history, history_file)。"""
    ai_name = cfg.get("ai_name", "AI")
    user_name = cfg.get("user_name", "用户")
    system_prompt = f"你是{ai_name}。请严格遵循人设与我聊天，永远不要承认自己是AI程序，保持自然的情感。\n\n【基础档案】\n"

    with file_lock:
        profile_path = os.path.join(AGENT_PROFILE_DIR, "人物档案.txt")
        if os.path.exists(profile_path):
            with open(profile_path, 'r', encoding='utf-8') as f:
                system_prompt += f.read() + "\n"

        user_profile_path = os.path.join(USER_PROFILE_DIR, "用户档案.txt")
        if os.path.exists(user_profile_path):
            with open(user_profile_path, 'r', encoding='utf-8') as f:
                u_info = f.read().strip()
                if u_info:
                    system_prompt += f"\n【{user_name}（我）的档案】\n{u_info}\n"

        summary_file = os.path.join(MEM_DIR, "memory_summary.json")
        mem_summary = safe_json_read(summary_file, {})
        if mem_summary.get('items'):
            system_prompt += "\n【长期记忆日记】\n"
            for m in mem_summary['items']:
                system_prompt += f"- [{m['time']}] {m['content']}\n"

        history_file = os.path.join(MEM_DIR, "chat_history.json")
        chat_history = safe_json_read(history_file, [])

    return system_prompt, chat_history, history_file


def build_llm_messages(system_prompt, chat_history, user_msg):
    """用上下文和用户消息构建 LLM 消息列表。"""
    recent = chat_history[-21:]
    formatted = []
    for m in recent:
        role = "assistant" if m["role"] == "agent" else m["role"]
        formatted.append({"role": role, "content": m["content"]})
    if formatted and formatted[0]["role"] == "assistant":
        formatted.insert(0, {"role": "user", "content": "（继续之前的对话）"})

    llm_msgs = []
    if formatted:
        formatted[0]["content"] = system_prompt + "\n\n" + formatted[0]["content"]
        llm_msgs.extend(formatted)
        llm_msgs.append({"role": "user", "content": user_msg})
    else:
        if isinstance(user_msg, str):
            user_msg = system_prompt + "\n\n" + user_msg
        else:
            user_msg[0]["text"] = system_prompt + "\n\n" + user_msg[0]["text"]
        llm_msgs.append({"role": "user", "content": user_msg})

    return llm_msgs

def get_conn_status():
    return _conn_status

def _set_conn_status(s):
    global _conn_status
    if _conn_status != s:
        _conn_status = s
        print(f"[{time.strftime('%H:%M:%S')}] 🔌 [状态] {s}")

def call_llm_with_circuit_breaker(cfg, messages, use_fallback=True):
    global api_cooldown_until, consecutive_failures

    with global_state_lock: cooldown_time = api_cooldown_until

    if time.time() < cooldown_time:
        return _MODEL_ERR if use_fallback else None

    timeout = int(cfg.get('model_timeout', 120))
    payload = {"model": cfg['model'], "messages": messages, "stream": False}
    req = urllib.request.Request(cfg['url'], data=json.dumps(payload).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    if cfg['key'].strip(): req.add_header('Authorization', f"Bearer {cfg['key']}")

    for attempt in range(2):
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            if resp.getcode() != 200: raise Exception("HTTP Error")
            resp_data = json.loads(resp.read().decode('utf-8'))
            # 检测 API 返回的错误（如模型名错误、余额不足等）
            api_err = resp_data.get('error')
            if api_err:
                err_msg = api_err.get('message', '') if isinstance(api_err, dict) else str(api_err)
                if not err_msg:
                    err_msg = str(api_err)[:200]
                raise Exception(f"API Error: {err_msg}")
            reply = resp_data.get('choices', [{}])[0].get('message', {}).get('content', '') or resp_data.get('response', '') or resp_data.get('message', {}).get('content', '')
            reply = re.sub(r'<think>.*?(?:</think>|$)', '', reply, flags=re.DOTALL).strip()

            with global_state_lock: consecutive_failures = 0
            _set_conn_status("online")
            return reply
        except Exception as ex:
            time.sleep(1)
            
    with global_state_lock:
        consecutive_failures += 1
        if consecutive_failures >= 3:
            api_cooldown_until = time.time() + 60

    _set_conn_status("offline")
    return _MODEL_ERR if use_fallback else None

def call_llm_stream(cfg, messages):
    """流式调用 LLM，逐 token yield 文本增量。连接失败时 yield None。"""
    global api_cooldown_until, consecutive_failures

    with global_state_lock: cooldown_time = api_cooldown_until
    if time.time() < cooldown_time:
        yield None; return

    timeout = int(cfg.get('model_timeout', 120))
    payload = {"model": cfg['model'], "messages": messages, "stream": True}
    req = urllib.request.Request(cfg['url'], data=json.dumps(payload).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    if cfg['key'].strip(): req.add_header('Authorization', f"Bearer {cfg['key']}")

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        in_think = False
        for raw_line in resp:
            line = raw_line.decode('utf-8', errors='replace').strip()
            if not line.startswith('data: '): continue
            data = line[6:]
            if data == '[DONE]': break
            try:
                chunk = json.loads(data)
                delta = chunk.get('choices', [{}])[0].get('delta', {}).get('content') or ''
                if not delta: continue
                remaining = delta
                while remaining:
                    if not in_think:
                        idx = remaining.find('<think>')
                        if idx == -1: yield remaining; break
                        if idx > 0: yield remaining[:idx]
                        remaining = remaining[idx + 7:]; in_think = True
                    else:
                        idx = remaining.find('</think>')
                        if idx == -1: break
                        remaining = remaining[idx + 8:]; in_think = False
            except: pass
        with global_state_lock: consecutive_failures = 0
        _set_conn_status("online")
    except Exception:
        with global_state_lock:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                api_cooldown_until = time.time() + 60
        _set_conn_status("offline")
        yield None

def auto_summarize_memory(cfg, recent_history):
    try:
        inner_thoughts_path = os.path.join(INNER_THOUGHTS_DIR, "人物内心.txt")
        current_inner_thoughts = ""
        with file_lock:
            if os.path.exists(inner_thoughts_path):
                with open(inner_thoughts_path, 'r', encoding='utf-8') as f:
                    current_inner_thoughts = f.read()

        sys_prompt = f"""你是{cfg.get("ai_name", "AI")}。请阅读以下你和{cfg.get("user_name", "用户")}的近期对话。

任务1：用第一人称（我）写一段简短私密日记，总结这段对话中发生的关键事件或约定。
任务2：从对话中提取关于他（{cfg.get("user_name", "用户")}）的【新情报】，只记录事实性信息（饮食喜好、习惯、近期状态等），不写叙事，不推测。如果某项情报在【已有情报】中已存在，不需要重复记录。
任务3（记忆强化）：如果对话内容在重复/延续之前已记录过的话题，且你十分确定是同一件事，请设置 "reinforce": "旧记忆的关键特征短语（10字以内）"。若不确定，不要设此字段，让系统作为新情报处理（不匹配的重复记忆会自然衰减消失）。

⚠️ 绝对不要把你自己（{cfg.get("ai_name", "AI")}）的特征、习惯、行为写进 new_user_profile。

【已有情报】：
{current_inner_thoughts[-800:]}

你必须且只能返回纯JSON数据，不要包含```json标记。格式要求如下：
{{"content": "今天他跟我说...", "importance": 5, "reinforce": "需要匹配的关键词（可选字段）", "new_user_profile": "饮食喜好：xxx\\n习惯：xxx\\n近期状态：xxx"}}
【重要度说明】：1=寒暄闲聊转眼就忘, 3=一般日常几天后模糊, 5=值得记住的互动, 7=重要约定或情绪波动, 10=影响一生的关键事件"""
        
        chat_text = "\n".join([f"{cfg.get('ai_name', 'AI') if msg['role'] == 'agent' else cfg.get('user_name', '用户')}: {msg['content']}" for msg in recent_history])
        
        reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": sys_prompt + "\n\n[对话记录]：\n" + chat_text}], use_fallback=False)
        if not reply: return

        start_idx = reply.find('{')
        end_idx = reply.rfind('}')
        if start_idx != -1 and end_idx != -1:
            try:
                json_str = reply[start_idx:end_idx+1]
                try:
                    new_mem = json.loads(json_str)
                except json.JSONDecodeError:
                    new_mem = json.loads(json_str.replace('\n', '\\n'))
                
                summary_file = os.path.join(MEM_DIR, "memory_summary.json")
                with file_lock:
                    mem_data = safe_json_read(summary_file, {"items": []})
                    reinforced = False
                    reinforce_kw = str(new_mem.get("reinforce", "")).strip()
                    if len(reinforce_kw) >= 3 and reinforce_kw.lower() not in ("无", "none", "null", ""):
                        items = mem_data.get("items", [])
                        for old in items:
                            old_content = old.get("content", "")
                            if reinforce_kw in old_content:
                                old["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                                old["importance"] = min(10, int(old.get("importance", 5)) + 1)
                                reinforced = True
                                print(f"[{time.strftime('%H:%M:%S')}] 🧠 [记忆强化] 刷新 +1: {reinforce_kw[:30]}")
                                break
                    if not reinforced and new_mem.get("content"):
                        new_mem['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                        mem_data['items'].append(new_mem)
                    atomic_json_write(summary_file, mem_data)

                new_facts = str(new_mem.get("new_user_profile", "")).strip()
                if new_facts and new_facts.lower() not in ["无", "none", "null", ""]:
                    entry = f"\n\n【{time.strftime('%Y-%m-%d %H:%M')}】\n{new_facts}"
                    with file_lock:
                        with open(inner_thoughts_path, 'a', encoding='utf-8') as f: f.write(entry)
                
                print(f"[{time.strftime('%H:%M:%S')}] 🧠 [记忆引擎] 时间衰减机制刷新完毕，画像提取成功！")
            except Exception: pass
    except Exception: pass

def proactive_worker():
    while True:
        time.sleep(10)
        try:
            cfg_path = os.path.join(CONFIG_DIR, "config.json")
            cfg = safe_json_read(cfg_path, {})
            if not cfg.get("proactive_enabled", False): continue
            
            now = datetime.now()
            curr_str = now.strftime("%H:%M")
            start_str = cfg.get("proactive_start", "00:00")
            end_str = cfg.get("proactive_end", "23:59")
            if start_str <= end_str:
                if not (start_str <= curr_str <= end_str): continue
            else:
                # 跨午夜窗口 如 23:00-08:00：在 start 之后 或 end 之前 即在窗口内
                if not (curr_str >= start_str or curr_str <= end_str): continue
                
            with global_state_lock:
                passed_time = time.time() - last_interaction_time
                target_delay = next_proactive_delay
                
            if passed_time > target_delay:
                ai_name = cfg.get("ai_name", "AI")
                user_name = cfg.get("user_name", "用户")
                # 读取最近对话，生成上下文感知的主动消息
                history_file = os.path.join(MEM_DIR, "chat_history.json")
                with file_lock:
                    history = safe_json_read(history_file, [])
                recent = history[-6:]
                context_str = ""
                if recent:
                    lines = []
                    for m in recent:
                        role = ai_name if m['role'] == 'agent' else user_name
                        lines.append(f"{role}: {m['content']}")
                    context_str = "\n".join(lines)
                # 读取人物档案以保持角色个性
                profile_text = ""
                try:
                    profile_path = os.path.join(AGENT_PROFILE_DIR, "人物档案.txt")
                    if os.path.exists(profile_path):
                        with open(profile_path, 'r', encoding='utf-8') as f:
                            profile_text = f.read()[:600]
                except Exception:
                    pass
                prompt = f"你是{ai_name}。\n【人设】{profile_text}\n\n以下是你们最近的对话：\n\n{context_str}\n\n{user_name}已经有一段时间没回复了。请根据人设和上述对话上下文，主动发一条自然延续话题的消息。如果对方之前说了要去做什么，可以关心进度；如果之前的话题被打断，可以尝试接续。不超过20个字，不要用任何解释和前缀，直接给出对话内容。"
                
                snap_time = last_interaction_time
                reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": prompt}], use_fallback=False)
                if reply:
                    with global_state_lock:
                        if last_interaction_time != snap_time:
                            print(f"[{time.strftime('%H:%M:%S')}] 💌 [主动关怀] 用户抢先发言，丢弃")
                            continue
                    parts = [p.strip() for p in re.split(r'(?<=[。！？!\?\n])', reply) if p.strip()]
                    if not parts: parts = [reply]
                    with file_lock:
                        history = safe_json_read(history_file, [])
                        for p in parts: history.append({"role": "agent", "content": p, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        atomic_json_write(history_file, history)
                    update_interaction_time(cfg)
                    print(f"[{time.strftime('%H:%M:%S')}] 💌 [主动关怀] 消息已推入时间流")
                    # 微信在线则同步推送
                    if WECHAT_AVAILABLE and wechat_agent.get_state()["running"]:
                        try:
                            acct = wechat_agent.get_account()
                            to_user = acct.get("user_id", "")
                            if to_user:
                                wechat_agent.send_message(to_user, ''.join(parts))
                                print(f"[{time.strftime('%H:%M:%S')}] 💌 [主动关怀] 已同步推送至微信")
                        except Exception:
                            pass
        except Exception: pass

threading.Thread(target=proactive_worker, daemon=True).start()

def memory_decay_cleaner():
    """后台线程：每30分钟对所有长期记忆做一次衰减评分，剔除已归零的记忆。"""
    while True:
        time.sleep(1800)
        try:
            summary_file = os.path.join(MEM_DIR, "memory_summary.json")
            with file_lock:
                mem_data = safe_json_read(summary_file, {"items": []})
                items = mem_data.get('items', [])
                if not items:
                    continue
                before = len(items)
                items = [m for m in items if get_decay_score(m) >= 0.3]
                if len(items) < before:
                    mem_data['items'] = items
                    atomic_json_write(summary_file, mem_data)
                    print(f"[{time.strftime('%H:%M:%S')}] 🧹 [记忆清理] 衰减剔除 {before - len(items)} 条，剩余 {len(items)} 条")
        except Exception:
            pass

threading.Thread(target=memory_decay_cleaner, daemon=True).start()

def handle_wechat_message(msg, account):
    try:
        item_list = msg.get("msg", {}).get("item_list", msg.get("item_list", []))
        user_text = ""
        for item in item_list:
            if item.get("type") == 1:
                ti = item.get("text_item", {})
                user_text += ti.get("text", ti.get("content", ""))
        if not user_text.strip():
            return
        from_user = msg.get("msg", {}).get("from_user_id", msg.get("from_user_id", ""))
        if not from_user:
            return
        context_token = msg.get("msg", {}).get("context_token", msg.get("context_token", ""))
        print(f"[{time.strftime('%H:%M:%S')}] 📥 [微信] {from_user[:20]}...: {user_text[:50]}")

        def _process_and_reply():
            try:
                cfg = safe_json_read(os.path.join(CONFIG_DIR, "config.json"), {})
                system_prompt, chat_history, history_file = build_chat_context(cfg)
                llm_messages = build_llm_messages(system_prompt, chat_history, user_text)

                ai_reply = call_llm_with_circuit_breaker(cfg, llm_messages, use_fallback=True)
                if not ai_reply or ai_reply.strip() == _MODEL_ERR:
                    with file_lock:
                        safe_chat = safe_json_read(history_file, [])
                        safe_chat.append({"role": "user", "content": user_text, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        atomic_json_write(history_file, safe_chat)
                    update_interaction_time(cfg)
                    wechat_agent.send_message(from_user, "（脑子卡了一下，刚才没听清，你再说一遍？）", account, context_token)
                    print(f"[{time.strftime('%H:%M:%S')}] ⚠️ [微信] 模型不可用，已发送降级回复")
                    return

                parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])', ai_reply) if p.strip()]
                if not parts: parts = [ai_reply]
                clean_reply = ''.join(parts)

                start_summary = False
                to_summarize = []
                with file_lock:
                    safe_chat = safe_json_read(history_file, [])
                    safe_chat.append({"role": "user", "content": user_text, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                    for p in parts:
                        safe_chat.append({"role": "agent", "content": _strip_think(p), "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                    if len(safe_chat) >= 22:
                        to_summarize = safe_chat[:20]
                        safe_chat = safe_chat[20:]
                        start_summary = True
                    atomic_json_write(history_file, safe_chat)

                update_interaction_time(cfg)

                if start_summary:
                    threading.Thread(target=auto_summarize_memory, args=(cfg, to_summarize)).start()

                if clean_reply.strip():
                    wechat_agent.send_message(from_user, clean_reply, account, context_token)
                print(f"[{time.strftime('%H:%M:%S')}] 💬 [微信] 回复: {clean_reply[:40]}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ [微信处理] 异常: {str(e)[:150]}")

        threading.Thread(target=_process_and_reply, daemon=True).start()
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ [微信] 异常: {str(e)[:150]}")
class AgentHandler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-type, Authorization")
        self.end_headers()

    def get_config(self):
        config_path = os.path.join(CONFIG_DIR, "config.json")
        cfg = {"url": "http://localhost:11434/v1/chat/completions", "key": "", "model": "", "hide_think": True, "ai_name": "", "user_name": "", "stream_enabled": False}
        cfg.update(safe_json_read(config_path, {}))
        return cfg

    def do_GET(self):
        if self.path == "/":
            self.send_response(200); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
            with open(_app_path("index.html"), "r", encoding="utf-8") as f: self.wfile.write(f.read().encode("utf-8"))
            return

        elif self.path == "/api/version":
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({"version": APP_VERSION}).encode('utf-8'))
            return

        elif self.path == "/api/status":
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({"status": get_conn_status()}).encode('utf-8'))
            return

        elif self.path == "/api/show":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, 'LangAgent')
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            self.send_response(200); self.end_headers()
            return

        elif self.path.startswith("/api/poll"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            client_count = int(qs.get('count', ['0'])[0])
            history_file = os.path.join(MEM_DIR, "chat_history.json")
            with file_lock: history = safe_json_read(history_file, [])
            new_msgs = history[client_count:] if len(history) > client_count else []
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({"new_messages": new_msgs}).encode('utf-8'))
            return

        elif self.path.startswith("/api/avatar/"):
            parsed = urllib.parse.urlparse(self.path)
            role = parsed.path.split("/")[-1]
            target_dir = AGENT_AVATAR_DIR if role == "agent" else USER_AVATAR_DIR
            avatar_path = None
            for filename in os.listdir(target_dir):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                    avatar_path = os.path.join(target_dir, filename)
                    break
            
            if avatar_path and os.path.exists(avatar_path):
                with open(avatar_path, 'rb') as f: content = f.read()
                mime_type, _ = mimetypes.guess_type(avatar_path)
                self.send_response(200); self.send_header('Content-type', mime_type or 'image/png'); self.end_headers()
                self.wfile.write(content)
            else: self.send_response(404); self.end_headers()

        elif self.path == "/api/signature":
            try:
                if get_conn_status() != "online":
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": ""}).encode('utf-8'))
                    return
                cfg = self.get_config()
                if not cfg.get("ai_name"): self.send_response(400); self.end_headers(); return

                sig_file = os.path.join(MEM_DIR, "daily_signature.json")
                today_str = time.strftime("%Y-%m-%d")

                with file_lock: sig_data = safe_json_read(sig_file, {})
                        
                if sig_data.get("date") == today_str and sig_data.get("signature"):
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": sig_data["signature"]}).encode('utf-8'))
                    return

                sys_prompt = f"你是{cfg['ai_name']}。请写一句【15字以内】的【社交软件个性签名】。要求：口语化、第一人称、展现你今天的心情或状态。直接返回签名文本，不要任何解释。"
                ai_reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": sys_prompt}], use_fallback=False)

                if ai_reply:
                    ai_reply = ai_reply.strip(' "”\'“\n')
                    atomic_json_write(sig_file, {"date": today_str, "signature": ai_reply})
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": ai_reply}).encode('utf-8'))
                else:
                    # LLM 不可用，复用旧签名（如有）
                    old = sig_data.get("signature", "")
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": old}).encode('utf-8'))
            except Exception:
                self.send_response(500); self.end_headers()

        elif self.path.startswith("/api/read/"):
            clean_path = self.path.split('?')[0]
            parts = clean_path.split("/")
            folder = urllib.parse.unquote(parts[-2])
            filename = os.path.basename(urllib.parse.unquote(parts[-1]))
            if folder not in ALLOWED_FOLDERS: self.send_response(403); self.end_headers(); return
            if not filename.lower().endswith(('.txt', '.json')):
                self.send_response(403); self.end_headers(); return
            file_path = os.path.join(DATA_DIR, folder, filename)
            with file_lock:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f: content = f.read()
                    self.send_response(200); self.send_header('Content-type', 'text/plain; charset=utf-8'); self.end_headers()
                    self.wfile.write(content.encode('utf-8'))
                else: self.send_response(404); self.end_headers()
        else: self.send_response(404); self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 15 * 1024 * 1024: self.send_response(413); self.end_headers(); return

        # ===== 微信原生接入端点 =====
        if self.path.startswith("/api/wechat/") and not WECHAT_AVAILABLE:
            self.send_response(503); self.end_headers()
            self.wfile.write(json.dumps({"error": "微信模块未安装"}).encode('utf-8'))
            return

        if WECHAT_AVAILABLE and self.path == "/api/wechat/status":
            try:
                state = wechat_agent.get_state()
                # 脱敏：不返回 bot_token 完整值
                if state.get("account", {}).get("bot_token"):
                    state["account"]["bot_token"] = state["account"]["bot_token"][:12] + "***"
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(state).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if WECHAT_AVAILABLE and self.path == "/api/wechat/login_start":
            try:
                result = wechat_agent.login_start()
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if WECHAT_AVAILABLE and self.path == "/api/wechat/login_poll":
            try:
                result = wechat_agent.login_poll()
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return


        if WECHAT_AVAILABLE and self.path == "/api/wechat/login_cancel":
            try:
                wechat_agent.login_cancel()
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if WECHAT_AVAILABLE and self.path == "/api/wechat/unbind":
            try:
                wechat_agent.stop()
                wechat_agent.unbind()
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if WECHAT_AVAILABLE and self.path == "/api/wechat/toggle":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                enable = post_data.get('enable', False)
                if enable:
                    ok = wechat_agent.start(handle_wechat_message)
                else:
                    ok = wechat_agent.stop() or True
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"ok": ok, "running": wechat_agent.get_state()["running"]}).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return
        # ===== 微信端点结束 =====

        if self.path == "/api/save":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                folder = post_data.get('folder')
                filename = os.path.basename(post_data.get('filename'))

                if folder not in ALLOWED_FOLDERS: self.send_response(403); self.end_headers(); return

                target_path = os.path.join(DATA_DIR, folder, filename)
                with file_lock:
                    if filename == 'config.json' and folder == '模型配置':
                        existing = safe_json_read(target_path, {})
                        try:
                            incoming = json.loads(post_data.get('content', '{}'))
                        except Exception:
                            incoming = {}
                        existing.update(incoming)
                        atomic_json_write(target_path, existing)
                    else:
                        with open(target_path, 'w', encoding='utf-8') as f: f.write(post_data.get('content'))
                self.send_response(200); self.end_headers(); self.wfile.write(b"Success")
            except Exception as e: self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))

        elif self.path == "/api/upload_avatar":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                role = post_data.get('role')
                img_b64 = post_data.get('image')
                if role not in ['agent', 'user'] or not img_b64: self.send_response(400); self.end_headers(); return
                if len(img_b64) > 5 * 1024 * 1024:
                    self.send_response(413); self.end_headers()
                    self.wfile.write(json.dumps({"error": "图片过大，请使用5MB以内的图片"}).encode('utf-8'))
                    return
                if ',' in img_b64: img_b64 = img_b64.split(',')[1]
                img_data = base64.b64decode(img_b64)
                
                target_dir = AGENT_AVATAR_DIR if role == "agent" else USER_AVATAR_DIR
                with file_lock:
                    for filename in os.listdir(target_dir):
                        file_path = os.path.join(target_dir, filename)
                        if os.path.isfile(file_path): os.remove(file_path)
                    with open(os.path.join(target_dir, "avatar.png"), "wb") as f: f.write(img_data)
                self.send_response(200); self.end_headers(); self.wfile.write(b"Success")
            except Exception as e: self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))
                
        elif self.path == "/api/reset":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                token = post_data.get('token', '')
                if token != 'LangAgent-Reset-Confirm':
                    self.send_response(403); self.end_headers()
                    self.wfile.write(json.dumps({"error": "需要确认令牌"}).encode('utf-8'))
                    return
                with file_lock:
                    cfg_path = os.path.join(CONFIG_DIR, "config.json")
                    if os.path.exists(cfg_path): os.remove(cfg_path)
                    
                    for p in [os.path.join(AGENT_PROFILE_DIR, "人物档案.txt"), os.path.join(USER_PROFILE_DIR, "用户档案.txt"), os.path.join(INNER_THOUGHTS_DIR, "人物内心.txt")]:
                        if os.path.exists(p): os.remove(p)
                    
                    atomic_json_write(os.path.join(MEM_DIR, "chat_history.json"), [])
                    atomic_json_write(os.path.join(MEM_DIR, "memory_summary.json"), {"items": []})
                    sig_file = os.path.join(MEM_DIR, "daily_signature.json")
                    if os.path.exists(sig_file): os.remove(sig_file)

                    for d in [AGENT_AVATAR_DIR, USER_AVATAR_DIR]:
                        for filename in os.listdir(d):
                            file_path = os.path.join(d, filename)
                            if os.path.isfile(file_path): os.remove(file_path)
                self.send_response(200); self.end_headers(); self.wfile.write(b"Reset Success")
            except Exception as e: self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))
                
        elif self.path == "/api/get_models":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                url = post_data.get('url', '')
                key = post_data.get('key', '')
                model_names = []
                status = "ok"
                # 归一化基础 URL：去尾部常见后缀
                base = url.rstrip('/')
                for suffix in ['/chat/completions', '/models', '/v1/models']:
                    if base.endswith(suffix):
                        base = base[:-len(suffix)]
                        break
                # 依次尝试不同模型列表端点
                endpoints = ['/models', '/v1/models', '/api/tags']
                for ep in endpoints:
                    if model_names:
                        break
                    try:
                        models_url = base.rstrip('/') + ep
                        req = urllib.request.Request(models_url, method='GET')
                        if key.strip(): req.add_header('Authorization', f'Bearer {key}')
                        resp = urllib.request.urlopen(req, timeout=5)
                        data = json.loads(resp.read().decode('utf-8'))
                        if 'models' in data:
                            model_names = [m.get('name', m.get('id', '')) for m in data.get('models', [])]
                        else:
                            model_names = [m.get('id', m.get('name', '')) for m in data.get('data', [])]
                        model_names = [n for n in model_names if n]
                    except Exception:
                        pass
                # 都没拿到则用已填写的模型名作为兜底
                if not model_names:
                    manual = post_data.get('model', '').strip()
                    if manual:
                        model_names = [manual]
                        status = "fallback"
                if not model_names:
                    model_names = ["（未能探测，请手动输入模型名）"]
                    status = "fallback"
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"models": model_names, "status": status}).encode('utf-8'))
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

        elif self.path == "/api/chat":
            try:
                cfg = self.get_config()
                update_interaction_time(cfg)

                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                user_message = post_data.get('message')
                image_data = post_data.get('image')

                system_prompt, chat_history, history_file = build_chat_context(cfg)
                msg_content = [{"type": "text", "text": user_message or "看看这张图片"}, {"type": "image_url", "image_url": {"url": image_data}}] if image_data else user_message
                llm_messages = build_llm_messages(system_prompt, chat_history, msg_content)

                stream_mode = post_data.get('stream') and cfg.get('stream_enabled', False)

                if stream_mode:
                    self.send_response(200)
                    self.send_header('Content-type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()

                    parts = []
                    buf = ""
                    try:
                        for token in call_llm_stream(cfg, llm_messages):
                            if token is None: break
                            buf += token
                            while True:
                                m = re.search(r'[。！？!?\n]', buf)
                                if not m: break
                                end = m.end()
                                sentence = buf[:end].strip()
                                buf = buf[end:]
                                if sentence:
                                    parts.append(sentence)
                                    self.wfile.write(f"data: {json.dumps({'type': 'sentence', 'text': sentence}, ensure_ascii=False)}\n\n".encode())
                                    self.wfile.flush()
                        if buf.strip():
                            parts.append(buf.strip())
                            self.wfile.write(f"data: {json.dumps({'type': 'sentence', 'text': buf.strip()}, ensure_ascii=False)}\n\n".encode())
                            buf = ""
                    except Exception:
                        pass

                    rejected = False
                    if buf.strip():
                        parts.append(buf.strip())
                        self.wfile.write(f"data: {json.dumps({'type': 'sentence', 'text': buf.strip()}, ensure_ascii=False)}\n\n".encode())
                    if not parts:
                        self.wfile.write(f"data: {json.dumps({'type': 'error', 'message': '请求失败，请检查模型配置是否正确'}, ensure_ascii=False)}\n\n".encode())
                        self.wfile.write(b"data: {\"type\":\"done\"}\n\n")
                        self.wfile.flush()
                        return
                    full_text = ''.join(parts)
                    if _is_rejected(full_text):
                        self.wfile.write(f"data: {json.dumps({'type': 'error', 'message': '请求失败，请检查模型配置是否正确'}, ensure_ascii=False)}\n\n".encode())
                        rejected = True
                    self.wfile.write(b"data: {\"type\":\"done\"}\n\n")
                    self.wfile.flush()

                    if rejected:
                        return

                    with file_lock:
                        safe_chat_history = safe_json_read(history_file, [])
                        safe_chat_history.append({"role": "user", "content": user_message or "[发送了一张图片]", "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        for p in parts:
                            safe_chat_history.append({"role": "agent", "content": p, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        if len(safe_chat_history) >= 22:
                            to_summarize = safe_chat_history[:20]
                            safe_chat_history = safe_chat_history[20:]
                            atomic_json_write(history_file, safe_chat_history)
                            threading.Thread(target=auto_summarize_memory, args=(cfg, to_summarize)).start()
                        else:
                            atomic_json_write(history_file, safe_chat_history)
                    update_interaction_time(cfg)
                    return

                ai_reply = call_llm_with_circuit_breaker(cfg, llm_messages, use_fallback=True)

                _model_failed = ai_reply.strip() == _MODEL_ERR
                if _is_rejected(ai_reply) or _model_failed:
                    msg = "模型请求失败，请检查模型配置是否正确" if _model_failed else "消息被云端安全策略拦截，请勿发送违规内容"
                    self.send_response(400); self.send_header("Content-type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({
                        "error": "content_rejected",
                        "message": msg
                    }, ensure_ascii=False).encode("utf-8"))
                    return

                parts = [p.strip() for p in re.split(r'(?<=[。！？!\?\n])', ai_reply) if p.strip()]
                if not parts: parts = [ai_reply if ai_reply else _MODEL_ERR]

                start_summary_thread = False
                to_summarize = []
                with file_lock:
                    safe_chat_history = safe_json_read(history_file, [])
                    safe_chat_history.append({"role": "user", "content": user_message or "[发送了一张图片]", "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                    for p in parts:
                        safe_chat_history.append({"role": "agent", "content": p, "time": time.strftime("%Y-%m-%d %H:%M:%S")})

                    if len(safe_chat_history) >= 22:
                        to_summarize = safe_chat_history[:20]
                        safe_chat_history = safe_chat_history[20:]
                        start_summary_thread = True
                        
                    atomic_json_write(history_file, safe_chat_history)

                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"reply_parts": parts}).encode('utf-8'))

                if start_summary_thread:
                    threading.Thread(target=auto_summarize_memory, args=(cfg, to_summarize)).start()

            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    # 单实例锁：防止重复启动
    import socket
    _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_sock.bind(('127.0.0.1', PORT + 1))
    except OSError:
        # 已有实例在运行，通知它恢复窗口
        restored = False
        try:
            urllib.request.urlopen(f'http://localhost:{PORT}/api/show', timeout=2)
            restored = True
        except Exception:
            pass
        if not restored:
            try:
                webbrowser.open(f'http://localhost:{PORT}')
            except Exception:
                pass
        sys.exit(0)

    print(f"============================================================")
    print(f"🚀 LangAgent 已启动")
    print(f"🌐 正在监听端口 {PORT}")
    if WECHAT_AVAILABLE:
        print(f"🔗 微信原生接入模块已就绪")
        if wechat_agent.load_running_state():
            if wechat_agent.get_account().get("bot_token"):
                wechat_agent.start(handle_wechat_message)
                print(f"[系统] 微信消息服务已自动恢复运行")
    else:
        print(f"⚠️ 微信模块不可用，使用纯 Web 模式")
    print(f"============================================================\n")
    if getattr(sys, 'frozen', False):
        threading.Thread(target=lambda: ThreadingServer(('localhost', PORT), AgentHandler).serve_forever(), daemon=True).start()
        try:
            import webview
            import ctypes
            from PIL import Image

            _close_mode = {'mode': 'minimize'}
            _tray_ref = [None]

            class _Api:
                def setCloseMode(self, val):
                    _close_mode['mode'] = val

                def openQrWindow(self, url):
                    # 居中显示
                    sw = ctypes.windll.user32.GetSystemMetrics(0)
                    sh = ctypes.windll.user32.GetSystemMetrics(1)
                    w, h = 420, 520
                    x, y = (sw - w) // 2, (sh - h) // 2
                    qr_win = webview.create_window('微信扫码绑定', url, width=w, height=h,
                                                    x=x, y=y, resizable=False, on_top=True)
                    _api._qr_win = qr_win

                def closeQrWindow(self):
                    try:
                        if hasattr(_api, '_qr_win') and _api._qr_win:
                            _api._qr_win.destroy()
                            _api._qr_win = None
                    except Exception:
                        pass

            _api = _Api()

            def _show_window():
                global _pywebview_window
                try:
                    if _pywebview_window:
                        _pywebview_window.show()
                        hwnd = ctypes.windll.user32.FindWindowW(None, 'LangAgent')
                        if hwnd:
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            def _setup_tray():
                import pystray
                icon_img = Image.open(_app_path('app_icon.ico'))
                menu = pystray.Menu(
                    pystray.MenuItem('显示窗口', lambda: _show_window(), default=True),
                    pystray.MenuItem('退出', lambda: _do_exit()),
                )
                _tray_ref[0] = pystray.Icon('LangAgent', icon_img, 'LangAgent', menu)
                threading.Thread(target=_tray_ref[0].run, daemon=True).start()

            def _do_exit():
                try:
                    if _tray_ref[0]:
                        _tray_ref[0].stop()
                except Exception:
                    pass
                os._exit(0)

            # 托盘图标（一直显示）
            _setup_tray()

            # 窗口状态记忆
            _win_state_file = os.path.join(DATA_DIR, '_window_state.json')
            _win_saved = {}
            if os.path.exists(_win_state_file):
                try:
                    with open(_win_state_file, 'r', encoding='utf-8') as f:
                        _win_saved = json.load(f)
                except Exception:
                    pass

            window = webview.create_window('LangAgent', f'http://localhost:{PORT}',
                                           js_api=_api, width=1100, height=750,
                                           min_size=(800, 600),
                                           maximized=_win_saved.get('maximized', True))
            _pywebview_window = window

            def _save_window_state():
                try:
                    hwnd = ctypes.windll.user32.FindWindowW(None, 'LangAgent')
                    if hwnd:
                        import ctypes.wintypes
                        placement = ctypes.create_string_buffer(44)
                        ctypes.windll.user32.GetWindowPlacement(hwnd, placement)
                        show_cmd = int.from_bytes(placement[8:12], 'little')
                        state = {'maximized': show_cmd == 3}
                        with open(_win_state_file, 'w', encoding='utf-8') as f:
                            json.dump(state, f)
                except Exception:
                    pass

            def _on_closing():
                _save_window_state()
                if _close_mode['mode'] == 'silent':
                    if _tray_ref[0]:
                        _tray_ref[0].stop()
                    return True
                _pywebview_window.hide()
                return False

            window.events.closing += _on_closing
            webview.start()
        except Exception as _e:
            try:
                with open(os.path.join(DATA_DIR, 'startup_error.log'), 'a', encoding='utf-8') as _f:
                    import traceback
                    _f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] pywebview init failed:\n{traceback.format_exc()}\n\n')
            except Exception:
                pass
            def _open_browser():
                time.sleep(1)
                webbrowser.open(f'http://localhost:{PORT}')
            threading.Thread(target=_open_browser, daemon=True).start()
            ThreadingServer(('localhost', PORT), AgentHandler).serve_forever()
    else:
        ThreadingServer(('localhost', PORT), AgentHandler).serve_forever()