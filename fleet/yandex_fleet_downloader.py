# -*- coding: utf-8 -*-
import os
import sys
import time
import logging
from datetime import datetime

# Настройка логирования
logger = logging.getLogger(__name__)

def fill_segment(page, label_contains, value):
    """
    Заполняет сегмент даты в интерфейсе Яндекс.Флит.
    
    :param page: объект страницы Playwright
    :param label_contains: часть aria-label для поиска поля
    :param value: значение для ввода
    """
    selector = f'span[aria-label*="{label_contains}"]'
    el = page.locator(selector)
    if el.count() > 0:
        el.click()
        el.press("Control+A")
        el.type(value)
        logger.info(f"  Заполнено поле '{label_contains}': {value}")
    else:
        logger.warning(f"  Сегмент даты '{label_contains}' не найден!")

def download_revenue_report(park_name, park_id, report_date, headless=True):
    """
    Скачивает отчет 'Выручка по аппаратам' для парка Yandex Fleet с помощью Playwright.
    Использует повторные попытки при ошибках Яндекса.
    
    :param park_name: название парка (используется для формирования имени файла)
    :param park_id: идентификатор парка в системе Яндекс.Флит
    :param report_date: объект datetime, за какую дату выгрузить отчет
    :param headless: запускать ли браузер в фоновом режиме (по умолчанию True)
    :return: путь к скачанному файлу отчета
    """
    from playwright.sync_api import sync_playwright
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    inputs_dir = os.path.join(base_dir, "inputs")
    debug_tools_dir = os.path.abspath(os.path.join(base_dir, "..", "debug_tools"))
    
    if not os.path.exists(inputs_dir):
        os.makedirs(inputs_dir)
        
    date_str = report_date.strftime("%Y-%m-%d")
    dest_path = os.path.join(inputs_dir, f"revenue_{park_name}_{date_str}.csv")
    url = f"https://fleet.yandex.ru/snickers/reports?park_id={park_id}"
    
    logger.info(f"Начало выгрузки отчета для парка {park_name} за {date_str}")
    
    start_day = report_date.strftime("%d")
    start_month = report_date.strftime("%m")
    start_year = report_date.strftime("%Y")
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            viewport={"width": 1280, "height": 800}
        )
        
        try:
            page = context.pages[0] if context.pages else context.new_page()
            max_attempts = 10
            downloaded = False
            
            for attempt in range(1, max_attempts + 1):
                logger.info(f"  Попытка {attempt} из {max_attempts}...")
                try:
                    page.goto(url)
                    page.wait_for_timeout(5000)
                    
                    if "passport.yandex" in page.url:
                        logger.error("  Ошибка: Требуется авторизация в Яндекс. Запустите login_and_inspect.py вручную.")
                        raise Exception("Необходима авторизация")
                        
                    # Кликаем на отчет "Выручка по аппаратам"
                    report_btn = page.locator("text='Выручка по аппаратам'")
                    if report_btn.count() > 0:
                        report_btn.first.click()
                        page.wait_for_timeout(3000)
                    else:
                        logger.warning("  Отчет 'Выручка по аппаратам' не найден на странице, пробуем перезагрузить...")
                        continue
                        
                    # Вводим дату
                    fill_segment(page, "день, ", start_day)
                    fill_segment(page, "месяц, ", start_month)
                    fill_segment(page, "год, ", start_year)
                    page.wait_for_timeout(1000)
                    
                    download_btn = page.locator('button:has-text("Скачать отчёт")')
                    is_disabled = download_btn.evaluate('el => el.disabled')
                    
                    if is_disabled:
                        logger.warning("  Кнопка 'Скачать отчёт' неактивна. Перезагрузка страницы...")
                        continue
                        
                    logger.info("  Клик по кнопке скачивания отчета...")
                    with page.expect_download(timeout=45000) as download_info:
                        download_btn.click()
                        
                    download = download_info.value
                    download.save_as(dest_path)
                    logger.info(f"  Отчет для {park_name} успешно скачан: {dest_path}")
                    downloaded = True
                    break
                    
                except Exception as attempt_err:
                    logger.warning(f"  Ошибка при попытке {attempt} для {park_name}: {attempt_err}")
                    
                    # Проверяем наличие модального окна ошибки
                    error_modal = page.locator("text='Что-то пошло не так'")
                    if error_modal.count() > 0 and error_modal.first.is_visible():
                        logger.warning("  Обнаружено окно ошибки 'Что-то пошло не так'")
                        screenshot_name = f"error_{park_name}_attempt_{attempt}.png"
                        os.makedirs(debug_tools_dir, exist_ok=True)
                        page.screenshot(path=os.path.join(debug_tools_dir, screenshot_name))
                        
                    time.sleep(3)
                    
            if not downloaded:
                raise Exception(f"Не удалось скачать отчет {park_name} после {max_attempts} попыток.")
                
            return dest_path
            
        except Exception as e:
            logger.error(f"Исключение при выгрузке отчета {park_name}: {e}")
            raise e
        finally:
            context.close()

if __name__ == "__main__":
    import argparse
    import json
    
    # Настройка логирования для прямого запуска
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Загружаем конфиг, чтобы получить список парков
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))
    
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден по пути: {config_path}")
        sys.exit(1)
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    yandex_parks = config.get("yandex_parks", {})
    if not yandex_parks:
        logger.error("В конфигурации config.json отсутствует раздел 'yandex_parks'!")
        sys.exit(1)
        
    parser = argparse.ArgumentParser(description="Скачивание отчетов 'Выручка по аппаратам' из Яндекс.Флит")
    parser.add_argument("--date", help="Дата отчета в формате ГГГГ-ММ-ДД (по умолчанию: вчера)")
    parser.add_argument("--park", help="Имя конкретного парка для скачивания (по умолчанию: все)")
    parser.add_argument("--headful", action="store_true", help="Запустить браузер в видимом режиме для отладки")
    args = parser.parse_args()
    
    # Определяем дату
    if args.date:
        try:
            report_date = datetime.strptime(args.date.strip(), "%Y-%m-%d")
        except ValueError:
            logger.error("Неверный формат даты! Используйте ГГГГ-ММ-ДД.")
            sys.exit(1)
    else:
        # По умолчанию - вчера
        from datetime import timedelta
        report_date = datetime.now() - timedelta(days=1)
        
    # Определяем парки
    parks_to_download = {}
    if args.park:
        # Регистронезависимый поиск
        park_clean = args.park.strip().lower()
        for p_name, p_id in yandex_parks.items():
            if p_name.lower() == park_clean:
                parks_to_download[p_name] = p_id
                break
        if not parks_to_download:
            logger.error(f"Парк с именем '{args.park}' не найден в config.json!")
            logger.info(f"Доступные парки: {list(yandex_parks.keys())}")
            sys.exit(1)
    else:
        parks_to_download = yandex_parks
        
    logger.info(f"Начало выгрузки отчетов выручки за дату: {report_date.strftime('%Y-%m-%d')}")
    logger.info(f"Список парков для скачивания: {list(parks_to_download.keys())}")
    
    headless = not args.headful
    success_count = 0
    
    for park_name, park_id in parks_to_download.items():
        try:
            download_revenue_report(park_name, park_id, report_date, headless=headless)
            success_count += 1
        except Exception as e:
            logger.error(f"Не удалось скачать отчет выручки для {park_name}: {e}")
            
    logger.info(f"Успешно скачано отчетов: {success_count} из {len(parks_to_download)}")
