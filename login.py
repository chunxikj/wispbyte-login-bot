import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

LOGIN_URL = "https://wispbyte.com/client/servers"

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
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled"
        ])
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
                # ── 原始登录逻辑（完全不变）──
                print(f"[{email}] 尝试 {attempt + 1}: 打开登录页...")
                await page.goto(LOGIN_URL, wait_until="load", timeout=90000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                if "client" in page.url and "login" not in page.url.lower():
                    print(f"[{email}] 已登录！当前URL: {page.url}")
                    result["success"] = True
                else:
                    await page.wait_for_selector(
                        'input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"], input[type="text"]',
                        timeout=20000
                    )
                    await page.fill('input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"], input[type="text"]', email)
                    await page.fill('input[placeholder*="Password"], input[type="password"]', password)

                    try:
                        await page.wait_for_selector('text=确认您是真人, input[type="checkbox"]', timeout=10000)
                        await page.click('text=确认您是真人')
                        await asyncio.sleep(3)
                    except:
                        pass

                    await page.click('button:has-text("Log In")')
                    await page.wait_for_url("**/client**", timeout=30000)
                    result["success"] = True
                    print(f"[{email}] 登录成功！当前URL: {page.url}")

                # ── 新增：检查服务器列表页状态 ──
                # 用固定等待替代 networkidle，避免 WebSocket 导致永远等不完
                print(f"[{email}] 等待列表页渲染（8秒）...")
                await asyncio.sleep(8)
                print(f"[{email}] 当前URL: {page.url}")

                # 截图确认页面内容（调试用，成功后可删除）
                debug_shot = f"list_{email.replace('@','_')}.png"
                await page.screenshot(path=debug_shot, full_page=True)
                await tg_notify_photo(debug_shot, caption=f"📋 列表页截图\n账号: <code>{email}</code>\nURL: {page.url}")

                # 读取服务器列表页的状态
                status_els = await page.query_selector_all('.server-status-text')
                list_statuses = []
                for el in status_els:
                    t = (await el.inner_text()).strip().lower()
                    list_statuses.append(t)
                print(f"[{email}] 列表页服务器状态: {list_statuses}")

                # 如果找不到状态元素，说明页面可能没有正确加载
                if not list_statuses:
                    raise Exception("找不到 .server-status-text 元素，页面可能未正确加载")

                has_offline = any("offline" in s for s in list_statuses)

                if not has_offline:
                    print(f"[{email}] ✅ 所有服务器在线，无需操作")
                    result["server_status"] = "already_online"
                    break

                # ── 有离线服务器，点击 Manage Server ──
                print(f"[{email}] 检测到离线服务器，寻找 Manage Server 按钮...")

                manage_btn = None

                # 方法1：找 .server-action-btn.primary
                try:
                    manage_btn = await page.query_selector('.server-action-btn.primary')
                    if manage_btn:
                        print(f"[{email}] 找到 .server-action-btn.primary")
                except:
                    manage_btn = None

                # 方法2：找文字
                if not manage_btn:
                    try:
                        manage_btn = await page.query_selector('button:has-text("Manage Server")')
                        if manage_btn:
                            print(f"[{email}] 找到 Manage Server 文字按钮")
                    except:
                        manage_btn = None

                if not manage_btn:
                    screenshot = f"debug_{email.replace('@','_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(screenshot, caption=f"🔍 调试截图\n账号: <code>{email}</code>\n找不到 Manage Server 按钮\nURL: {page.url}")
                    raise Exception(f"找不到 Manage Server 按钮，URL: {page.url}")

                print(f"[{email}] 点击 Manage Server 进入 Console...")
                await manage_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(5)
                print(f"[{email}] ✅ 已进入 Console 页，URL: {page.url}")

                # ── Console 页确认状态 ──
                print(f"[{email}] 读取 Console 页服务器状态...")
                status_el = await page.wait_for_selector('#online-status-text', timeout=20000)
                status_text = (await status_el.inner_text()).strip()
                print(f"[{email}] Console 页状态: [{status_text}]")

                if status_text.lower() == "online":
                    print(f"[{email}] ✅ 服务器在线，无需操作")
                    result["server_status"] = "already_online"
                    break

                # ── 离线则点击 Start ──
                print(f"[{email}] 服务器 [{status_text}]，点击 Start 启动...")
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
                    screenshot = f"warn_{email.replace('@','_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(
                        screenshot,
                        caption=f"⚠️ Wispbyte 启动超时\n账号: <code>{email}</code>\n已点击 Start 但60秒内未变为 Online"
                    )
                    result["server_status"] = "restart_timeout"
                break

            except Exception as e:
                print(f"[{email}] 第 {attempt + 1} 次失败: {e}")
                result["reason"] = str(e)[:200]
                # 每次失败都截图
                try:
                    screenshot = f"error_{email.replace('@','_')}_{attempt + 1}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(
                        screenshot,
                        caption=f"❌ 第{attempt + 1}次失败\n账号: <code>{email}</code>\n错误: <i>{str(e)[:200]}</i>\nURL: {page.url}"
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
    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        await tg_notify("❌ Failed: 未配置任何账号")
        return

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    if not accounts:
        await tg_notify("❌ Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password")
        return

    tasks = [login_one(email, pwd) for email, pwd in (acc.split(":", 1) for acc in accounts)]
    results = await asyncio.gather(*tasks)

    end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    final_msg = build_report(results, start_time, end_time)
    await tg_notify(final_msg)
    print(final_msg)

if __name__ == "__main__":
    accounts = os.getenv('LOGIN_ACCOUNTS', '').strip()
    count = len([a for a in accounts.split(',') if ':' in a]) if accounts else 0
    print(f"[{datetime.now()}] login.py 开始运行", file=sys.stderr)
    print(f"Python: {sys.version.split()[0]}, 有效账号数: {count}", file=sys.stderr)
    asyncio.run(main())
