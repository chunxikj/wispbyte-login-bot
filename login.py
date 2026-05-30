import os
import sys
import asyncio
import subprocess
import time
from datetime import datetime

LOGIN_URL = "https://wispbyte.com/client/servers"
CONSOLE_URL = "https://wispbyte.com/client/servers/67461084/console"
GOST_LOCAL_PORT = 18080

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

def tg_notify_sync(message: str):
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
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    import urllib.request, uuid
    try:
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

def close_popups(sb, email: str):
    """关闭页面上可能出现的弹窗，最多尝试3次"""
    # 可能出现的关闭按钮选择器
    close_selectors = [
        'button:contains("Maybe later")',
        'button:contains("maybe later")',
        '.modal-close',
        'button.close',
        '[aria-label="Close"]',
        'button:contains("×")',
        'button:contains("✕")',
    ]
    closed = 0
    for _ in range(3):
        found = False
        for sel in close_selectors:
            try:
                if sb.is_element_present(sel):
                    sb.click(sel)
                    print(f"[{email}] 关闭弹窗: {sel}")
                    time.sleep(1)
                    closed += 1
                    found = True
                    break
            except:
                pass
        if not found:
            break
    print(f"[{email}] 共关闭 {closed} 个弹窗")

def get_server_status(sb, email: str) -> str:
    """多方式读取 Console 页服务器状态，返回 online/offline/unknown"""
    # 方式1: #online-status-text
    try:
        el = sb.driver.find_element("css selector", "#online-status-text")
        text = el.text.strip().lower()
        if text:
            print(f"[{email}] 状态来源 #online-status-text: [{text}]")
            return text
    except:
        pass

    # 方式2: #server-uptime-panel
    try:
        el = sb.driver.find_element("css selector", "#server-uptime-panel")
        text = el.text.strip().lower()
        if text:
            print(f"[{email}] 状态来源 #server-uptime-panel: [{text}]")
            if "offline" in text:
                return "offline"
            if text:
                return "online"
    except:
        pass

    # 方式3: #start-btn disabled 属性
    try:
        btn = sb.driver.find_element("css selector", "#start-btn")
        disabled = btn.get_attribute("disabled")
        if disabled:
            print(f"[{email}] #start-btn disabled → online")
            return "online"
        else:
            print(f"[{email}] #start-btn not disabled → offline")
            return "offline"
    except:
        pass

    print(f"[{email}] 无法读取状态，返回 unknown")
    return "unknown"

def wait_for_online(sb, email: str, max_seconds: int = 60) -> bool:
    """等待服务器变为 online，最多等 max_seconds 秒"""
    for i in range(max_seconds):
        time.sleep(1)
        status = get_server_status(sb, email)
        if status == "online":
            print(f"[{email}] ✅ 服务器已上线（第{i+1}秒）")
            return True
    return False

def login_one(email: str, password: str) -> dict:
    from seleniumbase import SB

    result = {"email": email, "success": False, "server_status": None, "reason": ""}
    proxy = f"http://127.0.0.1:{GOST_LOCAL_PORT}"

    try:
        print(f"[{email}] 启动 UC 模式浏览器...")
        with SB(
            uc=True,
            headless=False,
            xvfb=True,
            proxy=proxy,
            incognito=True,
        ) as sb:

            # ── 步骤1: 打开登录页 ──
            print(f"[{email}] 打开登录页...")
            sb.open(LOGIN_URL)
            sb.sleep(5)

            need_login = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
            print(f"[{email}] 是否需要登录: {need_login}，当前URL: {sb.get_current_url()}")

            if need_login:
                print(f"[{email}] 填写账号密码...")
                sb.type('input[type="email"], input[placeholder*="Email"]', email)
                sb.sleep(0.5)
                sb.type('input[type="password"]', password)
                sb.sleep(1)

                print(f"[{email}] 处理 Turnstile...")
                try:
                    sb.uc_gui_handle_captcha()
                    sb.sleep(3)
                    token = sb.execute_script(
                        'return document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
                    )
                    print(f"[{email}] Turnstile token 长度: {len(token) if token else 0}")
                except Exception as e:
                    print(f"[{email}] uc_gui_handle_captcha 异常: {e}")

                print(f"[{email}] 点击登录按钮...")
                sb.click('button:contains("Log In")')
                sb.sleep(8)

                current_url = sb.get_current_url()
                print(f"[{email}] 登录后URL: {current_url}")

                still_login = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
                if still_login:
                    shot = f"error_login_{email.replace('@','_')}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"❌ 登录失败\n账号: <code>{email}</code>\nURL: {current_url}")
                    raise Exception("登录失败，仍停留在登录页（Turnstile未通过）")

                print(f"[{email}] ✅ 登录成功！")
            else:
                print(f"[{email}] ✅ 已有登录态")

            result["success"] = True

            # ── 步骤2: 直接跳转 Console 页 ──
            print(f"[{email}] 跳转 Console 页: {CONSOLE_URL}")
            sb.open(CONSOLE_URL)
            sb.sleep(8)
            print(f"[{email}] 当前URL: {sb.get_current_url()}")

            # ── 步骤3: 关闭弹窗（最多两轮）──
            print(f"[{email}] 检查并关闭弹窗...")
            close_popups(sb, email)
            time.sleep(2)
            # 再关一轮，防止有多个弹窗
            close_popups(sb, email)
            time.sleep(1)

            # 截图确认弹窗已关闭
            shot = f"console_{email.replace('@','_')}.png"
            sb.save_screenshot(shot)
            tg_notify_photo_sync(shot, caption=f"📋 Console 页截图（弹窗处理后）\n账号: <code>{email}</code>")

            # ── 步骤4: 读取服务器状态 ──
            status = get_server_status(sb, email)
            print(f"[{email}] Console 页状态: [{status}]")

            if status == "online":
                print(f"[{email}] ✅ 服务器在线，无需操作")
                result["server_status"] = "already_online"
                return result

            # ── 步骤5: 离线则点击 Start ──
            print(f"[{email}] 服务器离线（状态:{status}），点击 Start 按钮...")
            try:
                start_btn = sb.driver.find_element("css selector", "#start-btn")
                start_btn.click()
                print(f"[{email}] ✅ 已点击 Start，等待启动（最多60秒）...")
            except Exception as e:
                shot = f"debug_start_{email.replace('@','_')}.png"
                sb.save_screenshot(shot)
                tg_notify_photo_sync(shot, caption=f"🔍 找不到 Start 按钮\n错误: {str(e)[:100]}")
                raise Exception(f"找不到 Start 按钮: {e}")

            # ── 步骤6: 等待上线 ──
            started = wait_for_online(sb, email, max_seconds=60)

            if started:
                print(f"[{email}] ✅ 服务器已成功启动！")
                result["server_status"] = "restarted"
            else:
                print(f"[{email}] ⚠️ 60秒内未变为 Online")
                shot = f"warn_{email.replace('@','_')}_{int(time.time())}.png"
                sb.save_screenshot(shot)
                tg_notify_photo_sync(shot, caption=f"⚠️ 启动超时\n账号: <code>{email}</code>\n已点击 Start 但60秒内未上线")
                result["server_status"] = "restart_timeout"

            return result

    except Exception as e:
        print(f"[{email}] 执行失败: {e}")
        result["reason"] = str(e)[:200]
        tg_notify_sync(f"❌ Wispbyte 失败\n账号: <code>{email}</code>\n原因: {str(e)[:200]}")

    return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    socks5_proxy = os.getenv("SOCKS5_PROXY", "").strip()
    gost_proc = None
    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            print("[gost] 代理已启动")
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
