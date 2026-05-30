import os
import sys
import asyncio
import aiohttp
import subprocess
import time
from datetime import datetime

LOGIN_URL = "https://wispbyte.com/client/servers"
GOST_LOCAL_PORT = 18080

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

def tg_notify_sync(message: str):
    """同步版 TG 通知，供线程中调用"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Warning: 未设置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知")
        return
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
        print("✅ TG 通知发送成功")
    except Exception as e:
        print(f"Warning: TG 通知失败: {e}")

def tg_notify_photo_sync(photo_path: str, caption: str = ""):
    """同步版 TG 图片通知，供线程中调用"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    import urllib.request
    try:
        from email.mime.multipart import MIMEMultipart
        import mimetypes, uuid
        boundary = uuid.uuid4().hex
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{TG_CHAT_ID}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="photo.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"Warning: TG 图片通知失败: {e}")
    finally:
        try:
            os.remove(photo_path)
        except:
            pass

async def tg_notify(message: str):
    tg_notify_sync(message)

def start_gost(socks5_proxy: str) -> subprocess.Popen:
    cmd = ["gost", f"-L=http://127.0.0.1:{GOST_LOCAL_PORT}", f"-F={socks5_proxy}"]
    print(f"[gost] 启动: gost -L=http://127.0.0.1:{GOST_LOCAL_PORT} -F=***")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"gost 启动失败: {proc.stderr.read().decode()}")
    print(f"[gost] 启动成功 PID={proc.pid}")
    return proc

def build_report(results, start_time, end_time):
    online    = [r for r in results if r.get("server_status") == "already_online"]
    restarted = [r for r in results if r.get("server_status") == "restarted"]
    timeout   = [r for r in results if r.get("server_status") == "restart_timeout"]
    failed    = [r for r in results if not r["success"]]

    lines = [
        "🖥 Wispbyte 服务器监控报告",
        f"目标: <a href='https://wispbyte.com/client'>控制面板</a>",
        f"时间: {start_time} → {end_time}",
        ""
    ]
    if online:
        lines.append("✅ 服务器在线（无需操作）：")
        lines.extend([f"• <code>{r['email']}</code>" for r in online])
        lines.append("")
    if restarted:
        lines.append("🔄 检测到离线，已成功启动：")
        lines.extend([f"• <code>{r['email']}</code>" for r in restarted])
        lines.append("")
    if timeout:
        lines.append("⚠️ 已点击启动，但未确认上线（超时）：")
        lines.extend([f"• <code>{r['email']}</code>" for r in timeout])
        lines.append("")
    if failed:
        lines.append("❌ 失败账号：")
        lines.extend([f"• <code>{r['email']}</code>  原因: {r.get('reason', '未知')}" for r in failed])

    return "\n".join(lines)

def login_one(email: str, password: str) -> dict:
    from seleniumbase import SB

    result = {"email": email, "success": False, "server_status": None, "reason": ""}
    max_retries = 2
    proxy = f"http://127.0.0.1:{GOST_LOCAL_PORT}"

    for attempt in range(max_retries + 1):
        try:
            print(f"[{email}] 尝试 {attempt + 1}: 启动 UC 模式浏览器...")
            with SB(
                uc=True,
                headless=False,
                xvfb=True,
                proxy=proxy,
                incognito=True,
            ) as sb:
                print(f"[{email}] 打开登录页...")
                sb.open(LOGIN_URL)
                sb.sleep(5)

                need_login = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
                print(f"[{email}] 是否需要登录: {need_login}，当前URL: {sb.get_current_url()}")

                if need_login:
                    # ── 关键：先处理 Turnstile，再填表单 ──
                    # Turnstile 在页面加载时就出现，需要先通过它
                    print(f"[{email}] 步骤1: 先处理 Turnstile 验证...")
                    try:
                        sb.uc_gui_click_captcha()
                        print(f"[{email}] uc_gui_click_captcha 完成，等待5秒...")
                        sb.sleep(5)
                    except Exception as te:
                        print(f"[{email}] uc_gui_click_captcha 失败: {te}")
                        try:
                            sb.uc_gui_handle_captcha()
                            print(f"[{email}] uc_gui_handle_captcha 完成")
                            sb.sleep(5)
                        except Exception as te2:
                            print(f"[{email}] uc_gui_handle_captcha 也失败: {te2}")

                    # 检查 Turnstile 是否已通过（token 是否生成）
                    token_val = sb.execute_script(
                        'return document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
                    )
                    print(f"[{email}] Turnstile token 长度: {len(token_val) if token_val else 0}")

                    # 步骤2: 填写表单
                    print(f"[{email}] 步骤2: 填写账号密码...")
                    sb.type('input[type="email"], input[placeholder*="Email"]', email)
                    sb.sleep(0.5)
                    sb.type('input[type="password"]', password)
                    sb.sleep(1)

                    # 步骤3: 如果 token 还没生成，再试一次 captcha
                    if not token_val or len(token_val) < 10:
                        print(f"[{email}] 步骤3: token未生成，再次处理 Turnstile...")
                        try:
                            sb.uc_gui_click_captcha()
                            sb.sleep(5)
                        except Exception as te3:
                            print(f"[{email}] 第二次 captcha 处理失败: {te3}")

                        token_val = sb.execute_script(
                            'return document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
                        )
                        print(f"[{email}] 二次处理后 token 长度: {len(token_val) if token_val else 0}")

                    # 步骤4: 点击登录
                    print(f"[{email}] 步骤4: 点击登录按钮...")
                    sb.click('button:contains("Log In")')
                    sb.sleep(8)

                    current_url = sb.get_current_url()
                    print(f"[{email}] 登录后URL: {current_url}")

                    still_login = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
                    if still_login:
                        shot = f"error_login_{email.replace('@','_')}_{attempt+1}.png"
                        sb.save_screenshot(shot)
                        tg_notify_photo_sync(shot, caption=f"❌ 第{attempt+1}次登录失败\n账号: <code>{email}</code>\nTurnstile未通过\nURL: {current_url}")
                        raise Exception("登录失败，仍停留在登录页（Turnstile未通过）")

                    print(f"[{email}] ✅ 登录成功！")
                else:
                    print(f"[{email}] ✅ 已有登录态")

                result["success"] = True

                # 等待列表页渲染
                print(f"[{email}] 等待服务器列表渲染（8秒）...")
                sb.sleep(8)

                # 读取服务器状态
                status_els = sb.find_elements('.server-status-text')
                list_statuses = [el.text.strip().lower() for el in status_els]
                print(f"[{email}] 列表页服务器状态: {list_statuses}")

                if not list_statuses:
                    shot = f"debug_list_{email.replace('@','_')}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"🔍 列表页调试截图\n找不到状态元素\nURL: {sb.get_current_url()}")
                    raise Exception("找不到 .server-status-text，列表页未正确加载")

                has_offline = any("offline" in s for s in list_statuses)

                if not has_offline:
                    print(f"[{email}] ✅ 所有服务器在线，无需操作")
                    result["server_status"] = "already_online"
                    return result

                # 有离线服务器，点击 Manage Server
                print(f"[{email}] 检测到离线，寻找 Manage Server 按钮...")
                manage_sel = None
                for sel in ['.server-action-btn.primary', 'button:contains("Manage Server")']:
                    try:
                        if sb.is_element_present(sel):
                            manage_sel = sel
                            break
                    except:
                        pass

                if not manage_sel:
                    shot = f"debug_manage_{email.replace('@','_')}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"🔍 找不到 Manage Server\nURL: {sb.get_current_url()}")
                    raise Exception("找不到 Manage Server 按钮")

                print(f"[{email}] 点击 Manage Server...")
                sb.click(manage_sel)
                sb.sleep(5)
                print(f"[{email}] ✅ 已进入 Console 页，URL: {sb.get_current_url()}")

                # Console 页确认状态
                sb.wait_for_element('#online-status-text', timeout=20)
                status_text = sb.get_text('#online-status-text').strip()
                print(f"[{email}] Console 页状态: [{status_text}]")

                if status_text.lower() == "online":
                    print(f"[{email}] ✅ 服务器在线")
                    result["server_status"] = "already_online"
                    return result

                # 离线则点击 Start
                print(f"[{email}] 服务器离线，点击 Start...")
                sb.click('#start-btn')
                print(f"[{email}] 已点击 Start，等待启动（最多60秒）...")

                started = False
                for _ in range(60):
                    sb.sleep(1)
                    try:
                        cur = sb.get_text('#online-status-text').strip().lower()
                        if cur == "online":
                            started = True
                            break
                    except:
                        pass

                if started:
                    print(f"[{email}] ✅ 服务器已成功启动！")
                    result["server_status"] = "restarted"
                else:
                    print(f"[{email}] ⚠️ 60秒内未变为 Online")
                    shot = f"warn_{email.replace('@','_')}_{int(time.time())}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"⚠️ 启动超时\n账号: <code>{email}</code>")
                    result["server_status"] = "restart_timeout"

                return result

        except Exception as e:
            print(f"[{email}] 第 {attempt + 1} 次失败: {e}")
            result["reason"] = str(e)[:200]
            if attempt >= max_retries:
                tg_notify_sync(f"❌ Wispbyte 最终失败\n账号: <code>{email}</code>\n原因: {str(e)[:200]}")

    return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    socks5_proxy = os.getenv("SOCKS5_PROXY", "").strip()
    gost_proc = None
    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            print("[gost] 代理已启动，出口IP将使用 SOCKS5 节点")
        except Exception as e:
            print(f"[gost] 启动失败: {e}")
    else:
        print("[gost] 未配置 SOCKS5_PROXY")

    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        tg_notify_sync("❌ Failed: 未配置任何账号")
        return

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    if not accounts:
        tg_notify_sync("❌ Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password")
        return

    try:
        loop = asyncio.get_event_loop()
        results = []
        for acc in accounts:
            email, pwd = acc.split(":", 1)
            result = await loop.run_in_executor(None, login_one, email, pwd)
            results.append(result)

        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_msg = build_report(results, start_time, end_time)
        tg_notify_sync(final_msg)
        print(final_msg)
    finally:
        if gost_proc:
            gost_proc.terminate()
            print("[gost] 已关闭代理进程")

if __name__ == "__main__":
    accounts = os.getenv('LOGIN_ACCOUNTS', '').strip()
    count = len([a for a in accounts.split(',') if ':' in a]) if accounts else 0
    print(f"[{datetime.now()}] login.py 开始运行", file=sys.stderr)
    print(f"Python: {sys.version.split()[0]}, 有效账号数: {count}", file=sys.stderr)
    asyncio.run(main())
