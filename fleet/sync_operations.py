# -*- coding: utf-8 -*-
import os
import sys
import argparse
import logging
import glob
from datetime import datetime, timedelta
import pandas as pd
import pymysql

# Настройка вывода в консоль
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def load_config(config_path=None):
    if config_path is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))
        
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден: {config_path}")
        sys.exit(1)
    import json
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def fill_segment(page, label_contains, value):
    selector = f'span[aria-label*="{label_contains}"]'
    el = page.locator(selector)
    if el.count() > 0:
        el.click()
        el.press("Control+A")
        el.type(value)
    else:
        logger.warning(f"Сегмент даты '{label_contains}' не найден!")

def download_operations_report(park_name, park_id, start_dt, end_dt, headless=True):
    """
    Скачивает отчет по операциям из Yandex Fleet с помощью Playwright.
    Сохраняет в inputs/operations_{park_name}.csv
    """
    from playwright.sync_api import sync_playwright
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    inputs_dir = os.path.join(base_dir, "inputs")
    if not os.path.exists(inputs_dir):
        os.makedirs(inputs_dir)
        
    if start_dt == end_dt:
        date_suffix = start_dt.strftime("%Y-%m-%d")
    else:
        date_suffix = f"{start_dt.strftime('%Y-%m-%d')}_to_{end_dt.strftime('%Y-%m-%d')}"
    dest_path = os.path.join(inputs_dir, f"operations_{park_name}_{date_suffix}.csv")
    url = f"https://fleet.yandex.ru/snickers/reports?park_id={park_id}"
    
    logger.info(f"Начало выгрузки операций для парка: {park_name}")
    
    # Форматируем даты в сегменты
    start_day = start_dt.strftime("%d")
    start_month = start_dt.strftime("%m")
    start_year = start_dt.strftime("%Y")
    
    end_day = end_dt.strftime("%d")
    end_month = end_dt.strftime("%m")
    end_year = end_dt.strftime("%Y")
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            viewport={"width": 1280, "height": 800}
        )
        
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url)
            page.wait_for_timeout(4000)
            
            if "passport.yandex" in page.url:
                logger.error("Ошибка: Требуется авторизация в Яндекс. Запустите login_and_inspect.py вручную.")
                raise Exception("Необходима авторизация")
                
            # Кликаем на "Операции" в списке отчетов
            logger.info("Выбираем отчет 'Операции'...")
            operations_link = page.locator("text='Операции'")
            if operations_link.count() > 0:
                operations_link.first.click()
                page.wait_for_timeout(3000)
            else:
                logger.error("Раздел отчетов 'Операции' не найден на странице!")
                debug_tools_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debug_tools"))
                os.makedirs(debug_tools_dir, exist_ok=True)
                page.screenshot(path=os.path.join(debug_tools_dir, f"error_report_{park_name}.png"))
                raise Exception("Операции не найдены")
                
            # Вводим даты диапазона
            logger.info(f"Вводим период отчета: {start_day}.{start_month}.{start_year} - {end_day}.{end_month}.{end_year}")
            fill_segment(page, "день, Дата начала", start_day)
            fill_segment(page, "месяц, Дата начала", start_month)
            fill_segment(page, "год, Дата начала", start_year)
            
            fill_segment(page, "день, Дата окончания", end_day)
            fill_segment(page, "месяц, Дата окончания", end_month)
            fill_segment(page, "год, Дата окончания", end_year)
            page.wait_for_timeout(500)
            
            # Выбираем регион (по названию парка)
            logger.info(f"Выбираем регион: '{park_name}'...")
            region_input = page.locator('input[aria-describedby="Регионы"]')
            if region_input.count() > 0:
                region_input.click()
                region_input.press("Control+A")
                region_input.type(park_name)
                page.wait_for_timeout(1500)
                
                # Кликаем на опцию в выпадающем списке
                option = page.locator(f'[role="option"]:has-text("{park_name}")')
                if option.count() > 0:
                    option.first.click()
                else:
                    logger.warning(f"Опция '{park_name}' не найдена в выпадающем списке, пробуем Enter...")
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Enter")
            else:
                logger.warning("Поле выбора региона не найдено!")
                
            page.wait_for_timeout(1000)
            
            # Скачиваем отчет
            logger.info("Скачиваем отчет...")
            download_btn = page.locator('button:has-text("Скачать отчёт")')
            if download_btn.count() > 0:
                with page.expect_download(timeout=45000) as download_info:
                    download_btn.click()
                download = download_info.value
                download.save_as(dest_path)
                logger.info(f"Отчет успешно сохранен в: {dest_path}")
                return dest_path
            else:
                logger.error("Кнопка 'Скачать отчёт' не найдена!")
                page.screenshot(path=f"error_download_{park_name}.png")
                raise Exception("Кнопка скачивания не найдена")
                
        except Exception as e:
            logger.error(f"Исключение при выгрузке отчета {park_name}: {e}")
            raise e
        finally:
            context.close()

def connect_db(config):
    db_conf = config.get("mysql", {})
    host = db_conf.get("host", "localhost")
    port = db_conf.get("port", 3306)
    
    # Если порт указан через двоеточие в хосте, разделяем их
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
        user=db_conf.get("user", "cz40394_bz"),
        password=db_conf.get("password", ""),
        database=db_conf.get("database", "cz40394_bz"),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def sync_csv_to_mysql(config, csv_path):
    """
    Парсит выгруженный CSV-отчет по операциям и синхронизирует его с MySQL post_data.
    """
    if not os.path.exists(csv_path):
        logger.error(f"Файл для синхронизации не найден: {csv_path}")
        return 0
        
    df = pd.read_csv(csv_path)
    if df.empty:
        logger.info(f"Файл {csv_path} пустой.")
        return 0
        
    # Проверяем обязательные колонки
    required_cols = ['ID задачи', 'ФИО исполнителя', 'ID вендинга', 'Тип задачи', 'Итоговый статус', 'Дата итогового статуса']
    for col in required_cols:
        if col not in df.columns:
            logger.error(f"В файле отсутствует обязательная колонка: {col}")
            return 0
            
    # Фильтруем: только выполненные задачи
    df_done = df[df['Итоговый статус'] == 'Выполнена'].copy()
    if df_done.empty:
        logger.info("Нет выполненных задач в отчете.")
        return 0
        
    courier_mapping = config.get("courier_mapping", {})
    
    # Подключаемся к базе данных
    try:
        conn = connect_db(config)
    except Exception as e:
        logger.error(f"Не удалось подключиться к базе данных: {e}")
        return 0
        
    # 1. Предварительно загружаем все существующие Yandex Task ID из базы в память (в set) для оптимизации
    existing_task_ids = set()
    use_memory_cache = False
    try:
        logger.info("Загрузка существующих ID задач из базы данных для оптимизации...")
        with conn.cursor() as cursor:
            # Выбираем только те комментарии, которые содержат сигнатуру Yandex Task ID
            cursor.execute("SELECT comment FROM post_data WHERE comment LIKE '%Yandex Task ID:%'")
            rows = cursor.fetchall()
            for r in rows:
                comment_text = r.get('comment')
                if comment_text:
                    # Извлекаем ID задачи. Формат: "Yandex Task ID: <task_id> | ..."
                    try:
                        parts = comment_text.split('|')
                        if parts:
                            task_part = parts[0].replace("Yandex Task ID:", "").strip()
                            if task_part:
                                existing_task_ids.add(task_part)
                    except Exception:
                        pass
            logger.info(f"Успешно загружено {len(existing_task_ids)} уникальных ID задач в память.")
            use_memory_cache = True
    except Exception as ex:
        logger.warning(f"Не удалось предзагрузить ID задач ({ex}). Будет использована медленная проверка через БД.")

    inserted_count = 0
    skipped_count = 0
    
    try:
        with conn.cursor() as cursor:
            for _, row in df_done.iterrows():
                task_id = str(row['ID задачи']).strip()
                vending_id = int(float(str(row['ID vendors'] if 'ID vendors' in df.columns else row['ID вендинга']).strip()))
                courier_raw = str(row['ФИО исполнителя']).strip()
                task_type_raw = str(row['Тип задачи']).strip()
                region_raw = str(row['Название региона']).strip()
                date_status_raw = str(row['Дата итогового статуса']).strip()
                
                # Проверяем, есть ли уже этот Yandex Task ID в БД (в поле comment)
                if use_memory_cache:
                    if task_id in existing_task_ids:
                        skipped_count += 1
                        continue
                else:
                    # Резервный вариант: медленный запрос поштучно к БД
                    comment_signature = f"Yandex Task ID: {task_id}"
                    check_query = "SELECT number FROM post_data WHERE comment LIKE %s"
                    cursor.execute(check_query, (f"%{task_id}%",))
                    existing = cursor.fetchone()
                    if existing:
                        skipped_count += 1
                        continue
                    
                # 2. Маппинг исполнителя во Fleet -> город (курьера) в БД
                # Исполнитель должен записываться в колонку "city"
                mapped_courier = courier_mapping.get(courier_raw, f"{region_raw} {courier_raw}")
                
                # 3. Маппинг типа задачи во Fleet -> action в БД
                # "Загрузка аппарата" -> "Пополнение"
                # "Выгрузка аппарата" -> "Выгрузка"
                # Любое другое -> "Сервисная заявка"
                if task_type_raw == "Загрузка аппарата":
                    mapped_action = "Пополнение"
                elif task_type_raw == "Выгрузка аппарата":
                    mapped_action = "Выгрузка"
                else:
                    mapped_action = "Сервисная заявка"
                    
                # 4. Форматирование даты
                # Дата итогового статуса во Fleet имеет формат: 2026-06-22T14:56:11+03:00
                # В БД дата должна записываться строго как ДД.ММ.ГГГГ (например, 22.06.2026)
                try:
                    # Убираем таймзону для парсинга, если она мешает (или используем ISO парсер)
                    clean_date_str = date_status_raw.split('T')[0] # 2026-06-22
                    dt = datetime.strptime(clean_date_str, "%Y-%m-%d")
                    mapped_date = dt.strftime("%d.%m.%Y")
                except Exception as ex:
                    logger.warning(f"Не удалось распарсить дату '{date_status_raw}': {ex}. Используем вчерашнюю дату.")
                    mapped_date = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")
                    
                # 5. Запись остальных полей
                mapped_answer = "Выполнена через Яндекс.Про"
                mapped_comment = f"Yandex Task ID: {task_id} | Исполнитель: {courier_raw} ({task_type_raw})"
                mapped_url_photo = ""
                
                # 6. Вставка в БД
                insert_query = """
                INSERT INTO post_data (id, date, city, url_photo, action, answer, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                cursor.execute(insert_query, (
                    vending_id,
                    mapped_date,
                    mapped_courier,
                    mapped_url_photo,
                    mapped_action,
                    mapped_answer,
                    mapped_comment
                ))
                inserted_count += 1
                if use_memory_cache:
                    existing_task_ids.add(task_id)
                
        # Фиксируем изменения
        conn.commit()
        logger.info(f"Синхронизация файла {os.path.basename(csv_path)} завершена. Добавлено: {inserted_count}, Пропущено дубликатов: {skipped_count}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка транзакции при записи в БД для {csv_path}: {e}")
    finally:
        conn.close()
        
    return inserted_count

def main():
    parser = argparse.ArgumentParser(description="Синхронизация выполненных задач из Яндекс.Про во Fleet в БД MySQL")
    parser.add_argument("--date", default="yesterday", help="Синхронизировать конкретную дату в формате ГГГГ-ММ-ДД или 'yesterday'")
    parser.add_argument("--start-date", help="Начало диапазона дат в формате ГГГГ-ММ-ДД")
    parser.add_argument("--end-date", help="Конец диапазона дат в формате ГГГГ-ММ-ДД")
    parser.add_argument("--only-park", help="Синхронизировать только конкретный город (парк)")
    parser.add_argument("--headful", action="store_true", help="Запустить браузер в видимом режиме")
    parser.add_argument("--no-download", action="store_true", help="Не скачивать новые отчеты, только импортировать существующие файлы из папки inputs/")
    args = parser.parse_args()
    config = load_config()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    inputs_dir = os.path.join(base_dir, "inputs")

    # Определение диапазона дат
    if args.start_date and args.end_date:
        start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(args.end_date, "%Y-%m-%d")
    elif args.date == "yesterday":
        yesterday = datetime.now() - timedelta(days=1)
        start_dt = yesterday
        end_dt = yesterday
    else:
        target_dt = datetime.strptime(args.date, "%Y-%m-%d")
        start_dt = target_dt
        end_dt = target_dt
        
    logger.info(f"Период синхронизации: с {start_dt.strftime('%d.%m.%Y')} по {end_dt.strftime('%d.%m.%Y')}")

    yandex_parks = config.get("yandex_parks", {})
    if not yandex_parks:
        logger.error("В config.json не заполнен раздел 'yandex_parks'!")
        sys.exit(1)

    if args.only_park:
        if args.only_park in yandex_parks:
            yandex_parks = {args.only_park: yandex_parks[args.only_park]}
        else:
            logger.error(f"Указанный парк '{args.only_park}' отсутствует в конфигурации!")
            sys.exit(1)

    # 1. Скачивание отчетов
    csv_files_to_sync = []
    
    if args.no_download:
        logger.info(f"Пропуск этапа скачивания. Поиск файлов в папке {inputs_dir}...")
        if start_dt == end_dt:
            date_suffix = start_dt.strftime("%Y-%m-%d")
        else:
            date_suffix = f"{start_dt.strftime('%Y-%m-%d')}_to_{end_dt.strftime('%Y-%m-%d')}"
            
        for park_name in yandex_parks.keys():
            pattern = os.path.join(inputs_dir, f"operations_{park_name}_{date_suffix}.csv")
            matches = glob.glob(pattern)
            if matches:
                csv_files_to_sync.extend(matches)
            else:
                # Попробуем найти любые файлы операций для этого парка (с датой или без даты)
                fallback_patterns = [
                    os.path.join(inputs_dir, f"operations_{park_name}_*.csv"),
                    os.path.join(inputs_dir, f"operations_{park_name}.csv")
                ]
                fallback_matches = []
                for pat in fallback_patterns:
                    fallback_matches.extend(glob.glob(pat))
                if fallback_matches:
                    logger.info(f"Точный файл для '{park_name}' за {date_suffix} не найден, используем найденные: {fallback_matches}")
                    csv_files_to_sync.extend(fallback_matches)
    else:
        logger.info(f"Начало скачивания отчетов для городов: {list(yandex_parks.keys())}")
        headless = not args.headful
        
        for park_name, park_id in yandex_parks.items():
            try:
                csv_path = download_operations_report(park_name, park_id, start_dt, end_dt, headless=headless)
                csv_files_to_sync.append(csv_path)
            except Exception as e:
                logger.error(f"Не удалось скачать отчет по операциям для парка {park_name}: {e}")

    # 2. Синхронизация данных с MySQL
    total_inserted = 0
    if not csv_files_to_sync:
        logger.warning("Нет файлов отчетов для синхронизации с базой данных.")
    else:
        logger.info(f"Начало импорта данных в базу данных из {len(csv_files_to_sync)} файлов...")
        for csv_path in csv_files_to_sync:
            try:
                inserted = sync_csv_to_mysql(config, csv_path)
                total_inserted += inserted
            except Exception as e:
                logger.error(f"Ошибка при синхронизации файла {csv_path}: {e}")

    logger.info(f"=== Синхронизация завершена. Всего новых записей добавлено: {total_inserted} ===")

if __name__ == "__main__":
    main()
