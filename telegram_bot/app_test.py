import logging
import pytz
import pymysql
import pymysql.cursors
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from dateutil.parser import parse

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

# Ваш токен
TELEGRAM_BOT_TOKEN = '7409491209:AAGqHBwBc5C_7KdRT4VcR98zz53BV0QlJis'

# Определение временной зоны
timezone = pytz.timezone('Europe/Moscow')

# Настройка планировщика
scheduler = BackgroundScheduler(timezone=timezone)

# Подключение к базе данных
def connect_db():
    import json
    import os
    
    host = 'localhost'
    port = 3306
    user = 'cz40394_bz'
    password = 't13ypq2ksh7k9mb'
    database = 'cz40394_bz'
    
    # Ищем config.json относительно папки, в которой лежит этот скрипт
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                db_conf = config.get("mysql", {})
                host = db_conf.get("host", host)
                user = db_conf.get("user", user)
                password = db_conf.get("password", password)
                database = db_conf.get("database", database)
        except Exception as e:
            logging.error(f"Error reading config.json in connect_db: {e}")
            
    if ":" in host:
        parts = host.split(":")
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            pass

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )



def start(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    context.bot.send_message(chat_id=chat_id, text=f'Your chat ID is: {chat_id}')
    print(f"Сообщение отправлено в chat_id: {chat_id}")

def extract_city_from_chat_title(chat_title: str) -> str:
    # Предполагается, что город в чате указывается после "Бери Заряд "
    return chat_title.replace("Бери Заряд ", "").strip()

def main():
    # Удаляем use_context=True — в v20+ он не нужен
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('start', start))
    # dispatcher.add_handler(CommandHandler('weekly', weekly_report))  # Закомментировано, как в вашем коде
    dispatcher.add_handler(CommandHandler('daily', daily_report))
    dispatcher.add_handler(CommandHandler('today', today_report))
    dispatcher.add_handler(CommandHandler('today_device', device_report))
    # dispatcher.add_handler(CommandHandler('monthly', monthly_report))  # Закомментировано
    # dispatcher.add_handler(CommandHandler('data', handle_date_command))  # Закомментировано
    updater.dispatcher.add_handler(CallbackQueryHandler(button_handler))

    bot = updater.bot

    # Список id чатов
    CHAT_IDS = ['-1002096991177', '-4209357394', '-4242855451', '-4124296716', '-4230733244', '-4200653371', '-4130324255', '-4532023258', '-4280949216', '-4879884450', '-4835681314']

    # Планировщик (оставляем как есть, но учтите, что на сервере polling может не работать корректно)
    scheduler.start()
    scheduler.remove_all_jobs()
    scheduler.add_job(send_daily_message, 'cron', hour=8, minute=30, args=[bot, CHAT_IDS], id='daily_job')
    # scheduler.add_job(send_weekly_message, 'cron', day_of_week='mon', hour=8, minute=30, args=[bot, CHAT_IDS], id='weekly_job')
    # scheduler.add_job(send_monthly_message, 'cron', day='last', hour=23, minute=58, args=[bot, CHAT_IDS], id='monthly_job')

    # Запуск polling (для тестирования локально; на сервере лучше webhook)
    updater.start_polling(bootstrap_retries=0)
    updater.idle()

def generate_date_buttons(base_date):
    #Генерирует кнопки для выбора даты
    buttons = []
    for i in range(-2, 1):  # последние 3 дня
        date = base_date + timedelta(days=i)
        buttons.append(
            [InlineKeyboardButton(date.strftime('%d.%m.%Y'), callback_data=date.strftime('%Y-%m-%d'))]
        )
    return buttons

def get_data_for_city(city: str, start_date: datetime, end_date: datetime):
    conn = connect_db()

    try:
        with conn.cursor() as cursor:
            query = '''
            SELECT city, action, COUNT(*) as count
            FROM post_data
            WHERE date BETWEEN %s AND %s
            AND city LIKE %s
            GROUP BY city, action
            '''

            cursor.execute(query, (start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y'), f"%{city}%"))
            results = cursor.fetchall()
    finally:
        conn.close()

    city_data = {}
    for row in results:
        if row['city'] not in city_data:
            city_data[row['city']] = {
                'load_unload': 0,
                'service_requests': 0,
                'hubex_tasks': 0,
                'turn_on': 0,
                'accounting': 0,
                'total': 0
            }
        city_data[row['city']]['total'] += row['count']
        if row['action'] in ["Пополнение", "Выгрузка", "Разгрузка аппарата"]:
            city_data[row['city']]['load_unload'] += row['count']
        elif row['action'] in ["Сервисная заявка"]:
            city_data[row['city']]['service_requests'] += row['count']
        elif row['action'] in ["Звонок на локацию для включения аппарата"]:
            city_data[row['city']]['turn_on'] += row['count']
        elif row['action'] in ["Hubex задача"]:
            city_data[row['city']]['hubex_tasks'] += row['count']
        elif row['action'] in ["Аккаунтинг"]:
            city_data[row['city']]['accounting'] += row['count']

    return city_data

def generate_message(city_data: dict, start_date: datetime, end_date: datetime) -> str:
    """
    Генерирует текст сообщения с итогами работы за период.
    """
    total_tasks = sum(data['total'] for data in city_data.values())
    if start_date == end_date:
        message = f"За {start_date.strftime('%d.%m.%Y')} было выполнено {total_tasks} задач.\n"
    else:
        message = f"За период с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')} было выполнено {total_tasks} задач.\n"

    for city, data in city_data.items():
        message += (f"{city} - пополнение/разгрузка: {data['load_unload']}, "
                    f"сервисные: {data['service_requests']}, "
                    #f"Hubex: {data['hubex_tasks']}, "
                    f"аккаунтинг: {data['accounting']}.\n")
                   #f"звонков на локацию для включения аппарата: {data['turn_on']}. В общем {data['total']}.\n")

    return message

def handle_date_command(update: Update, context: CallbackContext) -> None:
    try:
        chat_title = update.message.chat.title
        city = extract_city_from_chat_title(chat_title)
        if city is None:
            logging.error(f"Failed to extract city from chat title: {chat_title}")
            return
        
        # Получаем даты из команды
        if len(context.args) not in [1, 2]:
            context.bot.send_message(chat_id=update.message.chat_id, text="Используй формат: /data ГГГГ-ММ-ДД или /data ГГГГ-ДД-ММ ГГГГ-ДД-ММ")
            return
        
        start_date_str = context.args[0]
        end_date_str = context.args[1] if len(context.args) == 2 else start_date_str
        
        start_date = parse(start_date_str)
        end_date = parse(end_date_str)
        
        if start_date > end_date:
            context.bot.send_message(chat_id=update.message.chat_id, text="Начальная дата должна быть перед конечной.")
            return

        city_data = get_data_for_city(city, start_date, end_date)
        if city_data:
            message = generate_message(city_data, start_date, end_date)
            context.bot.send_message(chat_id=update.message.chat_id, text=message)
        else:
            context.bot.send_message(chat_id=update.message.chat_id, text=f"Не найдено данных для: {city}")
    except Exception as e:
        logging.error(f"Error handling date command: {e}")
        context.bot.send_message(chat_id=update.message.chat_id, text="Неизвестная ошибка. Пожалуйста, проверьте формат даты и попробуйте еще раз")

def get_daily_data_for_city(city: str, date: datetime):
    conn = connect_db()

    try:
        with conn.cursor() as cursor:
            query = '''
            SELECT city, action, COUNT(*) as count
            FROM post_data
            WHERE date = %s
            AND city LIKE %s
            GROUP BY city, action
            '''

            cursor.execute(query, (date.strftime('%d.%m.%Y'), f"%{city}%"))
            results = cursor.fetchall()
    finally:
        conn.close()

    city_data = {}
    for row in results:
        if row['city'] not in city_data:
            city_data[row['city']] = {
                'load_unload': 0,
                'service_requests': 0,
                'hubex_tasks': 0,
                'turn_on': 0,
                'rebranding': 0,
                'accounting': 0,
                'total': 0
            }
        city_data[row['city']]['total'] += row['count']
        if row['action'] in ["Пополнение", "Выгрузка", "Разгрузка аппарата"]:
            city_data[row['city']]['load_unload'] += row['count']
        elif row['action'] in ["Сервисная заявка", "Hubex заявка"]:
            city_data[row['city']]['service_requests'] += row['count']
        elif row['action'] in ["Hubex задача"]:
            city_data[row['city']]['hubex_tasks'] += row['count']
        elif row['action'] in ["Звонок на локацию для включения аппарата"]:
            city_data[row['city']]['turn_on'] += row['count']
        elif row['action'] in ["Обклейка. Сделал в офисе", "Обклейка. Сделал на локации"]:
            city_data[row['city']]['rebranding'] += row['count']
        elif row['action'] in ["Аккаунтинг"]:
            city_data[row['city']]['accounting'] += row['count']

    return city_data

def generate_daily_message(city_data: dict, date: str) -> str:
    """
    Генерирует текст сообщения с ежедневными итогами и расчетом выплат.
    """
    total_tasks = sum(data['total'] for data in city_data.values())
    total_payment = 0
    message = f"За {date} было выполнено {total_tasks} задач.\n"

    for city, data in city_data.items():
        # Определяем коэффициенты для каждого города в зависимости от общего количества задач
        if data['total'] <= 20:
            payment_service_requests = data['service_requests'] * 100
            payment_load_unload = data['load_unload'] * 50
            payment_turn_on = data['turn_on'] * 50
            payment_rebranding = data['rebranding'] * 100
        elif 21 <= data['total'] <= 30:
            payment_service_requests = data['service_requests'] * 120
            payment_load_unload = data['load_unload'] * 60
            payment_turn_on = data['turn_on'] * 60
            payment_rebranding = data['rebranding'] * 120
        else:  # data['total'] >= 31
            payment_service_requests = data['service_requests'] * 130
            payment_load_unload = data['load_unload'] * 65
            payment_turn_on = data['turn_on'] * 65
            payment_rebranding = data['rebranding'] * 130

        # Считаем общую выплату для текущего города
        city_payment = (payment_service_requests + payment_load_unload +
                        payment_turn_on + payment_rebranding)
        total_payment += city_payment

    # Формируем сообщение для текущего города
    for city, data in city_data.items():
        message += (f"{city} - пополнение/разгрузка: {data['load_unload']}, "
                    f"сервисные: {data['service_requests']}, "
                    f"Hubex: {data['hubex_tasks']}, "
                    f"ребрендинг станций: {data['rebranding']}, "
                    f"аккаунтинг: {data['accounting']}, "
                    f"звонков на локацию для включения аппарата: {data['turn_on']}. В общем {data['total']}.\n")

    return message


def send_daily_message(bot, chat_ids):
    for chat_id in chat_ids:
        try:
            chat = bot.get_chat(chat_id)
            if chat is None:
                logging.error(f"Failed to get chat with id: {chat_id}")
                return
            chat_title = chat.title

            city = extract_city_from_chat_title(chat_title)
            if city is None:
                logging.error(f"Failed to extract city from chat title: {chat_title}")
                return

            # Получаем данные за вчерашний день
            base_date = datetime.now() - timedelta(days=1)
            city_data = get_daily_data_for_city(city, base_date)

            if city_data:
                daily_message = generate_daily_message(city_data, base_date.strftime('%d.%m.%Y'))
                if daily_message is None:
                    logging.error("Generated message is None")
                    return
                
                # Генерация кнопок для выбора даты
                keyboard = generate_date_buttons(base_date)
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                bot.send_message(chat_id=chat_id, text=daily_message, reply_markup=reply_markup)
                logging.info("Daily message sent successfully.")
            else:
                bot.send_message(chat_id=chat_id, text=f"За вчерашний день не было выполнено задач по городу {city}")
        except Exception as e:
            logging.error(f"Error sending daily message: {e}")

def get_weekly_data_for_city(city: str):
    conn = connect_db()

    try:
        with conn.cursor() as cursor:
            start_date = datetime.now() - timedelta(days=7)
            end_date = datetime.now()

            query = '''
            SELECT city, action, COUNT(*) as count
            FROM post_data
            WHERE date BETWEEN %s and %s
            AND city LIKE %s
            GROUP BY city, action
            '''

            cursor.execute(query, (start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y'), f"%{city}%"))
            results = cursor.fetchall()
    finally:
        conn.close()

    city_data = {}
    for row in results:
        if row['city'] not in city_data:
            city_data[row['city']] = {
                'load_unload': 0,
                'service_requests': 0,
                'turn_on': 0,
                'accounting': 0,
                'total': 0
            }
        city_data[row['city']]['total'] += row['count']
        if row['action'] in ["Пополнение", "Выгрузка", "Разгрузка аппарата"]:
            city_data[row['city']]['load_unload'] += row['count']
        elif row['action'] in ["Сервисная заявка", "Hubex заявка"]:
            city_data[row['city']]['service_requests'] += row['count']
        elif row['action'] in ["Звонок на локацию для включения аппарата"]:
            city_data[row['city']]['turn_on'] += row['count']
        elif row['action'] in ["Аккаунтинг"]:
            city_data[row['city']]['accounting'] += row['count']

    return city_data

def generate_weekly_message(city_data: dict, date: str) -> str:
    """
    Генерирует текст сообщения с еженедельными итогами.
    """
    start_date = datetime.now() - timedelta(days=7)
    end_date = datetime.now()
    total_tasks = sum(data['total'] for data in city_data.values())
    message = f"За предыдущую неделю {start_date.strftime('%d.%m.%Y')}-{end_date.strftime('%d.%m.%Y')} было выполнено {total_tasks} задач.\n"

    for city, data in city_data.items():
        message += (f"{city} - пополнение/разгрузка: {data['load_unload']}, "
                    f"сервисные: {data['service_requests']}, "
                    f"аккаунтинг: {data['accounting']}, "
                    f"звонков на локацию для включения аппарата: {data['turn_on']}. В общем {data['total']}.\n")

    return message


def send_weekly_message(bot, chat_ids):
  # Отправляем данные по чатам
  for chat_id in chat_ids:
    try:
        chat = bot.get_chat(chat_id)
        if chat is None:
           logging.error(f"Failed to get chat with id: {chat_id}")
           return
        chat_title = chat.title
        city = extract_city_from_chat_title(chat_title)
        if city is None:
            logging.error(f"Failed to extract city from chat title: {chat_title}")
            return
        city_data = get_weekly_data_for_city(city)

        if city_data:
            message = generate_weekly_message(city_data, datetime.now())
            if message is None:
                logging.error("Generated message if None")
                return
            bot.send_message(chat_id=chat_id, text=message)
            logging.info("Weekly message sent successfully.")
        else:
            logging.info(f"No data found for city: {city}")
    except Exception as e:
        logging.error(f"Error sending weekly message: {e}")

def get_monthly_data_for_city(city: str):
    conn = connect_db()

    try:
        with conn.cursor() as cursor:
            start_date = datetime.now().replace(day=1)
            end_date = datetime.now()

            query = '''
            SELECT city, action, COUNT(*) as count
            FROM post_data
            WHERE date BETWEEN %s and %s
            AND city LIKE %s
            GROUP BY city, action
            '''

            cursor.execute(query, (start_date.strftime('%d.%m.%Y'), end_date.strftime('%d.%m.%Y'), f"%{city}%"))
            results = cursor.fetchall()
    finally:
        conn.close()

    city_data = {}
    for row in results:
        if row['city'] not in city_data:
            city_data[row['city']] = {
                'load_unload': 0,
                'service_requests': 0,
                'turn_on': 0,
                'accounting': 0,
                'total': 0
            }
        city_data[row['city']]['total'] += row['count']
        if row['action'] in ["Пополнение", "Выгрузка", "Разгрузка аппарата"]:
            city_data[row['city']]['load_unload'] += row['count']
        elif row['action'] in ["Сервисная заявка", "Hubex заявка"]:
            city_data[row['city']]['service_requests'] += row['count']
        elif row['action'] in ["Звонок на локацию для включения аппарата"]:
            city_data[row['city']]['turn_on'] += row['count']
        elif row['action'] in ["Аккаунтинг"]:
            city_data[row['city']]['accounting'] += row['count']

    return city_data

def generate_monthly_message(city_data: dict, date: str) -> str:
    """
    Генерирует текст сообщения с ежемесячными итогами.
    """
    start_date = datetime.now().replace(day=1)
    end_date = datetime.now()
    total_tasks = sum(data['total'] for data in city_data.values())
    message = f"За текущий месяц было выполнено {total_tasks} задач с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}.\n"

    for city, data in city_data.items():
        message += (f"{city} - пополнение/разгрузка: {data['load_unload']}, "
                    f"сервисные: {data['service_requests']}, "
                    f"аккаунтинг: {data['accounting']}, "
                    f"звонков на локацию для включения аппарата: {data['turn_on']}. В общем {data['total']}.\n")

    return message


def send_monthly_message(bot, chat_ids):
  for chat_id in chat_ids:
    try:
        chat = bot.get_chat(chat_id)
        if chat is None:
           logging.error(f"Failed to get chat with id: {chat_id}")
           return
        chat_title = chat.title
        city = extract_city_from_chat_title(chat_title)
        if city is None:
            logging.error(f"Failed to extract city from chat title: {chat_title}")
            return
        city_data = get_monthly_data_for_city(city)

        if city_data:
            message = generate_monthly_message(city_data, datetime.now())
            if message is None:
                logging.error("Generated message if None")
                return
            bot.send_message(chat_id=chat_id, text=message)
            logging.info("Monthly message sent successfully.")
        else:
            logging.info(f"No data found for city: {city}")
    except Exception as e:
        logging.error(f"Error sending Monthly message: {e}")


def monthly_report(update: Update, context: CallbackContext) -> None:
    chat_title = update.message.chat.title
    city = extract_city_from_chat_title(chat_title)
    if city is None:
            logging.error(f"Failed to extract city from chat title: {chat_title}")
            return

    city_data = get_monthly_data_for_city(city)
    if city_data:
        monthly_message = generate_monthly_message(city_data, datetime.now())
        context.bot.send_message(chat_id=update.message.chat_id, text=monthly_message)
    else:
        context.bot.send_message(chat_id=update.message.chat_id, text=f"За текущий месяц не было выполнено задач по городу {city}")

def weekly_report(update: Update, context: CallbackContext) -> None:
    chat_title = update.message.chat.title
    city = extract_city_from_chat_title(chat_title)
    if city is None:
            logging.error(f"Failed to extract city from chat title: {chat_title}")
            return

    city_data = get_weekly_data_for_city(city)
    if city_data:
        message = generate_weekly_message(city_data, datetime.now())
        context.bot.send_message(chat_id=update.message.chat_id, text=message)
    else:
        context.bot.send_message(chat_id=update.message.chat_id, text=f"За прошедшую неделю не было выполнено задач по городу {city}")

def daily_report(update, context):
    chat_title = update.message.chat.title
    city = extract_city_from_chat_title(chat_title)
    if city is None:
        logging.error(f"Failed to extract city from chat title: {chat_title}")
        return

    base_date = datetime.now() - timedelta(days=1)
    city_data = get_daily_data_for_city(city, base_date)
    if city_data:
        message = generate_daily_message(city_data, base_date.strftime('%d.%m.%Y'))
        if message is None:
            logging.error("Generated message is None")
            return
        
        keyboard = generate_date_buttons(base_date)
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.bot.send_message(chat_id=update.message.chat_id, text=message, reply_markup=reply_markup)
    else:
        context.bot.send_message(chat_id=update.message.chat_id, text=f"За вчерашний день не было выполнено задач по городу {city}")

def button_handler(update, context):
    query = update.callback_query
    query.answer()
    
    # Если нажата кнопка для получения данных по аппаратам
    if query.data == "get_device_data":
        device_report(update, context)
        return

    # Остальная логика для обработки других кнопок
    selected_date = datetime.strptime(query.data, '%Y-%m-%d')
    city = extract_city_from_chat_title(query.message.chat.title)
    
    city_data = get_daily_data_for_city(city, selected_date)
    
    if city_data:
        message = generate_daily_message(city_data, selected_date.strftime('%d.%m.%Y'))
        query.edit_message_text(text=message)

        # Генерация кнопок для выбора даты
        keyboard = generate_date_buttons(selected_date)
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Обновляем разметку клавиатуры
        query.edit_message_reply_markup(reply_markup=reply_markup)
    else:
        query.edit_message_text(text=f"За {selected_date.strftime('%d.%m.%Y')} не было выполнено задач по городу {city}")
        
        # Генерация кнопок для выбора даты
        keyboard = generate_date_buttons(selected_date)
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Обновляем разметку клавиатуры
        query.edit_message_reply_markup(reply_markup=reply_markup)

def today_report(update: Update, context: CallbackContext) -> None:
    chat_title = update.message.chat.title
    city = extract_city_from_chat_title(chat_title)
    if city is None:
        logging.error(f"Failed to extract city from chat title: {chat_title}")
        return

    city_data = get_daily_data_for_city(city, datetime.now())
    if city_data:
        message = generate_daily_message(city_data, datetime.now().strftime('%d.%m.%Y'))
        context.bot.send_message(chat_id=update.message.chat_id, text=message)

        # Генерация кнопки для получения данных по аппаратам
        keyboard = [[InlineKeyboardButton("Получить данные по аппаратам", callback_data="get_device_data")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        context.bot.send_message(chat_id=update.message.chat_id, text="Выберите действие:", reply_markup=reply_markup)
    else:
        context.bot.send_message(chat_id=update.message.chat_id, text=f"За текущий день не было выполнено задач по городу {city}")


def get_device_data_for_city(city: str, date: datetime):
    conn = connect_db()

    try:
        with conn.cursor() as cursor:
            query = '''
            SELECT id, action
            FROM post_data
            WHERE date = %s
            AND city LIKE %s
            '''

            cursor.execute(query, (date.strftime('%d.%m.%Y'), f"%{city}%"))
            results = cursor.fetchall()
    finally:
        conn.close()

    device_data = {}
    for row in results:
        device_id = row['id']
        action = row['action']

        if device_id not in device_data:
            device_data[device_id] = []
        device_data[device_id].append(action)

    return device_data


def generate_device_message(device_data: dict):
    if not device_data:
        return "За текущий день не было выполнено задач по аппаратам."

    message = "За текущий день были выполнены задачи по аппаратам:\n"
    for device_id, actions in device_data.items():
        actions_list = ', '.join(actions)
        message += f"{device_id} - {actions_list}\n"

    message += f"Итого было выполнено задач по {len(device_data)} станциям."
    return message

def device_report(update: Update, context: CallbackContext) -> None:
    chat_id = update.callback_query.message.chat.id  # Получаем ID чата из callback_query
    chat_title = update.callback_query.message.chat.title  # Получаем название чата
    city = extract_city_from_chat_title(chat_title)
    
    if city is None:
        logging.error(f"Failed to extract city from chat title: {chat_title}")
        return

    device_data = get_device_data_for_city(city, datetime.now())
    message = generate_device_message(device_data)
    context.bot.send_message(chat_id=chat_id, text=message)


if __name__ == '__main__':
    main()
