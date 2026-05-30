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

def click_turnstile_checkbox(sb) -> bool:
    """
    通过获取 Turnstile iframe 的实际屏幕坐标，用 ActionChains 精确点击 checkbox
    返回是否成功点击
    """
    import pyautogui
    try:
        # 找到 Turnstile iframe
        iframes = sb.find_elements("iframe")
        turnstile_iframe = None
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenges.cloudflare.com" in src or "turnstile" in src:
                turnstile_iframe = iframe
                break

        if not turnstile_iframe:
            print("  未找到 Turnstile iframe，尝试找所有 iframe...")
            for iframe in iframes:
                src = iframe.get_attribute("src") or ""
                print(f"  iframe src: {src[:80]}")
            # 没找到 CF iframe，可能用的是不同方式嵌入
            return False

        # 获取 iframe 在页面中的位置和大小
        rect = sb.execute_script("""
            var el = arguments[0];
            var rect = el.getBoundingClientRect();
            return {
                x: rect.left,
                y: rect.top,
                width: rect.width,
                height: rect.height
            };
        """, turnstile_iframe)

        print(f"  Turnstile iframe 位置: x={rect['x']:.0f}, y={rect['y']:.0f}, w={rect['width']:.0f}, h={rect['height']:.0f}")

        # checkbox 在 iframe 左侧约 20px，垂直居中
        # checkbox 相对 iframe 的偏移大约是 (20, 高度/2)
        checkbox_x_in_iframe = 20
        checkbox_y_in_iframe = rect['height'] / 2

        # 用 ActionChains 移动到 iframe 内的 checkbox 位置点击
        from selenium.webdriver.common.action_chains import ActionChains
        actions = ActionChains(sb.driver)
        actions.move_to_element_with_offset(
            turnstile_iframe,
            checkbox_x_in_iframe - rect['width'] / 2,
            checkbox_y_in_iframe - rect['height'] / 2
        )
        actions.click()
        actions.perform()
        print("  ✅ ActionChains 点击 Turnstile checkbox 完成")
        return True

    except Exception as e:
        print(f"  ActionChains 点击失败: {e}")
        return False

def wait_for_turnstile_token(sb, max_wait: int = 15) -> bool:
    """等待 Turnstile token 生成，返回是否成功"""
    for i in range(max_wait):
        time.sleep(1)
        try:
            token = sb.execute_script(
                'return document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
            )
            if token and len(token) > 10:
                print(f"  ✅ Turnstile token 已生成（第{i+1}秒），长度: {len(token)}")
                return True
        except:
            pass
    print(f"  ❌ 等待 {max_wait} 秒后 token 仍未生成")
    return False

def handle_turnstile(sb, email: str) -> bool:
    """
    多策略处理 Turnstile，返回是否通过
    策略1: SeleniumBase 内置 uc_gui_click_captcha
    策略2: 计算 iframe 坐标用 ActionChains 点击
    策略3: 直接在 iframe 内找 checkbox 点击
    """
    # 策略1: uc_gui_click_captcha
    print(f"[{email}] Turnstile 策略1: uc_gui_click_captcha...")
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
        if wait_for_turnstile_token(sb, max_wait=5):
            return True
        print(f"[{email}] 策略1 token 未生成，尝试策略2...")
    except Exception as e:
        print(f"[{email}] 策略1 失败: {e}")

    # 策略2: ActionChains 精确坐标点击
    print(f"[{email}] Turnstile 策略2: ActionChains 坐标点击...")
    try:
        if click_turnstile_checkbox(sb):
            time.sleep(3)
            if wait_for_turnstile_token(sb, max_wait=8):
                return True
        print(f"[{email}] 策略2 token 未生成，尝试策略3...")
    except Exception as e:
        print(f"[{email}] 策略2 失败: {e}")

    # 策略3: 切换到 iframe 内直接点击 checkbox
    print(f"[{email}] Turnstile 策略3: 切换 iframe 内点击...")
    try:
        iframes = sb.find_elements("iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenges.cloudflare.com" in src or "turnstile" in src:
                sb.driver.switch_to.frame(iframe)
                try:
                    cb = sb.driver.find_element("css selector", 'input[type="checkbox"]')
                    cb.click()
                    print(f"[{email}]   iframe 内 checkbox 点击成功")
                except:
                    # 找不到 checkbox，直接点击 iframe 中央
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(sb.driver).move_by_offset(15, 15).click().perform()
                    print(f"[{email}]   iframe 中央点击")
                finally:
                    sb.driver.switch_to.default_content()
                time.sleep(3)
                if wait_for_turnstile_token(sb, max_wait=8):
                    return True
                break
    except Exception as e:
        print(f"[{email}] 策略3 失败: {e}")
        try:
            sb.driver.switch_to.default_content()
        except:
            pass

    # 策略4: uc_gui_handle_captcha
    print(f"[{email}] Turnstile 策略4: uc_gui_handle_captcha...")
    try:
        sb.uc_gui_handle_captcha()
        time.sleep(3)
        if wait_for_turnstile_token(sb, max_wait=8):
            return True
    except Exception as e:
        print(f"[{email}] 策略4 失败: {e}")

    return False

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
                    # 先填账号密码
                    print(f"[{email}] 填写账号密码...")
                    sb.type('input[type="email"], input[placeholder*="Email"]', email)
                    sb.sleep(0.5)
                    sb.type('input[type="password"]', password)
                    sb.sleep(1)

                    # 截图看填写后状态
                    shot = f"before_captcha_{email.replace('@','_')}_{attempt+1}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"📋 填写后截图（第{attempt+1}次）\n账号: <code>{email}</code>")

                    # 多策略处理 Turnstile
                    turnstile_ok = handle_turnstile(sb, email)
                    print(f"[{email}] Turnstile 处理结果: {'✅ 通过' if turnstile_ok else '❌ 未通过'}")

                    # 截图看 Turnstile 处理后状态
                    shot2 = f"after_captcha_{email.replace('@','_')}_{attempt+1}.png"
                    sb.save_screenshot(shot2)
                    tg_notify_photo_sync(shot2, caption=f"📋 Turnstile处理后截图（第{attempt+1}次）\ntoken通过: {turnstile_ok}")

                    # 点击登录
                    print(f"[{email}] 点击登录按钮...")
                    sb.click('button:contains("Log In")')
                    sb.sleep(8)

                    current_url = sb.get_current_url()
                    print(f"[{email}] 登录后URL: {current_url}")

                    still_login = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
                    if still_login:
                        shot3 = f"error_login_{email.replace('@','_')}_{attempt+1}.png"
                        sb.save_screenshot(shot3)
                        tg_notify_photo_sync(shot3, caption=f"❌ 第{attempt+1}次登录失败\nURL: {current_url}")
                        raise Exception("登录失败，仍停留在登录页")

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
                    tg_notify_photo_sync(shot, caption=f"🔍 列表页截图\n找不到状态元素\nURL: {sb.get_current_url()}")
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
