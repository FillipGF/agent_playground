# -*- coding: utf-8 -*-
import os
import sys
import json
import logging
import glob
import re
import argparse
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

# Setup logging
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

def get_latest_revenue_file(city, inputs_dir):
    pattern = os.path.join(inputs_dir, f"revenue_{city}_*.csv")
    files = glob.glob(pattern)
    if not files:
        return None
    files.sort()
    return files[-1]

def fetch_disconnected_stations(city, park_id, profile_dir, inputs_dir):
    url = f"https://fleet.yandex.ru/snickers/map/service?park_id={park_id}&status=offline&location_warehouse=false&sort_field=time_in_status&sort_order=asc"
    
    logger.info(f"Запуск Playwright для города {city} (Park ID: {park_id})...")
    
    points_data = None
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1400, "height": 900}
        )
        
        page = context.pages[0] if context.pages else context.new_page()
        
        def handle_response(response):
            nonlocal points_data
            if "vendings-map/points" in response.url:
                try:
                    points_data = response.json()
                except Exception as e:
                    logger.error(f"Ошибка парсинга JSON: {e}")
                    
        page.on("response", handle_response)
        
        try:
            page.goto(url)
            page.wait_for_timeout(10000) # Wait 10 seconds for API call
        except Exception as e:
            logger.error(f"Ошибка загрузки страницы: {e}")
        finally:
            context.close()
            
    if not points_data:
        logger.error("Не удалось перехватить данные от API vendings-map/points.")
        return []
        
    vendings = points_data.get("vendings", [])
    logger.info(f"Получено всего автоматов в ответе API: {len(vendings)}")
    
    # Отбираем только оффлайн
    offline_vendings = [v for v in vendings if v.get("status", {}).get("id") == "offline"]
    logger.info(f"Из них оффлайн: {len(offline_vendings)}")
    
    # Читаем сначала vendings_{city}.csv для маппинга, так как там наиболее полные данные по всем установленным аппаратам
    vending_mapping = {}
    vendings_file = os.path.join(inputs_dir, f"vendings_{city}.csv")
    if os.path.exists(vendings_file):
        logger.info(f"Загрузка файла vendings для маппинга адресов: {vendings_file}")
        try:
            df_v = pd.read_csv(vendings_file)
            for _, r in df_v.iterrows():
                v_id = str(r.get('DisplayNumber', '')).strip()
                if v_id:
                    if '.' in v_id:
                        v_id = v_id.split('.')[0]
                    vending_mapping[v_id] = {
                        "place_name": str(r.get('PlaceName', '')).strip(),
                        "address": str(r.get('Address', '')).strip()
                    }
        except Exception as e:
            logger.error(f"Ошибка чтения файла vendings: {e}")
            
    # Читаем также последний отчет по выручке как дополнение/резервный источник
    revenue_file = get_latest_revenue_file(city, inputs_dir)
    if revenue_file:
        logger.info(f"Загрузка файла выручки для маппинга адресов: {revenue_file}")
        try:
            df_rev = pd.read_csv(revenue_file, decimal=',')
            for _, r in df_rev.iterrows():
                v_id = str(r.get('vending_id', '')).strip()
                if v_id:
                    if '.' in v_id:
                        v_id = v_id.split('.')[0]
                    place = str(r.get('place_name', '')).strip()
                    addr = str(r.get('address', '')).strip()
                    
                    if v_id not in vending_mapping or vending_mapping[v_id]["place_name"] in ["", "nan", "Неизвестно", "undefined"]:
                        vending_mapping[v_id] = {
                            "place_name": place if place else "Неизвестно",
                            "address": addr if addr else "Неизвестно"
                        }
        except Exception as e:
            logger.error(f"Ошибка чтения файла выручки: {e}")
            
    processed_stations = []
    
    for v in offline_vendings:
        vending_id = str(v.get("display_number", v.get("id", "")))
        disconnection_sec = v.get("disconnection_time")
        
        # Если disconnection_time отсутствует или равен null, пропускаем или считаем за 0
        if disconnection_sec is None:
            disconnection_sec = 0
            
        # Проверяем фильтр по времени (не в сети менее 24 часов)
        if disconnection_sec < 86400:
            continue
            
        # Маппим данные локации
        meta = vending_mapping.get(vending_id, {"place_name": "Неизвестно", "address": "Неизвестно"})
        place_name = meta["place_name"]
        address = meta["address"]
        
        # Фильтруем станции, у которых в адресе есть "@"
        if "@" in address:
            continue
            
        # Фильтруем "офис" или "склад" в названии локации
        place_name_lower = place_name.lower()
        if "офис" in place_name_lower or "склад" in place_name_lower:
            continue
            
        # Рассчитываем дни и часы оффлайна
        days_offline = disconnection_sec // 86400
        hours_offline = (disconnection_sec % 86400) // 3600
        
        processed_stations.append({
            "vending_id": vending_id,
            "place_name": place_name,
            "address": address,
            "disconnection_seconds": disconnection_sec,
            "offline_duration": f"{days_offline} д. {hours_offline} ч.",
            "park_id": park_id
        })
        
    logger.info(f"После фильтрации осталось станций: {len(processed_stations)}")
    return processed_stations

def main():
    config = load_config()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    inputs_dir = os.path.abspath(os.path.join(base_dir, "inputs"))
    
    parser = argparse.ArgumentParser(description="Выгрузка оффлайн-станций Яндекс.Флит")
    parser.add_argument("--city", default=None, help="Город для выгрузки (по умолчанию все)")
    args = parser.parse_args()
    
    yandex_parks = config.get("yandex_parks", {})
    
    cities_to_sync = []
    if args.city:
        if args.city not in yandex_parks:
            logger.error(f"Город {args.city} не найден в config.json")
            sys.exit(1)
        cities_to_sync = [args.city]
    else:
        cities_to_sync = list(yandex_parks.keys())
        
    for city in cities_to_sync:
        park_id = yandex_parks[city]
        stations = fetch_disconnected_stations(city, park_id, profile_dir, inputs_dir)
        
        # Сохраняем в JSON-файл в inputs/
        out_path = os.path.join(inputs_dir, f"disconnected_{city}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(stations, f, ensure_ascii=False, indent=2)
        logger.info(f"Данные оффлайн-станций для {city} сохранены в: {out_path}")

if __name__ == "__main__":
    main()
