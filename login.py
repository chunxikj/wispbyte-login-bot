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
    close_selectors = [
        'button:contains("Maybe later")',
        'button:contains("maybe later")',
        '.modal-close',
        'button.close',
        '[aria-label="Close"]',
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
    if closed:
        print(f"[{email}] 共关闭 {closed} 个弹窗")

def js_get_status(sb, email: str) -> str:
    try:
        status_text = sb.execute_script(
            "(function(){ var el=document.getElementById('online-status-text'); return el ? el.textContent.trim().toLowerCase() : ''; })()"
        )
        print(f"[{email}] online-status-text: [{status_text}]")
        if status_text and status_text not in ('', 'loading', 'loading...'):
            return status_text

        uptime_text = sb.execute_script(
            "(function(){ var el=document.getElementById('server-uptime-panel'); return el ? el.textContent.trim().toLowerCase() : ''; })()"
        )
        print(f"[{email}] server-uptime-panel: [{uptime_text}]")
        if uptime_text and uptime_text not in ('', 'loading', 'loading...'):
            return 'offline' if 'offline' in uptime_text else 'online'

        disabled = sb.execute_script(
            "(function(){ var btn=document.getElementById('start-btn'); if(!btn) return null; return btn.disabled; })()"
        )
        if disabled is not None:
            return 'online' if disabled else 'offline'

    except Exception as e:
        print(f"[{email}] JS读取状态失败: {e}")

    return 'unknown'

def js_click_start(sb, email: str) -> bool:
    try:
        result = sb.execute_script(
            "(function(){ var btn=document.getElementById('start-btn'); if(btn){ btn.click(); return true; } return false; })()"
        )
        if result:
            print(f"[{email}] ✅ JS 点击 Start 成功")
            return True
        print(f"[{email}] ❌ JS 未找到 Start 按钮")
        return False
    except Exception as e:
        print(f"[{email}] JS 点击 Start 失败: {e}")
        return False

def check_verify_popup(sb, email: str) -> bool:
    try:
        has_popup = sb.execute_script(
            "(function(){ return document.querySelector('.wisp-start-captcha-modal') !== null; })()"
        )
        result = bool(has_popup)
        if result:
            print(f"[{email}] 检测到 .wisp-start-captcha-modal 弹窗")
        return result
    except:
        return False

def wait_for_turnstile_in_popup(sb, email: str, max_wait: int = 35) -> bool:
    """等待弹窗内 Turnstile iframe 加载，同时检测是否显示加载失败错误"""
    print(f"[{email}] 等待弹窗内 Turnstile 加载（最多{max_wait}秒）...")
    for i in range(max_wait):
        time.sleep(1)
        try:
            # 检查是否有加载失败错误提示
            has_error = sb.execute_script(
                "(function(){ "
                "var modal=document.querySelector('.wisp-start-captcha-modal');"
                "if(!modal) return false;"
                "var err=modal.querySelector('.wisp-start-captcha-error');"
                "if(err && !err.hidden && err.textContent.trim()) return true;"
                "var text=modal.textContent||'';"
                "return text.includes('Unable to load') || text.includes('Please refresh');"
                "})()"
            )
            if has_error:
                print(f"[{email}] ⚠️ 弹窗内 CAPTCHA 加载失败（第{i+1}秒），提前退出等待")
                return False

            # 检查 iframe 是否出现
            has_iframe = sb.execute_script(
                "(function(){ "
                "var widget=document.querySelector('.wisp-start-captcha-widget');"
                "if(!widget) return false;"
                "return widget.querySelectorAll('iframe').length > 0;"
                "})()"
            )
            if has_iframe:
                print(f"[{email}] ✅ Turnstile iframe 已出现（第{i+1}秒）")
                time.sleep(2)
                return True
        except:
            pass
        if (i + 1) % 5 == 0:
            print(f"[{email}] 已等待 {i+1} 秒...")
    print(f"[{email}] Turnstile 未加载成功")
    return False

def js_cancel_verify_popup(sb, email: str):
    try:
        cancelled = sb.execute_script(
            "(function(){ "
            "var modal=document.querySelector('.wisp-start-captcha-modal');"
            "if(!modal) return false;"
            "var btns=modal.querySelectorAll('button');"
            "for(var i=0;i<btns.length;i++){"
            "  if(btns[i].textContent.trim()==='Cancel'){ btns[i].click(); return true; }"
            "}"
            "return false;"
            "})()"
        )
        if cancelled:
            print(f"[{email}] ✅ Cancel 已点击")
            time.sleep(2)
            return True
    except Exception as e:
        print(f"[{email}] 点击 Cancel 失败: {e}")
    return False

def reload_console_page(sb, email: str):
    """刷新 Console 页面并等待状态加载"""
    print(f"[{email}] 刷新 Console 页面，重新加载资源...")
    sb.open(CONSOLE_URL)
    time.sleep(5)
    close_popups(sb, email)
    time.sleep(1)
    close_popups(sb, email)
    print(f"[{email}] 等待页面状态更新（15秒）...")
    time.sleep(15)
    status = js_get_status(sb, email)
    print(f"[{email}] 刷新后状态: [{status}]")
    return status

def wait_for_online_js(sb, email: str, max_seconds: int = 60) -> bool:
    for i in range(max_seconds):
        time.sleep(1)
        status = js_get_status(sb, email)
        if status == 'online':
            print(f"[{email}] ✅ 服务器已上线（第{i+1}秒）")
            return True
        if (i + 1) % 10 == 0:
            print(f"[{email}] 已等待 {i+1} 秒，当前状态: {status}")
    return False

def start_server_with_verify(sb, email: str, max_retries: int = 3) -> bool:
    """
    点击 Start，处理 Verify before starting 弹窗
    CAPTCHA 加载失败时：Cancel → 刷新页面 → 重试
    """
    for attempt in range(max_retries):
        print(f"[{email}] 点击 Start（第{attempt+1}次）...")
        clicked = js_click_start(sb, email)
        if not clicked:
            print(f"[{email}] 找不到 Start 按钮")
            return False

        # 等待35秒，给弹窗足够时间出现
        print(f"[{email}] 等待35秒，检查是否出现验证弹窗...")
        time.sleep(35)

        has_popup = check_verify_popup(sb, email)

        if not has_popup:
            status = js_get_status(sb, email)
            print(f"[{email}] 无弹窗，当前状态: {status}")
            if status == 'online':
                return True
            print(f"[{email}] 等待服务器启动...")
            return wait_for_online_js(sb, email, max_seconds=60)

        # 有弹窗，等待 Turnstile 加载
        turnstile_loaded = wait_for_turnstile_in_popup(sb, email, max_wait=35)

        if turnstile_loaded:
            # Turnstile 加载成功，尝试自动处理
            shot = f"verify_{email.replace('@','_')}_{int(time.time())}.png"
            sb.save_screenshot(shot)
            tg_notify_photo_sync(shot, caption=f"🔐 Verify 弹窗（已加载）\n账号: <code>{email}</code>")

            print(f"[{email}] 尝试 uc_gui_handle_captcha...")
            try:
                sb.uc_gui_handle_captcha()
                time.sleep(5)
                still_popup = check_verify_popup(sb, email)
                if not still_popup:
                    print(f"[{email}] ✅ 验证通过，弹窗已消失！")
                    return wait_for_online_js(sb, email, max_seconds=60)
                print(f"[{email}] 验证未通过，弹窗仍存在")
            except Exception as e:
                print(f"[{email}] uc_gui_handle_captcha 失败: {e}")

            # 验证未通过，Cancel
            js_cancel_verify_popup(sb, email)

        else:
            # CAPTCHA 加载失败，直接 Cancel
            print(f"[{email}] CAPTCHA 加载失败，点击 Cancel...")
            js_cancel_verify_popup(sb, email)

        # ── 关键：刷新页面，让资源重新加载 ──
        if attempt < max_retries - 1:
            print(f"[{email}] 刷新页面后重试（第{attempt+2}次）...")
            status = reload_console_page(sb, email)
            if status == 'online':
                print(f"[{email}] ✅ 刷新后检测到服务器已上线！")
                return True
            # 继续下一轮点 Start
        else:
            print(f"[{email}] 已重试 {max_retries} 次，仍未成功")

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

            # ── 步骤1: 登录 ──
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
                        "(function(){ var el=document.querySelector('input[name=\"cf-turnstile-response\"]'); return el ? el.value : ''; })()"
                    )
                    print(f"[{email}] Turnstile token 长度: {len(token) if token else 0}")
                except Exception as e:
                    print(f"[{email}] uc_gui_handle_captcha 异常: {e}")

                print(f"[{email}] 点击登录按钮...")
                sb.click('button:contains("Log In")')

                print(f"[{email}] 等待登录完成（最多15秒）...")
                for i in range(15):
                    time.sleep(1)
                    still_form = sb.is_element_present('input[type="email"], input[placeholder*="Email"]')
                    if not still_form:
                        print(f"[{email}] ✅ 登录成功（第{i+1}秒）")
                        break
                else:
                    shot = f"error_login_{email.replace('@','_')}.png"
                    sb.save_screenshot(shot)
                    tg_notify_photo_sync(shot, caption=f"❌ 登录失败\n账号: <code>{email}</code>")
                    raise Exception("登录失败，15秒内表单未消失")

                print(f"[{email}] 登录后URL: {sb.get_current_url()}")
            else:
                print(f"[{email}] ✅ 已有登录态")

            result["success"] = True

            # ── 步骤2: 跳转 Console 页 ──
            print(f"[{email}] 跳转 Console 页: {CONSOLE_URL}")
            sb.open(CONSOLE_URL)
            sb.sleep(5)

            current_url = sb.get_current_url()
            print(f"[{email}] 当前URL: {current_url}")

            if "chrome-error" in current_url:
                print(f"[{email}] 页面加载失败，重试...")
                sb.sleep(3)
                sb.open(CONSOLE_URL)
                sb.sleep(8)
                current_url = sb.get_current_url()
                if "chrome-error" in current_url:
                    raise Exception(f"Console 页无法访问，URL: {current_url}")

            # ── 步骤3: 关闭广告弹窗 ──
            print(f"[{email}] 检查并关闭弹窗...")
            close_popups(sb, email)
            time.sleep(1)
            close_popups(sb, email)

            # ── 步骤4: 等待状态更新 ──
            print(f"[{email}] 等待页面状态更新（15秒）...")
            sb.sleep(15)

            shot = f"console_{email.replace('@','_')}.png"
            sb.save_screenshot(shot)
            tg_notify_photo_sync(shot, caption=f"📋 Console 页截图\n账号: <code>{email}</code>")

            # ── 步骤5: 读取状态 ──
            status = js_get_status(sb, email)
            print(f"[{email}] Console 页状态: [{status}]")

            if status == 'online':
                print(f"[{email}] ✅ 服务器在线，无需操作")
                result["server_status"] = "already_online"
                return result

            # ── 步骤6: 启动服务器 ──
            print(f"[{email}] 服务器离线（状态:{status}），开始启动流程...")
            started = start_server_with_verify(sb, email, max_retries=3)

            if started:
                print(f"[{email}] ✅ 服务器已成功启动！")
                result["server_status"] = "restarted"
            else:
                print(f"[{email}] ⚠️ 启动失败或超时")
                shot = f"warn_{email.replace('@','_')}_{int(time.time())}.png"
                sb.save_screenshot(shot)
                tg_notify_photo_sync(shot, caption=f"⚠️ 启动失败或超时\n账号: <code>{email}</code>")
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
