# -*- coding: utf-8 -*-
import os
import sys
import glob
import json
import logging
import pandas as pd

# Настройка вывода в консоль для корректной работы с кодировками на Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Конфигурация логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def clean_revenue_data(df_rev: pd.DataFrame) -> pd.DataFrame:
    """
    Очищает и нормализует данные о выручке из DataFrame отчета по выручке.
    
    Аргументы:
        df_rev (pd.DataFrame): Исходный DataFrame отчета по выручке.
        
    Возвращает:
        pd.DataFrame: Измененный DataFrame с очищенным столбцом выручки 'fact_clean'.
    """
    df_cleaned = df_rev.copy()
    # Заменяем пробельные символы и неразрывные пробелы \xa0, меняем запятые на точки
    df_cleaned['fact_clean'] = (
        df_cleaned['fact']
        .astype(str)
        .str.replace(r'[\s\xa0]', '', regex=True)
        .str.replace(',', '.', regex=False)
    )
    df_cleaned['fact_clean'] = pd.to_numeric(df_cleaned['fact_clean'], errors='coerce').fillna(0.0)
    
    # Также приводим vending_id к числовому типу для надежности сопоставления
    df_cleaned['vending_id_num'] = pd.to_numeric(df_cleaned['vending_id'], errors='coerce')
    
    return df_cleaned

def aggregate_revenue_by_id(df_rev_cleaned: pd.DataFrame) -> dict:
    """
    Группирует данные по vending_id и суммирует выручку.
    
    Аргументы:
        df_rev_cleaned (pd.DataFrame): Очищенный DataFrame отчета по выручке с 'vending_id_num' и 'fact_clean'.
        
    Возвращает:
        dict: Словарь {vending_id: sum_revenue}
    """
    grouped = df_rev_cleaned.groupby('vending_id_num')['fact_clean'].sum()
    return grouped.to_dict()

def process_city_data(vending_path: str, revenue_path: str, city_name: str) -> list:
    """
    Фильтрует станции владельца 'berizaryad' и объединяет их с данными по выручке и статусом размещения.
    
    Аргументы:
        vending_path (str): Абсолютный путь к CSV-файлу с аппаратами.
        revenue_path (str): Абсолютный путь к CSV-файлу с выручкой.
        city_name (str): Название города.
        
    Возвращает:
        list: Список словарей с данными по станциям: [{'vending_id': int, 'address': str, 'place_name': str, 'city': str, 'revenue': float, 'status': str}]
    """
    # Чтение файлов с явными параметрами sep, encoding, decimal
    df_v = pd.read_csv(vending_path, sep=',', encoding='utf-8', decimal='.', low_memory=False)
    df_r = pd.read_csv(revenue_path, sep=',', encoding='utf-8', decimal='.', low_memory=False)
    
    # Очистка и фильтрация аппаратов OwnedBy == 'berizaryad'
    df_v_filtered = df_v[df_v['OwnedBy'].astype(str).str.strip().str.lower() == 'berizaryad'].copy()
    
    # Нормализация DisplayNumber к числовому типу для джойна
    df_v_filtered['DisplayNumber_num'] = pd.to_numeric(df_v_filtered['DisplayNumber'], errors='coerce')
    df_v_valid = df_v_filtered.dropna(subset=['DisplayNumber_num'])
    
    # Подготовка и агрегация выручки
    df_r_cleaned = clean_revenue_data(df_r)
    revenue_map = aggregate_revenue_by_id(df_r_cleaned)
    
    # Определяем активный статус каждого аппарата (по remove_date)
    df_r_active = df_r_cleaned[df_r_cleaned['remove_date'].astype(str).str.strip().str.contains('2222-02-01|01.02.2222', regex=True)]
    status_map = {}
    for _, row in df_r_active.iterrows():
        v_id = row['vending_id_num']
        if pd.notna(v_id):
            status_map[int(v_id)] = str(row['office_status']).strip().lower()
            
    # Объединение данных
    results = []
    for _, row in df_v_valid.iterrows():
        vending_id = int(row['DisplayNumber_num'])
        address = str(row['Address']).strip() if pd.notna(row['Address']) else "Адрес не указан"
        place_name = str(row['PlaceName']).strip() if pd.notna(row['PlaceName']) else "Неизвестная локация"
        
        # Получаем выручку из мапы, по умолчанию 0.0
        revenue = float(revenue_map.get(vending_id, 0.0))
        
        # Определяем статус: location (на локации) или office (в офисе / за офисом)
        office_status = status_map.get(vending_id, '')
        if 'placed' in office_status:
            status = 'location'
        else:
            status = 'office'
            
        results.append({
            "vending_id": vending_id,
            "address": address,
            "place_name": place_name,
            "city": city_name,
            "revenue": revenue,
            "status": status
        })
        
    return results

def main():
    # Определение путей относительно __file__ для обеспечения переносимости
    base_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(base_dir)
    inputs_dir = os.path.join(base_dir, 'inputs')
    templates_dir = os.path.join(base_dir, 'templates')
    
    template_path = os.path.join(templates_dir, 'berizaryad_template.html')
    output_path = os.path.join(workspace_dir, 'berizaryad_revenue_report.html')
    
    cities = ['Омск', 'Рязань', 'Ижевск', 'Ульяновск', 'Магнитогорск', 'Сургут', 'Киров', 'Чебоксары', 'Орёл']
    
    report_data = {
        "cities": {},
        "summary": {
            "total_revenue": 0.0,
            "total_devices": 0,
            "avg_revenue": 0.0,
            "devices_on_location": 0,
            "devices_in_office": 0,
            "city_revenues": {},
            "city_device_counts": {},
            "city_location_counts": {},
            "city_office_counts": {}
        }
    }
    
    total_rev = 0.0
    total_dev = 0
    total_location = 0
    total_office = 0
    
    # Обработка данных по каждому городу
    for city in cities:
        vending_file = os.path.join(inputs_dir, f"vendings_{city}.csv")
        
        # Поиск самого свежего отчета по выручке
        revenue_pattern = os.path.join(inputs_dir, f"revenue_{city}_*.csv")
        revenue_files = glob.glob(revenue_pattern)
        
        if not os.path.exists(vending_file):
            logger.warning(f"Файл аппаратов для города {city} не найден: {vending_file}")
            continue
            
        if not revenue_files:
            logger.warning(f"Файлы выручки для города {city} не найдены по паттерну: {revenue_pattern}")
            continue
            
        # Сортируем и выбираем последний файл (например, 2026-06-30.csv)
        revenue_files.sort()
        latest_revenue_file = revenue_files[-1]
        
        logger.info(f"Обработка города: {city}. Выручка: {os.path.basename(latest_revenue_file)}")
        
        try:
            city_results = process_city_data(vending_file, latest_revenue_file, city)
            report_data["cities"][city] = city_results
            
            # Подсчет локальных сумм по городу
            city_rev = sum(item["revenue"] for item in city_results)
            city_count = len(city_results)
            city_location = sum(1 for item in city_results if item["status"] == 'location')
            city_office = sum(1 for item in city_results if item["status"] == 'office')
            
            report_data["summary"]["city_revenues"][city] = city_rev
            report_data["summary"]["city_device_counts"][city] = city_count
            report_data["summary"]["city_location_counts"][city] = city_location
            report_data["summary"]["city_office_counts"][city] = city_office
            
            total_rev += city_rev
            total_dev += city_count
            total_location += city_location
            total_office += city_office
            
            logger.info(
                f"Город {city}: найдено {city_count} аппаратов 'berizaryad' "
                f"(на локациях: {city_location}, в офисе: {city_office}), "
                f"общая выручка: {city_rev:.2f} ₽"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при обработке данных по городу {city}: {e}", exc_info=True)
            report_data["cities"][city] = []
            report_data["summary"]["city_revenues"][city] = 0.0
            report_data["summary"]["city_device_counts"][city] = 0
            report_data["summary"]["city_location_counts"][city] = 0
            report_data["summary"]["city_office_counts"][city] = 0
            
    # Заполнение агрегированных показателей
    report_data["summary"]["total_revenue"] = total_rev
    report_data["summary"]["total_devices"] = total_dev
    report_data["summary"]["avg_revenue"] = (total_rev / total_dev) if total_dev > 0 else 0.0
    report_data["summary"]["devices_on_location"] = total_location
    report_data["summary"]["devices_in_office"] = total_office
    
    logger.info(
        f"Итоговые показатели: всего аппаратов {total_dev} "
        f"(на локациях: {total_location}, в офисе: {total_office}), "
        f"общая выручка {total_rev:.2f} ₽"
    )
    
    # Генерация HTML-файла отчета
    if not os.path.exists(template_path):
        logger.error(f"Шаблон отчета не найден: {template_path}")
        sys.exit(1)
        
    try:
        with open(template_path, 'r', encoding='utf-8') as tf:
            html_content = tf.read()
            
        # Инъекция JSON в шаблон
        json_payload = json.dumps(report_data, ensure_ascii=False, indent=2)
        html_rendered = html_content.replace('{{ REPORT_DATA }}', json_payload)
        
        with open(output_path, 'w', encoding='utf-8') as of:
            of.write(html_rendered)
            
        logger.info(f"Успешно сгенерирован отчет: {output_path}")
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчета: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
