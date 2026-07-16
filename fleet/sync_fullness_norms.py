# -*- coding: utf-8 -*-
import os
import sys
import json
import logging
import random
import time
import pandas as pd
from playwright.sync_api import sync_playwright

# Setup logging
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def load_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))
    if not os.path.exists(config_path):
        logger.error(f"Config not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def download_yandex_map_service_csv(config, park_name, park_id, headless=True):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    inputs_dir = os.path.join(base_dir, "inputs")
    os.makedirs(inputs_dir, exist_ok=True)
    
    dest_path = os.path.join(inputs_dir, f"map_service_{park_name}.csv")
    url = f"https://fleet.yandex.ru/snickers/map/service?park_id={park_id}&location_warehouse=false"
    
    logger.info(f"Начало скачивания сервисной карты для парка: {park_name} (ID: {park_id})")
    
    max_attempts = 3
    base_delay = 5.0
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"Попытка {attempt} из {max_attempts} для парка {park_name}...")
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                viewport={"width": 1400, "height": 900},
                args=["--no-proxy-server"]
            )
            
            try:
                page = context.pages[0] if context.pages else context.new_page()
                
                logger.info(f"Переход на страницу {url}...")
                page.goto(url)
                page.wait_for_timeout(10000) # Wait for page load and API call
                
                if "passport.yandex" in page.url:
                    logger.error(f"Ошибка: Необходима авторизация в Яндекс для парка {park_name}!")
                    raise Exception("Необходима авторизация")
                    
                export_button_selector = 'button[aria-label*="CSV"]'
                try:
                    page.wait_for_selector(export_button_selector, timeout=20000)
                except Exception:
                    if "passport.yandex" in page.url:
                        logger.error(f"Ошибка: Необходима авторизация в Яндекс для парка {park_name}!")
                        raise Exception("Необходима авторизация")
                    else:
                        logger.error(f"Не удалось дождаться кнопки экспорта для парка {park_name}!")
                        debug_tools_dir = os.path.abspath(os.path.join(base_dir, "..", "debug_tools"))
                        os.makedirs(debug_tools_dir, exist_ok=True)
                        screenshot_path = os.path.join(debug_tools_dir, f"error_map_{park_name}_attempt_{attempt}.png")
                        page.screenshot(path=screenshot_path)
                        logger.info(f"Скриншот ошибки сохранен в {screenshot_path}")
                        raise Exception("Кнопка экспорта не найдена")
                
                logger.info("Клик по кнопке экспорта и скачивание файла...")
                with page.expect_download(timeout=30000) as download_info:
                    page.locator(export_button_selector).first.click()
                    
                download = download_info.value
                download.save_as(dest_path)
                logger.info(f"Сервисная карта успешно скачана и сохранена в: {dest_path}")
                return dest_path
                
            except Exception as e:
                logger.error(f"Исключение на попытке {attempt} при скачивании карты {park_name}: {e}")
                if attempt < max_attempts:
                    delay = base_delay * attempt + random.uniform(1.0, 3.0)
                    logger.info(f"Ожидание {delay:.2f} сек перед повторной попыткой...")
                    time.sleep(delay)
                else:
                    raise e
            finally:
                context.close()

def parse_map_service_norms(file_path):
    norms = {}
    if not os.path.exists(file_path):
        return norms
    try:
        df = pd.read_csv(file_path, encoding='utf-8-sig')
        if len(df.columns) > 9:
            for _, row in df.iterrows():
                try:
                    vending_id = str(row.iloc[6]).strip()
                    if vending_id.endswith('.0'):
                        vending_id = vending_id[:-2]
                    if '.' in vending_id:
                        vending_id = vending_id.split('.')[0]
                    
                    sla_val = str(row.iloc[9]).strip()
                    if '%' in sla_val:
                        threshold = int(sla_val.replace('%', '').strip())
                        norms[vending_id] = threshold
                    elif sla_val.isdigit():
                        norms[vending_id] = int(sla_val)
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"Ошибка при парсинге файла {file_path}: {e}")
    return norms

def main():
    config = load_config()
    yandex_parks = config.get("yandex_parks", {})
    if not yandex_parks:
        logger.error("В конфигурации config.json отсутствует раздел 'yandex_parks'!")
        sys.exit(1)
        
    base_dir = os.path.dirname(os.path.abspath(__file__))
    inputs_dir = os.path.join(base_dir, "inputs")
    
    fullness_norms = {}
    
    # Загружаем существующий кэш, если он есть, чтобы сохранить старые нормы при сбоях
    cache_path = os.path.join(inputs_dir, "fullness_norms.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                fullness_norms = json.load(f)
            logger.info(f"Загружен существующий кэш норм заполненности. Записей: {len(fullness_norms)}")
        except Exception as e:
            logger.warning(f"Не удалось прочитать существующий кэш: {e}")
            
    success_count = 0
    fail_count = 0
    
    for park_name, park_id in yandex_parks.items():
        try:
            csv_path = download_yandex_map_service_csv(config, park_name, park_id, headless=True)
            if csv_path and os.path.exists(csv_path):
                norms = parse_map_service_norms(csv_path)
                logger.info(f"Успешно распарсено норм для парка {park_name}: {len(norms)} шт.")
                fullness_norms.update(norms)
                success_count += 1
                
                # Удаляем временный CSV-файл для экономии места
                try:
                    os.remove(csv_path)
                except Exception as rm_err:
                    logger.warning(f"Не удалось удалить временный файл {csv_path}: {rm_err}")
            else:
                fail_count += 1
        except Exception as e:
            logger.error(f"Не удалось выгрузить нормы для парка {park_name}: {e}")
            fail_count += 1
            
    # Сохраняем обновленный кэш в JSON
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(fullness_norms, f, ensure_ascii=False, indent=2)
        logger.info(f"Обновленный кэш сохранен в: {cache_path}. Всего записей: {len(fullness_norms)}")
    except Exception as e:
        logger.error(f"Не удалось сохранить кэш норм заполненности в JSON: {e}")
        
    logger.info(f"Синхронизация завершена. Успешно: {success_count}, Ошибок: {fail_count}")

if __name__ == '__main__':
    main()
