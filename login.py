import os
import sys
import asyncio
import aiohttp
import subprocess
import time
from datetime import datetime
from playwright.async_api import async_playwright

LOGIN_URL = "https://wispbyte.com/client/servers"
GOST_LOCAL_PORT = 18080  # gost 本地 HTTP 代理端口

async def tg_notify(message: str):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("Warning: 未设置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
        except Exception as e:
            print(f"Warning: Telegram 消息发送失败: {e}")

async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                if caption:
                    data.add_field("caption", caption)
                    data.add_field("parse_mode", "HTML")
                await session.post(url, data=data)
        except Exception as e:
            print(f"Warning: Telegram 图片发送失败: {e}")
        finally:
            try:
                os.remove(photo_path)
            except:
                pass

def start_gost(socks5_proxy: str) -> subprocess.Popen:
    """
    启动 gost，将本地 HTTP 代理转发到 SOCKS5
    socks5_proxy 格式: socks5://user:pass@host:port
    本地监听: 127.0.0.1:18080 (HTTP代理)
    Chrome 不支持带认证的 SOCKS5，必须通过 gost 中转
    """
    cmd = [
        "gost",
        f"-L=http://127.0.0.1:{GOST_LOCAL_PORT}",
        f"-F={socks5_proxy}"
    ]
    print(f"[gost] 启动命令: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)  # 等待 gost 启动
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode()
        raise RuntimeError(f"gost 启动失败: {stderr}")
    print(f"[gost] 启动成功，PID: {proc.pid}，本地代理: http://127.0.0.1:{GOST_LOCAL_PORT}")
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

async def login_one(email: str, password: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
                # 强制所有流量走 gost 本地 HTTP 代理
                f"--proxy-server=http://127.0.0.1:{GOST_LOCAL_PORT}",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(90000)

        result = {"email": email, "success": False, "server_status": None, "reason": ""}
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                print(f"[{email}] 尝试 {attempt + 1}: 打开登录页...")
                await page.goto(LOGIN_URL, wait_until="load", timeout=90000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                # 用页面元素判断是否需要登录
                login_form = await page.query_selector('input[type="email"], input[placeholder*="Email"]')
                need_login = login_form is not None
                print(f"[{email}] 是否需要登录: {need_login}，当前URL: {page.url}")

                if need_login:
                    print(f"[{email}] 检测到登录表单，开始填写...")
                    await login_form.fill(email)
                    await page.fill('input[placeholder*="Password"], input[type="password"]', password)
                    print(f"[{email}] 已填写账号密码，等待 Turnstile 验证（最多30秒）...")

                    # 等待 Turnstile 自动完成
                    turnstile_done = False
                    for i in range(30):
                        await asyncio.sleep(1)
                        try:
                            token_val = await page.evaluate(
                                'document.querySelector(\'input[name="cf-turnstile-response"]\')?.value || ""'
                            )
                            if token_val and len(token_val) > 10:
                                print(f"[{email}] ✅ Turnstile 验证完成（第{i+1}秒）")
                                turnstile_done = True
                                break
                        except:
                            pass

                    if not turnstile_done:
                        # 尝试点击 Turnstile iframe 内的 checkbox
                        print(f"[{email}] token未生成，尝试点击 Turnstile checkbox...")
                        try:
                            for frame in page.frames:
                                if "challenges.cloudflare.com" in frame.url:
                                    cb = await frame.query_selector('input[type="checkbox"]')
                                    if cb:
                                        await cb.click()
                                        print(f"[{email}] 已点击 Turnstile checkbox，再等5秒...")
                                        await asyncio.sleep(5)
                                        break
                        except Exception as te:
                            print(f"[{email}] 点击 Turnstile 失败: {te}")

                    await page.click('button:has-text("Log In")')
                    print(f"[{email}] 已点击登录，等待跳转（8秒）...")
                    await asyncio.sleep(8)
                    print(f"[{email}] 登录后URL: {page.url}")

                    # 检查是否还在登录页
                    still_login = await page.query_selector('input[type="email"], input[placeholder*="Email"]')
                    if still_login:
                        shot = f"error_login_{email.replace('@','_')}_{attempt+1}.png"
                        await page.screenshot(path=shot, full_page=True)
                        await tg_notify_photo(shot, caption=f"❌ 第{attempt+1}次登录失败\n账号: <code>{email}</code>\nTurnstile未通过\nURL: {page.url}")
                        raise Exception("登录失败，仍停留在登录页（Turnstile未通过）")

                    print(f"[{email}] ✅ 登录成功！")
                else:
                    print(f"[{email}] ✅ 已有登录态，无需重新登录")

                result["success"] = True

                # ── 等待列表页内容渲染 ──
                print(f"[{email}] 等待服务器列表渲染（8秒）...")
                await asyncio.sleep(8)

                # 读取服务器状态
                status_els = await page.query_selector_all('.server-status-text')
                list_statuses = []
                for el in status_els:
                    t = (await el.inner_text()).strip().lower()
                    list_statuses.append(t)
                print(f"[{email}] 列表页服务器状态: {list_statuses}")

                if not list_statuses:
                    shot = f"debug_list_{email.replace('@','_')}.png"
                    await page.screenshot(path=shot, full_page=True)
                    await tg_notify_photo(shot, caption=f"🔍 列表页调试截图\n找不到状态元素\nURL: {page.url}")
                    raise Exception("找不到 .server-status-text 元素，列表页未正确加载")

                has_offline = any("offline" in s for s in list_statuses)

                if not has_offline:
                    print(f"[{email}] ✅ 所有服务器在线，无需操作")
                    result["server_status"] = "already_online"
                    break

                # ── 有离线服务器，点击 Manage Server ──
                print(f"[{email}] 检测到离线服务器，寻找 Manage Server 按钮...")
                manage_btn = (
                    await page.query_selector('.server-action-btn.primary') or
                    await page.query_selector('button:has-text("Manage Server")')
                )

                if not manage_btn:
                    shot = f"debug_manage_{email.replace('@','_')}.png"
                    await page.screenshot(path=shot, full_page=True)
                    await tg_notify_photo(shot, caption=f"🔍 找不到 Manage Server 按钮\nURL: {page.url}")
                    raise Exception("找不到 Manage Server 按钮")

                print(f"[{email}] 点击 Manage Server 进入 Console...")
                await manage_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(5)
                print(f"[{email}] ✅ 已进入 Console 页，URL: {page.url}")

                # ── Console 页确认状态 ──
                status_el = await page.wait_for_selector('#online-status-text', timeout=20000)
                status_text = (await status_el.inner_text()).strip()
                print(f"[{email}] Console 页状态: [{status_text}]")

                if status_text.lower() == "online":
                    print(f"[{email}] ✅ 服务器在线，无需操作")
                    result["server_status"] = "already_online"
                    break

                # ── 离线则点击 Start ──
                print(f"[{email}] 服务器离线，点击 Start 启动...")
                start_btn = await page.wait_for_selector('#start-btn', timeout=10000)
                await start_btn.click()
                print(f"[{email}] 已点击 Start，等待启动（最多60秒）...")

                try:
                    await page.wait_for_function(
                        'document.getElementById("online-status-text")?.textContent?.trim().toLowerCase() === "online"',
                        timeout=60000
                    )
                    print(f"[{email}] ✅ 服务器已成功启动！")
                    result["server_status"] = "restarted"
                except:
                    print(f"[{email}] ⚠️ 60秒内未变为 Online")
                    shot = f"warn_{email.replace('@','_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=shot, full_page=True)
                    await tg_notify_photo(shot, caption=f"⚠️ 启动超时\n账号: <code>{email}</code>\n已点击 Start 但60秒内未变为 Online")
                    result["server_status"] = "restart_timeout"
                break

            except Exception as e:
                print(f"[{email}] 第 {attempt + 1} 次失败: {e}")
                result["reason"] = str(e)[:200]
                try:
                    shot = f"error_{email.replace('@','_')}_{attempt+1}.png"
                    await page.screenshot(path=shot, full_page=True)
                    await tg_notify_photo(
                        shot,
                        caption=f"❌ 第{attempt+1}次失败\n账号: <code>{email}</code>\n错误: <i>{str(e)[:200]}</i>\nURL: {page.url}"
                    )
                except:
                    pass

                if attempt < max_retries:
                    await context.close()
                    context = await browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                    )
                    page = await context.new_page()
                    page.set_default_timeout(90000)
                    await asyncio.sleep(2)

        await context.close()
        await browser.close()
        return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 启动 gost 代理
    socks5_proxy = os.getenv("SOCKS5_PROXY", "").strip()
    gost_proc = None
    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            print(f"[gost] 代理已启动，出口IP将使用 SOCKS5 节点")
        except Exception as e:
            print(f"[gost] 启动失败: {e}，将不使用代理继续运行")
            gost_proc = None
    else:
        print("[gost] 未配置 SOCKS5_PROXY，不使用代理")

    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        await tg_notify("❌ Failed: 未配置任何账号")
        return

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    if not accounts:
        await tg_notify("❌ Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password")
        return

    try:
        tasks = [login_one(email, pwd) for email, pwd in (acc.split(":", 1) for acc in accounts)]
        results = await asyncio.gather(*tasks)

        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_msg = build_report(results, start_time, end_time)
        await tg_notify(final_msg)
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
