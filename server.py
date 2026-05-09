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
import base64
import random
from datetime import datetime

PORT = 5622
DATA_DIR = "Data"
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
model_status_cache = {"is_online": False, "last_check": 0}

def update_interaction_time(cfg):
    global last_interaction_time, next_proactive_delay
    with global_state_lock:
        last_interaction_time = time.time()
        next_proactive_delay = random.uniform(int(cfg.get("proactive_min", 120)), int(cfg.get("proactive_max", 240))) * 60

def safe_json_read(filepath, default_val):
    if not os.path.exists(filepath):
        return default_val
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

def call_llm_with_circuit_breaker(cfg, messages, use_fallback=True):
    global api_cooldown_until, consecutive_failures
    
    with global_state_lock: cooldown_time = api_cooldown_until
        
    if time.time() < cooldown_time:
        return "（网络卡卡的，能再说一遍嘛？）" if use_fallback else None
    
    payload = {"model": cfg['model'], "messages": messages, "stream": False}
    req = urllib.request.Request(cfg['url'], data=json.dumps(payload).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    if cfg['key'].strip(): req.add_header('Authorization', f"Bearer {cfg['key']}")
    
    for attempt in range(2):
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            if resp.getcode() != 200: raise Exception("HTTP Error")
            resp_data = json.loads(resp.read().decode('utf-8'))
            reply = resp_data.get('choices', [{}])[0].get('message', {}).get('content', '') or resp_data.get('response', '') or resp_data.get('message', {}).get('content', '')
            if cfg['hide_think']: reply = re.sub(r'<think>.*?(?:</think>|$)', '', reply, flags=re.DOTALL).strip()
            
            with global_state_lock: consecutive_failures = 0
            return reply
        except Exception as ex:
            time.sleep(1)
            
    with global_state_lock:
        consecutive_failures += 1
        if consecutive_failures >= 3:
            api_cooldown_until = time.time() + 60
            
    return "（网络卡卡的，能再说一遍嘛？）" if use_fallback else None

def auto_summarize_memory(cfg, recent_history):
    try:
        inner_thoughts_path = os.path.join(INNER_THOUGHTS_DIR, "人物内心.txt")
        current_inner_thoughts = ""
        with file_lock:
            if os.path.exists(inner_thoughts_path):
                with open(inner_thoughts_path, 'r', encoding='utf-8') as f:
                    current_inner_thoughts = f.read()

        sys_prompt = f"""你是{cfg.get("ai_name", "AI")}。请阅读以下你和他（{cfg.get("user_name", "用户")}）的近期对话。
任务1：用写『内心私密日记』的口吻，总结这段对话中发生的关键事件或约定，用第一人称（我）。
任务2：寻找对话中关于他的【新情报】（例如：家庭、目标、喜好、习惯等）。结合下面提供的【当前已有情报】，进行忽略、覆盖或新增，为他整理出一份最新、最完整的规范化档案。

【当前已有情报】：
{current_inner_thoughts}

你必须且只能返回纯JSON数据，不要包含```json标记。格式要求如下：
{{"content": "今天他跟我说...", "importance": 5, "new_user_profile": "籍贯：xxx\\n身高：xxx\\n饮食喜好：xxx\\n近期状态：xxx"}}
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
                    if new_mem.get("content"):
                        new_mem['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                        mem_data['items'].append(new_mem)
                        atomic_json_write(summary_file, mem_data)

                new_facts = str(new_mem.get("new_user_profile", "")).strip()
                if new_facts and new_facts.lower() not in ["无", "none", "null", ""]:
                    formatted_inner = f"【{cfg.get('ai_name', 'AI')}】在 {time.strftime('%Y-%m-%d %H:%M:%S')} 更新的信息\n\n{new_facts}"
                    with file_lock:
                        tmp_path = inner_thoughts_path + ".tmp"
                        with open(tmp_path, 'w', encoding='utf-8') as f: f.write(formatted_inner)
                        if os.path.exists(tmp_path): os.replace(tmp_path, inner_thoughts_path)
                
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
                prompt = f"你是{ai_name}。{user_name}已经有一段时间没说话了，请根据你的人设和内心档案，主动发一条不超过15个字的日常关心或分享。不要用任何解释和前缀，直接给出对话内容。"
                
                reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": prompt}], use_fallback=False)
                if reply:
                    parts = [p.strip() for p in re.split(r'(?<=[。！？!\?\n])', reply) if p.strip()]
                    if not parts: parts = [reply]
                    history_file = os.path.join(MEM_DIR, "chat_history.json")
                    with file_lock:
                        history = safe_json_read(history_file, [])
                        for p in parts: history.append({"role": "agent", "content": p, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        atomic_json_write(history_file, history)
                    update_interaction_time(cfg)
                    print(f"[{time.strftime('%H:%M:%S')}] 💌 [主动关怀] 消息已推入时间流")
        except Exception: pass

threading.Thread(target=proactive_worker, daemon=True).start()

def model_health_checker():
    """后台线程：定期探测大模型 /models 端点，更新缓存。心跳接口只读不查，永不阻塞。"""
    first_run = True
    while True:
        time.sleep(3 if first_run else 30)
        first_run = False
        try:
            cfg_path = os.path.join(CONFIG_DIR, "config.json")
            cfg = safe_json_read(cfg_path, {})
            url = cfg.get('url', '')
            if not url:
                with global_state_lock:
                    model_status_cache["is_online"] = False
                    model_status_cache["last_check"] = time.time()
                continue

            check_url = url.replace('/chat/completions', '/models') if '/chat/completions' in url else url.rstrip('/') + '/models'
            req = urllib.request.Request(check_url, method='GET')
            if cfg.get('key', '').strip():
                req.add_header('Authorization', f"Bearer {cfg['key']}")
            response = urllib.request.urlopen(req, timeout=5)
            with global_state_lock:
                model_status_cache["is_online"] = (response.getcode() == 200)
                model_status_cache["last_check"] = time.time()
        except Exception:
            with global_state_lock:
                model_status_cache["is_online"] = False
                model_status_cache["last_check"] = time.time()

threading.Thread(target=model_health_checker, daemon=True).start()

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

class AgentHandler(http.server.BaseHTTPRequestHandler):

    # ===== 🦞 龙虾(OpenClaw)专属区域 开始 🦞 =====
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-type, Authorization")
        self.end_headers()

    def _send_openai_response(self, text, is_stream, model_name):
        if is_stream:
            self.send_response(200)
            self.send_header('Content-type', 'text/event-stream; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            
            chunk = {
                "id": "chatcmpl-" + str(int(time.time())),
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name or "agent-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]
            }
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode('utf-8'))
            end_chunk = chunk.copy()
            end_chunk["choices"][0]["delta"] = {}
            end_chunk["choices"][0]["finish_reason"] = "stop"
            self.wfile.write(f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n".encode('utf-8'))
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            openai_response = {
                "id": "chatcmpl-" + str(int(time.time())),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name or "agent-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(openai_response, ensure_ascii=False).encode('utf-8'))
    # ===== 🦞 龙虾(OpenClaw)专属区域 结束 🦞 =====

    def get_config(self):
        config_path = os.path.join(CONFIG_DIR, "config.json")
        cfg = {"url": "http://localhost:11434/v1/chat/completions", "key": "", "model": "", "hide_think": True, "ai_name": "", "user_name": "", "lobster_enabled": False}
        cfg.update(safe_json_read(config_path, {}))
        return cfg

    def do_GET(self):
        # ===== 🦞 龙虾(OpenClaw)专属区域 开始 🦞 =====
        if self.path in ["/v1/models", "/models"]:
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            mock_models = {"object": "list", "data": [{"id": "agent-model", "object": "model", "created": int(time.time()), "owned_by": "custom"}]}
            self.wfile.write(json.dumps(mock_models).encode('utf-8'))
            return
        # ===== 🦞 龙虾(OpenClaw)专属区域 结束 🦞 =====

        if self.path == "/":
            self.send_response(200); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
            with open("index.html", "r", encoding="utf-8") as f: self.wfile.write(f.read().encode("utf-8"))
            return

        elif self.path.startswith("/api/list/"):
            with global_state_lock:
                is_online = model_status_cache.get("is_online", False)
            if is_online:
                self.send_response(200); self.end_headers()
            else:
                self.send_response(503); self.end_headers()
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
                else: self.send_response(500); self.end_headers()
            except Exception: self.send_response(500); self.end_headers()

        elif self.path.startswith("/api/read/"):
            parts = self.path.split("/")
            folder = urllib.parse.unquote(parts[-2])
            filename = os.path.basename(urllib.parse.unquote(parts[-1])) 
            if folder not in ALLOWED_FOLDERS: self.send_response(403); self.end_headers(); return
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

        # ===== 🦞 龙虾(OpenClaw)专属区域 开始 🦞 =====
        if self.path in ["/v1/chat/completions", "/chat/completions"]:
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                messages = post_data.get('messages', [])
                is_stream = post_data.get('stream', False)
                if not messages: self.send_response(400); self.end_headers(); return
                
                cfg = self.get_config()
                ai_name = cfg.get("ai_name", "AI")
                user_name = cfg.get("user_name", "用户")

                if not cfg.get("lobster_enabled", False):
                    print(f"[{time.strftime('%H:%M:%S')}] 拦截: 微信/OpenClaw消息被挂起 (接管开关已关闭)")
                    blocked_reply = "【系统提示】主人正在专心忙碌，接管模式暂时关闭哦~"
                    self._send_openai_response(blocked_reply, is_stream, cfg.get('model', ''))
                    return
                
                user_message = ""
                image_data = None
                noise_signatures = ["<conversation>", "HEARTBEAT", "openclaw", "workspace", "read_file", "Sender (untrusted metadata)", "TOOL_CALL", "system prompt", "Update the existing structured summary"]
                
                for msg in reversed(messages):
                    if msg.get('role') == 'user':
                        raw_content = msg.get('content', '')
                        temp_text = ""
                        if isinstance(raw_content, list):
                            for item in raw_content:
                                if item.get('type') == 'text': temp_text += item.get('text', '')
                                elif item.get('type') == 'image_url': image_data = item.get('image_url', {}).get('url', '')
                        else: temp_text = str(raw_content)
                        
                        temp_text = temp_text.strip()
                        is_noise = any(noise in temp_text for noise in noise_signatures)
                        
                        if temp_text and not is_noise:
                            clean_text = re.sub(r'^\[.*?\d{4}-\d{2}-\d{2}.*?\d{2}:\d{2}.*?\]\s*', '', temp_text)
                            user_message = clean_text
                            break 

                if not user_message and not image_data:
                    self._send_openai_response("HEARTBEAT_OK", is_stream, cfg.get('model', ''))
                    return

                print(f"\n[{time.strftime('%H:%M:%S')}] 📥 [接收消息] {user_name}: {user_message}")

                system_prompt = f"你是{ai_name}。请严格遵循人设与我聊天，永远不要承认自己是AI程序，保持自然的情感。\n\n【基础档案】\n"
                with file_lock:
                    profile_path = os.path.join(AGENT_PROFILE_DIR, "人物档案.txt")
                    if os.path.exists(profile_path):
                        with open(profile_path, 'r', encoding='utf-8') as f: system_prompt += f.read() + "\n"
                    user_profile_path = os.path.join(USER_PROFILE_DIR, "用户档案.txt")
                    if os.path.exists(user_profile_path):
                        with open(user_profile_path, 'r', encoding='utf-8') as f: 
                            u_info = f.read().strip()
                            if u_info: system_prompt += f"\n【{user_name}（我）的档案】\n{u_info}\n"
                    
                    summary_file = os.path.join(MEM_DIR, "memory_summary.json")
                    mem_summary = safe_json_read(summary_file, {})
                    if mem_summary.get('items'):
                        system_prompt += "\n【长期记忆日记】(请在对话中自然参考以下过去发生的事情)：\n"
                        for m in mem_summary['items']: system_prompt += f"- [{m['time']}] {m['content']}\n"

                    history_file = os.path.join(MEM_DIR, "chat_history.json")
                    chat_history = safe_json_read(history_file, [])
                
                msg_content = [{"type": "text", "text": user_message or "看看这张图片"}, {"type": "image_url", "image_url": {"url": image_data}}] if image_data else user_message
                
                recent_history = chat_history[-21:] 
                formatted_history = []
                for msg in recent_history:
                    api_role = "assistant" if msg["role"] == "agent" else msg["role"]
                    formatted_history.append({"role": api_role, "content": msg["content"]})
                
                if formatted_history and formatted_history[0]["role"] == "assistant":
                    formatted_history.insert(0, {"role": "user", "content": "（继续之前的对话）"})
                
                llm_messages = []
                if formatted_history:
                    formatted_history[0]["content"] = system_prompt + "\n\n" + formatted_history[0]["content"]
                    llm_messages.extend(formatted_history)
                    llm_messages.append({"role": "user", "content": msg_content})
                else:
                    if isinstance(msg_content, str): msg_content = system_prompt + "\n\n" + msg_content
                    else: msg_content[0]["text"] = system_prompt + "\n\n" + msg_content[0]["text"]
                    llm_messages.append({"role": "user", "content": msg_content})

                ai_reply = call_llm_with_circuit_breaker(cfg, llm_messages, use_fallback=True)
                if not ai_reply: ai_reply = "（网络卡卡的，能再说一遍嘛？）"

                parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])', ai_reply) if p.strip()]
                if not parts: parts = [ai_reply]

                print(f"[{time.strftime('%H:%M:%S')}] 📤 [{ai_name}回复] {ai_reply}\n")

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

                clean_reply = ''.join(parts)
                self._send_openai_response(clean_reply, is_stream, cfg.get('model', ''))

                if start_summary_thread:
                    threading.Thread(target=auto_summarize_memory, args=(cfg, to_summarize)).start()
                update_interaction_time(cfg)

            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                error_response = {"error": {"message": str(e), "type": "server_error"}}
                self.wfile.write(json.dumps(error_response).encode('utf-8'))
            return
        # ===== 🦞 龙虾(OpenClaw)专属区域 结束 🦞 =====
            
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
                url = post_data.get('url', '').replace('/chat/completions', '/models') if '/chat/completions' in post_data.get('url', '') else post_data.get('url', '').rstrip('/') + '/models'
                req = urllib.request.Request(url, method='GET')
                if post_data.get('key', '').strip(): req.add_header('Authorization', f'Bearer {post_data.get("key")}')
                response = urllib.request.urlopen(req, timeout=8)
                model_names = [m['id'] for m in json.loads(response.read().decode('utf-8')).get('data', [])]
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({"models": model_names, "status": "ok"}).encode('utf-8'))
            except Exception as e: self.send_response(500); self.end_headers(); self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

        elif self.path == "/api/chat":
            try:
                cfg = self.get_config()
                update_interaction_time(cfg)
                
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                user_message = post_data.get('message')
                image_data = post_data.get('image')

                ai_name = cfg.get("ai_name", "AI")
                user_name = cfg.get("user_name", "用户")
                system_prompt = f"你是{ai_name}。请严格遵循人设与我聊天，永远不要承认自己是AI程序，保持自然的情感。\n\n【基础档案】\n"
                
                with file_lock:
                    profile_path = os.path.join(AGENT_PROFILE_DIR, "人物档案.txt")
                    if os.path.exists(profile_path):
                        with open(profile_path, 'r', encoding='utf-8') as f: system_prompt += f.read() + "\n"
                    user_profile_path = os.path.join(USER_PROFILE_DIR, "用户档案.txt")
                    if os.path.exists(user_profile_path):
                        with open(user_profile_path, 'r', encoding='utf-8') as f: 
                            u_info = f.read().strip()
                            if u_info: system_prompt += f"\n【{user_name}（我）的档案】\n{u_info}\n"
                    
                    summary_file = os.path.join(MEM_DIR, "memory_summary.json")
                    mem_summary = safe_json_read(summary_file, {})
                    if mem_summary.get('items'):
                        system_prompt += "\n【长期记忆日记】(请在对话中自然参考以下过去发生的事情)：\n"
                        for m in mem_summary['items']: system_prompt += f"- [{m['time']}] {m['content']}\n"

                    history_file = os.path.join(MEM_DIR, "chat_history.json")
                    chat_history = safe_json_read(history_file, [])
                
                msg_content = [{"type": "text", "text": user_message or "看看这张图片"}, {"type": "image_url", "image_url": {"url": image_data}}] if image_data else user_message
                recent_history = chat_history[-21:] 
                formatted_history = []
                for msg in recent_history:
                    api_role = "assistant" if msg["role"] == "agent" else msg["role"]
                    formatted_history.append({"role": api_role, "content": msg["content"]})
                
                if formatted_history and formatted_history[0]["role"] == "assistant":
                    formatted_history.insert(0, {"role": "user", "content": "（继续之前的对话）"})
                
                llm_messages = []
                if formatted_history:
                    formatted_history[0]["content"] = system_prompt + "\n\n" + formatted_history[0]["content"]
                    llm_messages.extend(formatted_history)
                    llm_messages.append({"role": "user", "content": msg_content})
                else:
                    if isinstance(msg_content, str): msg_content = system_prompt + "\n\n" + msg_content
                    else: msg_content[0]["text"] = system_prompt + "\n\n" + msg_content[0]["text"]
                    llm_messages.append({"role": "user", "content": msg_content})

                ai_reply = call_llm_with_circuit_breaker(cfg, llm_messages, use_fallback=True)
                
                parts = [p.strip() for p in re.split(r'(?<=[。！？!\?\n])', ai_reply) if p.strip()]
                if not parts: parts = [ai_reply if ai_reply else "（无言以对）"]

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
    print(f"============================================================")
    print(f"🚀 安全级 Agent 驱动中枢已启动 [时间衰减+心跳架构+防脏数据]")
    print(f"🌐 正在监听端口 {PORT}")
    print(f"🔗 OpenClaw 桥接地址: [http://127.0.0.1](http://127.0.0.1):{PORT}/v1")
    print(f"============================================================\n")
    ThreadingServer(('localhost', PORT), AgentHandler).serve_forever()