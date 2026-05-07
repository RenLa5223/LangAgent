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
global_state_lock = threading.Lock() # 新增：专门保护全局变量的锁

# 全局自愈与节律机制变量
api_cooldown_until = 0
consecutive_failures = 0
last_interaction_time = time.time()
next_proactive_delay = random.uniform(120, 240) * 60

def update_interaction_time(cfg):
    """安全地更新交互时间和下一次主动触发的延迟"""
    global last_interaction_time, next_proactive_delay
    with global_state_lock:
        last_interaction_time = time.time()
        next_proactive_delay = random.uniform(int(cfg.get("proactive_min", 120)), int(cfg.get("proactive_max", 240))) * 60

def safe_json_read(filepath, default_val):
    if not os.path.exists(filepath):
        return default_val
    content = ""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='gbk') as f:
                content = f.read()
        except:
            pass
    except Exception:
        pass

    if not content.strip():
        return default_val

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
        atomic_json_write(os.path.join(MEM_DIR, "memory_summary.json"), {"limit": "unlimited", "items": []})

def get_decay_score(item):
    imp = float(item.get('importance', 3))
    try:
        dt = datetime.strptime(item.get('time', ''), "%Y-%m-%d %H:%M:%S")
        hours_elapsed = (datetime.now() - dt).total_seconds() / 3600.0
    except:
        hours_elapsed = 0
    return 0.6 * imp - 0.4 * (hours_elapsed / 24.0)

def call_llm_with_circuit_breaker(cfg, messages, use_fallback=True):
    """
    带熔断机制的 LLM 调用。
    use_fallback: 如果为 True (如用户主动聊天)，在网络失败时返回预设文案。
                  如果为 False (如后台更新签名/主动发消息)，在网络失败时返回 None，直接放弃。
    """
    global api_cooldown_until, consecutive_failures
    
    with global_state_lock:
        cooldown_time = api_cooldown_until
        
    if time.time() < cooldown_time:
        return "（网络信号好像被外星人拦截了，稍微等我一下下哦...📡）" if use_fallback else None
    
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
            
            with global_state_lock:
                consecutive_failures = 0
            return reply
        except Exception as ex:
            time.sleep(1)
            
    with global_state_lock:
        consecutive_failures += 1
        if consecutive_failures >= 3:
            api_cooldown_until = time.time() + 60
            
    return "（网络卡卡的，你能再说一遍嘛？🥺）" if use_fallback else None

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
{{"content": "今天他跟我说...", "importance": 4, "new_user_profile": "籍贯：xxx\\n身高：xxx\\n饮食喜好：xxx\\n近期状态：xxx"}}"""
        
        chat_text = "\n".join([f"{cfg.get('ai_name', 'AI') if msg['role'] == 'agent' else cfg.get('user_name', '用户')}: {msg['content']}" for msg in recent_history])
        
        # 记忆总结也是后台任务，不需要降级回复污染数据
        reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": sys_prompt + "\n\n[对话记录]：\n" + chat_text}], use_fallback=False)
        
        if not reply:
            return

        start_idx = reply.find('{')
        end_idx = reply.rfind('}')
        if start_idx != -1 and end_idx != -1:
            try:
                json_str = reply[start_idx:end_idx+1]
                new_mem = json.loads(json_str)
                
                summary_file = os.path.join(MEM_DIR, "memory_summary.json")
                with file_lock:
                    mem_data = safe_json_read(summary_file, {"limit": "unlimited", "items": []})
                    if new_mem.get("content"):
                        new_mem['time'] = time.strftime("%Y-%m-%d %H:%M:%S")
                        mem_data['items'].append(new_mem)
                        limit_val = mem_data.get('limit', 'unlimited')
                        if limit_val != 'unlimited':
                            limit_int = int(limit_val)
                            if len(mem_data['items']) > limit_int:
                                mem_data['items'].sort(key=get_decay_score, reverse=True)
                                mem_data['items'] = mem_data['items'][:limit_int]
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
            if not cfg.get("proactive_enabled", False):
                continue
            
            now = datetime.now()
            curr_str = now.strftime("%H:%M")
            if not (cfg.get("proactive_start", "08:00") <= curr_str <= cfg.get("proactive_end", "22:00")):
                continue
                
            with global_state_lock:
                passed_time = time.time() - last_interaction_time
                target_delay = next_proactive_delay
                
            if passed_time > target_delay:
                ai_name = cfg.get("ai_name", "AI")
                user_name = cfg.get("user_name", "用户")
                prompt = f"你是{ai_name}。{user_name}已经有一段时间没说话了，请根据你的人设和内心档案，主动发一条不超过15个字的日常关心或分享。不要用任何解释和前缀，直接给出对话内容。"
                
                # 安全阻断：后台触发的主动消息禁止使用降级兜底文案，失败则直接放弃
                reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": prompt}], use_fallback=False)
                
                if reply:
                    parts = [p.strip() for p in re.split(r'(?<=[。！？!\?\n])', reply) if p.strip()]
                    if not parts: parts = [reply]
                    
                    history_file = os.path.join(MEM_DIR, "chat_history.json")
                    with file_lock:
                        history = safe_json_read(history_file, [])
                        for p in parts:
                            history.append({"role": "agent", "content": p, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                        atomic_json_write(history_file, history)
                    
                    update_interaction_time(cfg)
                    print(f"[{time.strftime('%H:%M:%S')}] 💌 [主动关怀] 消息已安全推入时间流")
        except Exception:
            pass

threading.Thread(target=proactive_worker, daemon=True).start()

class AgentHandler(http.server.BaseHTTPRequestHandler): 

    def get_config(self):
        config_path = os.path.join(CONFIG_DIR, "config.json")
        cfg = {"url": "http://localhost:11434/v1/chat/completions", "key": "", "model": "", "hide_think": True, "ai_name": "", "user_name": ""}
        cfg.update(safe_json_read(config_path, {}))
        return cfg

    def do_GET(self):
        if self.path == "/":
            self.send_response(200); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
            with open("index.html", "r", encoding="utf-8") as f: self.wfile.write(f.read().encode("utf-8"))
            return

        if self.path.startswith("/api/list/"): 
            self.send_response(200); self.end_headers()
            return
            
        elif self.path.startswith("/api/poll"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            client_count = int(qs.get('count', ['0'])[0])
            
            history_file = os.path.join(MEM_DIR, "chat_history.json")
            with file_lock:
                history = safe_json_read(history_file, [])
            
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
            else:
                self.send_response(404); self.end_headers()

        elif self.path == "/api/signature":
            try:
                cfg = self.get_config()
                if not cfg.get("ai_name"): self.send_response(400); self.end_headers(); return

                sig_file = os.path.join(MEM_DIR, "daily_signature.json")
                today_str = time.strftime("%Y-%m-%d")

                with file_lock:
                    sig_data = safe_json_read(sig_file, {})
                        
                if sig_data.get("date") == today_str and sig_data.get("signature"):
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": sig_data["signature"]}).encode('utf-8'))
                    return

                sys_prompt = f"你是{cfg['ai_name']}。请写一句【15字以内】的【社交软件个性签名】。要求：口语化、第一人称、展现你今天的心情或状态。直接返回签名文本，不要任何解释。"
                
                # 安全阻断：后台刷新签名禁止降级兜底，网络故障直接放弃，防止写入脏数据
                ai_reply = call_llm_with_circuit_breaker(cfg, [{"role": "user", "content": sys_prompt}], use_fallback=False)

                if ai_reply:
                    ai_reply = ai_reply.strip(' "”\'“\n')
                    atomic_json_write(sig_file, {"date": today_str, "signature": ai_reply})
                    self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                    self.wfile.write(json.dumps({"signature": ai_reply}).encode('utf-8'))
                else:
                    self.send_response(500); self.end_headers()
            except Exception:
                self.send_response(500); self.end_headers()

        elif self.path.startswith("/api/read/"):
            parts = self.path.split("/")
            folder = urllib.parse.unquote(parts[-2])
            filename = os.path.basename(urllib.parse.unquote(parts[-1])) 
            
            if folder not in ALLOWED_FOLDERS:
                self.send_response(403); self.end_headers(); return
                
            file_path = os.path.join(DATA_DIR, folder, filename)
            with file_lock:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f: content = f.read()
                    self.send_response(200); self.send_header('Content-type', 'text/plain; charset=utf-8'); self.end_headers()
                    self.wfile.write(content.encode('utf-8'))
                else:
                    self.send_response(404); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 15 * 1024 * 1024: 
            self.send_response(413); self.end_headers(); return
            
        if self.path == "/api/save":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                folder = post_data.get('folder')
                filename = os.path.basename(post_data.get('filename'))
                
                if folder not in ALLOWED_FOLDERS:
                    self.send_response(403); self.end_headers(); return
                    
                target_path = os.path.join(DATA_DIR, folder, filename)
                with file_lock:
                    with open(target_path, 'w', encoding='utf-8') as f: f.write(post_data.get('content'))
                self.send_response(200); self.end_headers(); self.wfile.write(b"Success")
            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))

        elif self.path == "/api/upload_avatar":
            try:
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                role = post_data.get('role')
                img_b64 = post_data.get('image')
                
                if role not in ['agent', 'user'] or not img_b64:
                    self.send_response(400); self.end_headers(); return
                    
                if ',' in img_b64:
                    img_b64 = img_b64.split(',')[1]
                img_data = base64.b64decode(img_b64)
                
                target_dir = AGENT_AVATAR_DIR if role == "agent" else USER_AVATAR_DIR
                with file_lock:
                    for filename in os.listdir(target_dir):
                        file_path = os.path.join(target_dir, filename)
                        if os.path.isfile(file_path): os.remove(file_path)
                    with open(os.path.join(target_dir, "avatar.png"), "wb") as f: f.write(img_data)
                self.send_response(200); self.end_headers(); self.wfile.write(b"Success")
            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))
                
        elif self.path == "/api/reset":
            try:
                with file_lock:
                    cfg_path = os.path.join(CONFIG_DIR, "config.json")
                    if os.path.exists(cfg_path): os.remove(cfg_path)
                    
                    for p in [os.path.join(AGENT_PROFILE_DIR, "人物档案.txt"), 
                              os.path.join(USER_PROFILE_DIR, "用户档案.txt"), 
                              os.path.join(INNER_THOUGHTS_DIR, "人物内心.txt")]:
                        if os.path.exists(p): os.remove(p)
                    
                    atomic_json_write(os.path.join(MEM_DIR, "chat_history.json"), [])
                    atomic_json_write(os.path.join(MEM_DIR, "memory_summary.json"), {"limit": "unlimited", "items": []})
                    
                    sig_file = os.path.join(MEM_DIR, "daily_signature.json")
                    if os.path.exists(sig_file): os.remove(sig_file)

                    for d in [AGENT_AVATAR_DIR, USER_AVATAR_DIR]:
                        for filename in os.listdir(d):
                            file_path = os.path.join(d, filename)
                            if os.path.isfile(file_path): os.remove(file_path)

                self.send_response(200); self.end_headers(); self.wfile.write(b"Reset Success")
            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))
                
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
            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

        elif self.path == "/api/chat":
            try:
                cfg = self.get_config()
                # 记录最后一次交互时间，用于重置主动关怀计时器
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

                # 正常聊天，允许在断网时返回降级文案进行互动
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
    print(f"🚀 安全级 Agent 驱动中枢已启动 [时间记忆衰减+心跳交互架构+防脏数据]")
    print(f"🌐 正在监听端口 {PORT}")
    print(f"============================================================\n")
    ThreadingServer(('localhost', PORT), AgentHandler).serve_forever()