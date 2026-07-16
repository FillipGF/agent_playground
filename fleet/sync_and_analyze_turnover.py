# -*- coding: utf-8 -*-
"""
sync_and_analyze_turnover.py
----------------------------
Скрипт для получения актуальных отчетов по выручке Яндекс.Флит,
фильтрации станций (исключая офис, утерянные и демонтированные) и
группировки по оборачиваемости ячеек (cell_turnover).
Результаты выводятся в виде интерактивного HTML-дашборда с фильтрацией по городу.

Применяется инструмент `uv` для запуска:
    uv run python fleet/sync_and_analyze_turnover.py
"""

import os
import sys
import json
import glob
import logging
from datetime import datetime, timedelta
import pandas as pd

# Добавляем директорию скрипта в sys.path для корректного импорта yandex_fleet_downloader
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

# Настройка вывода в консоль в UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

try:
    from yandex_fleet_downloader import download_revenue_report
except ImportError as e:
    logger.error(f"Не удалось импортировать download_revenue_report: {e}")
    download_revenue_report = None


def load_config():
    """
    Загружает файл конфигурации config.json из корня проекта.
    
    :return: dict с конфигурацией
    """
    config_path = os.path.abspath(os.path.join(script_dir, "..", "config.json"))
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_report_file(park_name, inputs_dir):
    """
    Ищет самый свежий по имени/дате файл revenue для указанного города в папке inputs.
    
    :param park_name: название города (парка)
    :param inputs_dir: путь к директории с файлами
    :return: str путь к файлу или None
    """
    pattern = os.path.join(inputs_dir, f"revenue_{park_name}_*.csv")
    matches = glob.glob(pattern)
    if not matches:
        return None
    # Сортируем по имени файла (дата в формате YYYY-MM-DD идет в конце, поэтому сортировка по имени корректна)
    return max(matches, key=os.path.basename)


def process_and_clean_data(csv_path, city_name):
    """
    Считывает CSV файл, фильтрует неактивные станции и очищает данные.
    
    :param csv_path: путь к CSV файлу
    :param city_name: название города (парка) для записи в данные
    :return: pd.DataFrame очищенный датафрейм или None
    """
    if not os.path.exists(csv_path):
        logger.warning(f"Файл не найден для обработки: {csv_path}")
        return None

    try:
        # Пробуем читать с автоопределением кодировки (UTF-8-sig для Excel-CSV или CP1251)
        try:
            df = pd.read_csv(csv_path, sep=',', encoding='utf-8-sig')
        except Exception:
            df = pd.read_csv(csv_path, sep=',', encoding='cp1251')

        # Проверка обязательных столбцов
        required_cols = ['vending_id', 'place_name', 'address', 'cells_total', 
                         'cell_turnover', 'place_date', 'remove_date', 'office_status', 'fact', 'orders']
        for col in required_cols:
            if col not in df.columns:
                logger.error(f"В файле {csv_path} отсутствует обязательный столбец '{col}'!")
                return None

        # Очистка и фильтрация
        # 1. Исключаем станции, которые находятся в офисе или утеряны (оставляем только 'placed')
        df = df[df['office_status'].fillna('').astype(str).str.strip().str.lower() == 'placed'].copy()

        # 2. Исключаем демонтированные станции (оставляем только заглушки 01.02.2222 или 2222-02-01)
        # Обрабатываем также пустые/NaN значения в remove_date как отсутствие демонтажа
        remove_date_filled = df['remove_date'].fillna('01.02.2222').astype(str).str.strip()
        df = df[remove_date_filled.str.contains('2222-02-01|01.02.2222', regex=True)].copy()

        # 3. Чистим cell_turnover (заменяем запятую на точку, переводим в числовой тип)
        turnover_str = df['cell_turnover'].fillna('0').astype(str).str.replace(',', '.').str.strip()
        df['cell_turnover_clean'] = pd.to_numeric(turnover_str, errors='coerce').fillna(0.0)

        # 4. Чистим cells_total
        cells_str = df['cells_total'].fillna('0').astype(str).str.strip()
        df['cells_total_clean'] = pd.to_numeric(cells_str, errors='coerce').fillna(0).astype(int)

        # 5. Чистим fact (выручку за период)
        fact_str = df['fact'].fillna('0').astype(str).str.replace(',', '.').str.strip()
        df['fact_clean'] = pd.to_numeric(fact_str, errors='coerce').fillna(0.0)

        # 6. Чистим orders (кол-во аренд за период)
        orders_str = df['orders'].fillna('0').astype(str).str.strip()
        df['orders_clean'] = pd.to_numeric(orders_str, errors='coerce').fillna(0).astype(int)

        # Добавляем город из имени парка
        df['city_clean'] = city_name

        # Оставляем только нужные столбцы
        df_result = df[[
            'vending_id', 'address', 'place_name', 
            'cells_total_clean', 'cell_turnover_clean', 
            'place_date', 'city_clean', 'fact_clean', 'orders_clean'
        ]].copy()
        
        # Переименуем для удобства
        df_result.columns = [
            'vending_id', 'address', 'place_name', 
            'cells_total', 'cell_turnover', 
            'place_date', 'city', 'revenue', 'orders'
        ]

        # Преобразуем vending_id в int для красоты
        df_result['vending_id'] = pd.to_numeric(df_result['vending_id'], errors='coerce').fillna(0).astype(int)

        return df_result

    except Exception as e:
        logger.error(f"Ошибка при обработке файла {csv_path}: {e}")
        return None


def generate_html_report(stations, output_paths):
    """
    Генерирует HTML-файл на основе переданного списка станций.
    
    :param stations: список словарей с данными станций
    :param output_paths: список путей для сохранения HTML файла
    """
    # Сериализуем данные в JSON для встраивания в HTML
    stations_json = json.dumps(stations, ensure_ascii=False, indent=2)

    # Список уникальных городов для фильтра
    cities = sorted(list(set(s['city'] for s in stations)))

    html_template = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Анализ оборачиваемости ячеек (Cell Turnover)</title>
    
    <!-- Google Fonts: Inter & Outfit -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
    
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

    <style>
        :root {{
            --bg-color: #0b0f19;
            --panel-bg: rgba(20, 27, 45, 0.7);
            --panel-border: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
            --text-dimmed: #6b7280;
            --accent-blue: #3b82f6;
            --accent-purple: #8b5cf6;
            --accent-gradient: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
            
            --color-high: #ef4444;     /* > 0.5 */
            --color-medium: #f59e0b;   /* 0.4 - 0.5 */
            --color-low: #10b981;      /* < 0.4 */
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
            padding: 0;
        }}

        header {{
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.9) 0%, rgba(11, 15, 25, 0) 100%);
            padding: 24px 32px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--panel-border);
            z-index: 10;
        }}

        .logo-area {{
            display: flex;
            flex-direction: column;
        }}

        .logo-title {{
            font-family: 'Outfit', sans-serif;
            font-size: 24px;
            font-weight: 800;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }}

        .logo-subtitle {{
            font-size: 11px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-top: 4px;
            font-weight: 600;
        }}

        .generation-time {{
            font-size: 13px;
            color: var(--text-dimmed);
        }}

        main {{
            flex: 1;
            padding: 32px;
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }}

        /* Панель фильтров */
        .filters-panel {{
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            align-items: center;
            backdrop-filter: blur(12px);
        }}

        .filter-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex: 1;
            min-width: 200px;
        }}

        .filter-group.search {{
            flex: 2;
            min-width: 300px;
        }}

        .filter-label {{
            font-size: 12px;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .filter-control {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--panel-border);
            border-radius: 10px;
            padding: 10px 14px;
            color: var(--text-primary);
            font-size: 14px;
            outline: none;
            transition: all 0.3s ease;
        }}

        .filter-control:focus {{
            border-color: var(--accent-blue);
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
        }}

        select.filter-control option {{
            background-color: var(--bg-color);
            color: var(--text-primary);
        }}

        .btn-action {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--panel-border);
            color: var(--text-primary);
            padding: 11px 20px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 18px;
        }}

        .btn-action:hover {{
            background: var(--accent-gradient);
            border-color: transparent;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(139, 92, 246, 0.3);
        }}

        .btn-action:active {{
            transform: translateY(0);
        }}

        /* Карточки со статистикой */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
        }}

        .stat-card {{
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            position: relative;
            overflow: hidden;
            backdrop-filter: blur(12px);
            transition: transform 0.3s ease, border-color 0.3s ease;
        }}

        .stat-card:hover {{
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--accent-gradient);
        }}

        .stat-card.high::before {{ background: var(--color-high); }}
        .stat-card.medium::before {{ background: var(--color-medium); }}
        .stat-card.low::before {{ background: var(--color-low); }}

        .stat-title {{
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 500;
        }}

        .stat-value {{
            font-family: 'Outfit', sans-serif;
            font-size: 28px;
            font-weight: 800;
        }}

        /* Раздел с графиками */
        .charts-section {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
        }}

        .chart-container {{
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 20px;
            backdrop-filter: blur(12px);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 280px;
        }}

        .chart-title {{
            font-size: 14px;
            color: var(--text-secondary);
            font-weight: 600;
            align-self: flex-start;
            margin-bottom: 15px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        /* Вкладки (Категории) */
        .tabs-header {{
            display: flex;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--panel-border);
            padding: 6px;
            border-radius: 14px;
            gap: 6px;
            margin-top: 10px;
            overflow-x: auto;
        }}

        .tab-btn {{
            flex: 1;
            min-width: 180px;
            background: transparent;
            border: none;
            color: var(--text-secondary);
            padding: 12px 20px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            white-space: nowrap;
        }}

        .tab-btn:hover {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
        }}

        .tab-btn.active {{
            background: rgba(255, 255, 255, 0.1);
            color: var(--text-primary);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }}

        .tab-btn.high.active {{
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}

        .tab-btn.medium.active {{
            background: rgba(245, 158, 11, 0.15);
            color: #fde047;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }}

        .tab-btn.low.active {{
            background: rgba(16, 185, 129, 0.15);
            color: #6ee7b7;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .tab-badge {{
            background: rgba(255, 255, 255, 0.1);
            color: inherit;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
        }}

        /* Контейнер таблиц */
        .table-card {{
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            overflow: hidden;
            backdrop-filter: blur(12px);
            display: flex;
            flex-direction: column;
        }}

        .table-scroll {{
            max-height: 600px;
            overflow-y: auto;
        }}

        /* Таблица */
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 14px;
        }}

        th {{
            background: rgba(15, 23, 42, 0.6);
            padding: 16px 20px;
            font-weight: 600;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--panel-border);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
            position: sticky;
            top: 0;
            z-index: 5;
            cursor: pointer;
            user-select: none;
            transition: background-color 0.2s ease, color 0.2s ease;
        }}

        th:hover {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-primary) !important;
        }}

        td {{
            padding: 14px 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-primary);
            vertical-align: middle;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        tr {{
            transition: background-color 0.2s ease;
        }}

        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        .vending-id {{
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            color: var(--accent-blue);
        }}

        .place-name {{
            font-weight: 600;
        }}

        .address {{
            color: var(--text-secondary);
            font-size: 13px;
        }}

        .badge-city {{
            background: rgba(59, 130, 246, 0.1);
            color: #93c5fd;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            display: inline-block;
        }}

        .turnover-val {{
            font-family: 'Outfit', sans-serif;
            font-weight: 700;
            font-size: 15px;
        }}

        .turnover-val.high {{ color: var(--color-high); }}
        .turnover-val.medium {{ color: var(--color-medium); }}
        .turnover-val.low {{ color: var(--color-low); }}

        .empty-state {{
            padding: 60px;
            text-align: center;
            color: var(--text-dimmed);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
        }}

        .empty-icon {{
            font-size: 40px;
        }}

        /* Скроллбар */
        .table-scroll::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}

        .table-scroll::-webkit-scrollbar-track {{
            background: rgba(0, 0, 0, 0.1);
        }}

        .table-scroll::-webkit-scrollbar-thumb {{
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }}

        .table-scroll::-webkit-scrollbar-thumb:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}
    </style>
</head>
<body>

    <header>
        <div class="logo-area">
            <span class="logo-title">CELL TURNOVER ANALYSIS</span>
            <span class="logo-subtitle">Отчет по оборачиваемости ячеек</span>
        </div>
        <div class="generation-time">
            Дата выгрузки: <strong id="current-date-span"></strong>
        </div>
    </header>

    <main>
        <!-- Статистика -->
        <div class="stats-grid">
            <div class="stat-card">
                <span class="stat-title">Всего аппаратов</span>
                <span class="stat-value" id="stat-total-count">0</span>
            </div>
            <div class="stat-card">
                <span class="stat-title">Средний оборот ячеек</span>
                <span class="stat-value" id="stat-avg-turnover">0.0000</span>
            </div>
            <div class="stat-card high">
                <span class="stat-title">Необходимо расширение (>0.5)</span>
                <span class="stat-value" id="stat-high-count">0</span>
            </div>
            <div class="stat-card medium">
                <span class="stat-title">Подумать о расширении (0.4-0.5)</span>
                <span class="stat-value" id="stat-medium-count">0</span>
            </div>
            <div class="stat-card low">
                <span class="stat-title">Не требуется (<0.4)</span>
                <span class="stat-value" id="stat-low-count">0</span>
            </div>
        </div>

        <!-- Фильтры -->
        <div class="filters-panel">
            <div class="filter-group">
                <span class="filter-label">Город</span>
                <select id="filter-city" class="filter-control" onchange="applyFilters()">
                    <option value="">Все города</option>
                    {"".join([f'<option value="{c}">{c}</option>' for c in cities])}
                </select>
            </div>
            <div class="filter-group search">
                <span class="filter-label">Поиск</span>
                <input type="text" id="filter-search" class="filter-control" placeholder="Поиск по номеру, названию или адресу..." oninput="applyFilters()">
            </div>
            <div>
                <button class="btn-action" onclick="resetFilters()">Сбросить фильтры</button>
            </div>
            <div>
                <button class="btn-action" onclick="exportCSV()">Экспорт в CSV</button>
            </div>
        </div>

        <!-- Графики -->
        <div class="charts-section">
            <div class="chart-container" style="position: relative;">
                <div style="display: flex; justify-content: space-between; align-items: center; width: 100%; margin-bottom: 15px;">
                    <span class="chart-title" style="margin-bottom: 0;">Средняя оборачиваемость по городам</span>
                    <button class="btn-action" id="btn-toggle-sort" style="margin-top: 0; padding: 6px 12px; font-size: 12px; height: 32px;" onclick="toggleChartSort()">
                        Сортировка: по убыванию
                    </button>
                </div>
                <div style="width: 100%; height: 260px;">
                    <canvas id="cityChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Вкладки категорий -->
        <div class="tabs-header">
            <button class="tab-btn high active" onclick="switchTab('high')">
                Необходимо расширение (>0.5)
                <span class="tab-badge" id="badge-high">0</span>
            </button>
            <button class="tab-btn medium" onclick="switchTab('medium')">
                Подумать о расширении (0.4-0.5)
                <span class="tab-badge" id="badge-medium">0</span>
            </button>
            <button class="tab-btn low" onclick="switchTab('low')">
                Расширение не требуется (<0.4)
                <span class="tab-badge" id="badge-low">0</span>
            </button>
        </div>

        <!-- Табличный блок -->
        <div class="table-card">
            <div class="table-scroll">
                <table id="stations-table">
                    <thead>
                        <tr>
                            <th id="th-vending-id" onclick="sortTable('vending_id')">Номер</th>
                            <th id="th-city" onclick="sortTable('city')">Город</th>
                            <th id="th-place-name" onclick="sortTable('place_name')">Название заведения</th>
                            <th id="th-address" onclick="sortTable('address')">Адрес</th>
                            <th id="th-cells-total" style="text-align: right;" onclick="sortTable('cells_total')">Всего ячеек</th>
                            <th id="th-orders" style="text-align: right;" onclick="sortTable('orders')">Аренды</th>
                            <th id="th-revenue" style="text-align: right;" onclick="sortTable('revenue')">Выручка</th>
                            <th id="th-cell-turnover" style="text-align: right;" onclick="sortTable('cell_turnover')">Оборот ячеек</th>
                            <th id="th-place-date" onclick="sortTable('place_date')">Дата установки</th>
                        </tr>
                    </thead>
                    <tbody id="table-body">
                        <!-- Данные загружаются динамически -->
                    </tbody>
                </table>
            </div>
            <div id="no-data-element" class="empty-state" style="display: none;">
                <span class="empty-icon">🔍</span>
                <h3>Станции не найдены</h3>
                <p>Попробуйте скорректировать параметры фильтрации или поиска</p>
            </div>
        </div>
    </main>

    <script>
        // Встроенные сырые данные по станциям
        const stationsData = {stations_json};

        // Текущее состояние фильтрации
        let activeTab = 'high';
        let cityChartInstance = null;
        let chartSortDirection = 'desc';
        let tableSortColumn = 'cell_turnover';
        let tableSortDirection = 'desc';

        // Инициализация при загрузке
        window.addEventListener('DOMContentLoaded', () => {{
            const now = new Date();
            document.getElementById('current-date-span').innerText = now.toLocaleDateString('ru-RU') + ' ' + now.toLocaleTimeString('ru-RU', {{hour: '2-digit', minute:'2-digit'}});
            
            applyFilters();
            initCharts();
            updateSortHeaders();
        }});

        function switchTab(category) {{
            // Удаляем активные классы у всех кнопок
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            
            // Добавляем активный класс на нужную кнопку
            const btn = document.querySelector(`.tab-btn.${{category}}`);
            if (btn) btn.classList.add('active');
            
            activeTab = category;
            renderTable();
        }}

        function getFilteredData() {{
            const cityFilter = document.getElementById('filter-city').value.toLowerCase();
            const searchFilter = document.getElementById('filter-search').value.toLowerCase().trim();

            return stationsData.filter(station => {{
                // Фильтр по городу
                const matchCity = !cityFilter || station.city.toLowerCase() === cityFilter;
                
                // Фильтр по поиску (номер, название или адрес)
                const matchSearch = !searchFilter || 
                    String(station.vending_id).includes(searchFilter) ||
                    station.place_name.toLowerCase().includes(searchFilter) ||
                    station.address.toLowerCase().includes(searchFilter);
                
                return matchCity && matchSearch;
            }});
        }}

        function applyFilters() {{
            const filtered = getFilteredData();
            
            // Расчет категорий
            const highList = filtered.filter(s => s.cell_turnover > 0.5);
            const mediumList = filtered.filter(s => s.cell_turnover >= 0.4 && s.cell_turnover <= 0.5);
            const lowList = filtered.filter(s => s.cell_turnover < 0.4);

            // Обновляем бейджи вкладок
            document.getElementById('badge-high').innerText = highList.length;
            document.getElementById('badge-medium').innerText = mediumList.length;
            document.getElementById('badge-low').innerText = lowList.length;

            // Обновляем показатели статистики
            document.getElementById('stat-total-count').innerText = filtered.length;
            document.getElementById('stat-high-count').innerText = highList.length;
            document.getElementById('stat-medium-count').innerText = mediumList.length;
            document.getElementById('stat-low-count').innerText = lowList.length;

            // Средняя оборачиваемость
            const avg = filtered.length > 0 
                ? (filtered.reduce((acc, curr) => acc + curr.cell_turnover, 0) / filtered.length)
                : 0;
            document.getElementById('stat-avg-turnover').innerText = avg.toFixed(4);

            // Отрисовка текущей таблицы
            renderTable();
            
            // Обновление графиков
            updateCharts(filtered);
        }}

        function toggleChartSort() {{
            chartSortDirection = chartSortDirection === 'desc' ? 'asc' : 'desc';
            document.getElementById('btn-toggle-sort').innerText = 'Сортировка: ' + (chartSortDirection === 'desc' ? 'по убыванию' : 'по возрастанию');
            applyFilters();
        }}

        function sortTable(column) {{
            if (tableSortColumn === column) {{
                tableSortDirection = tableSortDirection === 'desc' ? 'asc' : 'desc';
            }} else {{
                tableSortColumn = column;
                tableSortDirection = 'desc';
            }}
            updateSortHeaders();
            renderTable();
        }}

        function updateSortHeaders() {{
            const headers = {{
                vending_id: 'th-vending-id',
                city: 'th-city',
                place_name: 'th-place-name',
                address: 'th-address',
                cells_total: 'th-cells-total',
                orders: 'th-orders',
                revenue: 'th-revenue',
                cell_turnover: 'th-cell-turnover',
                place_date: 'th-place-date'
            }};
            
            Object.keys(headers).forEach(col => {{
                const el = document.getElementById(headers[col]);
                if (!el) return;
                
                let baseText = el.getAttribute('data-text');
                if (!baseText) {{
                    baseText = el.innerText.replace(/ [▲▼]/g, '');
                    el.setAttribute('data-text', baseText);
                }}
                
                if (col === tableSortColumn) {{
                    el.innerHTML = baseText + (tableSortDirection === 'desc' ? ' ▼' : ' ▲');
                    el.style.color = 'var(--text-primary)';
                }} else {{
                    el.innerHTML = baseText;
                    el.style.color = 'var(--text-secondary)';
                }}
            }});
        }}

        function renderTable() {{
            const filtered = getFilteredData();
            let subset = [];
            
            if (activeTab === 'high') {{
                subset = filtered.filter(s => s.cell_turnover > 0.5);
            }} else if (activeTab === 'medium') {{
                subset = filtered.filter(s => s.cell_turnover >= 0.4 && s.cell_turnover <= 0.5);
            }} else {{
                subset = filtered.filter(s => s.cell_turnover < 0.4);
            }}

            // Сортировка таблицы
            subset.sort((a, b) => {{
                let valA = a[tableSortColumn];
                let valB = b[tableSortColumn];
                
                if (typeof valA === 'string') {{
                    valA = valA.toLowerCase();
                    valB = valB.toLowerCase();
                }}
                
                if (valA < valB) return tableSortDirection === 'desc' ? 1 : -1;
                if (valA > valB) return tableSortDirection === 'desc' ? -1 : 1;
                return 0;
            }});

            const tbody = document.getElementById('table-body');
            const noData = document.getElementById('no-data-element');
            tbody.innerHTML = '';

            if (subset.length === 0) {{
                noData.style.display = 'flex';
                document.getElementById('stations-table').style.display = 'none';
            }} else {{
                noData.style.display = 'none';
                document.getElementById('stations-table').style.display = 'table';
                
                subset.forEach(station => {{
                    const tr = document.createElement('tr');
                    
                    let cls = 'low';
                    if (station.cell_turnover > 0.5) cls = 'high';
                    else if (station.cell_turnover >= 0.4) cls = 'medium';

                    tr.innerHTML = `
                        <td class="vending-id">${{station.vending_id}}</td>
                        <td><span class="badge-city">${{station.city}}</span></td>
                        <td class="place-name">${{station.place_name}}</td>
                        <td class="address">${{station.address}}</td>
                        <td style="text-align: right; font-weight: 600;">${{station.cells_total}}</td>
                        <td style="text-align: right; font-weight: 500;">${{station.orders}}</td>
                        <td style="text-align: right; font-weight: 500;">${{Math.round(station.revenue).toLocaleString('ru-RU')}} ₽</td>
                        <td style="text-align: right;"><span class="turnover-val ${{cls}}">${{station.cell_turnover.toFixed(4)}}</span></td>
                        <td style="color: var(--text-secondary); font-size: 13px;">${{station.place_date}}</td>
                    `;
                    tbody.appendChild(tr);
                }});
            }}
        }}

        function resetFilters() {{
            document.getElementById('filter-city').value = '';
            document.getElementById('filter-search').value = '';
            applyFilters();
        }}

        function initCharts() {{
            const ctxCity = document.getElementById('cityChart').getContext('2d');
            cityChartInstance = new Chart(ctxCity, {{
                type: 'bar',
                data: {{
                    labels: [],
                    datasets: [{{
                        label: 'Средний оборот',
                        data: [],
                        backgroundColor: '#3b82f6',
                        borderRadius: 6,
                        borderWidth: 0
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    scales: {{
                        x: {{
                            grid: {{ color: 'rgba(255,255,255,0.05)' }},
                            ticks: {{ color: '#9ca3af' }}
                        }},
                        y: {{
                            grid: {{ display: false }},
                            ticks: {{ color: '#9ca3af' }}
                        }}
                    }},
                    plugins: {{
                        legend: {{ display: false }}
                    }}
                }}
            }});
        }}

        function updateCharts(filteredData) {{
            if (!cityChartInstance) return;

            // Обновление среднего по городам
            const cityData = {{}};
            filteredData.forEach(s => {{
                if (!cityData[s.city]) {{
                    cityData[s.city] = {{ total: 0, count: 0 }};
                }}
                cityData[s.city].total += s.cell_turnover;
                cityData[s.city].count += 1;
            }});

            const cityList = Object.keys(cityData).map(c => ({{
                name: c,
                val: cityData[c].total / cityData[c].count
            }}));

            // Сортировка по выбранному направлению
            cityList.sort((a, b) => {{
                if (chartSortDirection === 'desc') {{
                    return b.val - a.val;
                }} else {{
                    return a.val - b.val;
                }}
            }});

            const labels = cityList.map(item => item.name);
            const values = cityList.map(item => item.val);

            cityChartInstance.data.labels = labels;
            cityChartInstance.data.datasets[0].data = values;
            cityChartInstance.update();
        }}

        function exportCSV() {{
            const filtered = getFilteredData();
            if (filtered.length === 0) {{
                alert('Нет данных для экспорта');
                return;
            }}

            let csvContent = "\ufeff"; // BOM для корректного открытия UTF-8 в Excel
            csvContent += "Номер,Город,Название заведения,Адрес,Всего ячеек,Аренды,Выручка,Оборот ячеек,Дата установки\\r\\n";

            filtered.forEach(s => {{
                const name = s.place_name ? s.place_name.replace(/"/g, '""') : '';
                const addr = s.address ? s.address.replace(/"/g, '""') : '';
                const city = s.city ? s.city.replace(/"/g, '""') : '';
                
                const row = [
                    s.vending_id,
                    `"${{city}}"`,
                    `"${{name}}"`,
                    `"${{addr}}"`,
                    s.cells_total,
                    s.orders,
                    Math.round(s.revenue),
                    s.cell_turnover.toFixed(4),
                    s.place_date
                ].join(",");
                csvContent += row + "\\r\\n";
            }});

            const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(blob);
            
            const link = document.createElement("a");
            link.setAttribute("href", url);
            link.setAttribute("download", `cell_turnover_report_${{new Date().toISOString().slice(0,10)}}.csv`);
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            setTimeout(() => URL.revokeObjectURL(url), 100);
        }}
    </script>
</body>
</html>
"""

    for out_path in output_paths:
        try:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html_template)
            logger.info(f"Дашборд успешно сгенерирован и сохранен по пути: {out_path}")
        except Exception as e:
            logger.error(f"Не удалось записать дашборд в {out_path}: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cell Turnover Report Analysis")
    parser.add_argument("--no-download", action="store_true", help="Пропустить скачивание отчетов из Яндекс.Флит")
    args = parser.parse_args()

    config = load_config()
    yandex_parks = config.get("yandex_parks", {})
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    inputs_dir = os.path.join(base_dir, "inputs")
    outputs_dir = os.path.join(base_dir, "outputs")
    
    # 1. Попытка скачивания свежих отчетов
    report_date = datetime.now() - timedelta(days=1)
    date_str = report_date.strftime("%Y-%m-%d")
    
    if args.no_download:
        logger.info("Скачивание пропущено по запросу пользователя (--no-download). Используем локальные файлы.")
    else:
        logger.info(f"Начало синхронизации за вчерашнюю дату: {date_str}")
        # Скачиваем отчеты для каждого города
        for park_name, park_id in yandex_parks.items():
            if download_revenue_report is None:
                logger.info(f"Скачивание пропущено (модуль downloader не импортирован). Используем локальные файлы.")
                break
                
            try:
                download_revenue_report(park_name, park_id, report_date, headless=True)
            except Exception as ex:
                logger.warning(f"Не удалось скачать свежий отчет для {park_name} за {date_str}: {ex}")
                logger.warning("Скрипт продолжит работу, используя последний доступный файл в inputs/.")

    # 2. Чтение и объединение данных по всем городам
    all_stations = []
    
    for park_name in yandex_parks.keys():
        latest_file = get_latest_report_file(park_name, inputs_dir)
        if not latest_file:
            logger.warning(f"Файлы для города '{park_name}' отсутствуют в папке {inputs_dir}.")
            continue
            
        logger.info(f"Обработка файла для города {park_name}: {os.path.basename(latest_file)}")
        df_cleaned = process_and_clean_data(latest_file, park_name)
        
        if df_cleaned is not None and not df_cleaned.empty:
            stations_list = df_cleaned.to_dict(orient='records')
            all_stations.extend(stations_list)
            logger.info(f"Добавлено {len(stations_list)} активных станций из города {park_name}.")
        else:
            logger.warning(f"Нет активных станций для города {park_name} в файле {latest_file}.")

    # 3. Вывод результатов
    if not all_stations:
        logger.error("Не найдено ни одной активной станции во всех файлах отчетов!")
        sys.exit(1)
        
    logger.info(f"Всего обработано и отфильтровано активных станций по всем городам: {len(all_stations)}")
    
    # Считаем количество по категориям для логов
    high = len([s for s in all_stations if s['cell_turnover'] > 0.5])
    medium = len([s for s in all_stations if 0.4 <= s['cell_turnover'] <= 0.5])
    low = len([s for s in all_stations if s['cell_turnover'] < 0.4])
    
    logger.info(f"  - Необходимо расширение ячеек (>0.5)      : {high}")
    logger.info(f"  - Стоит подумать о расширении (0.4 - 0.5) : {medium}")
    logger.info(f"  - Расширение не требуется (<0.4)          : {low}")

    # Пути для сохранения HTML дашборда
    output_html_fleet = os.path.join(outputs_dir, "cell_turnover_analysis.html")
    output_html_root = os.path.abspath(os.path.join(base_dir, "..", "cell_turnover_analysis.html"))
    
    # Генерация HTML-файлов
    generate_html_report(all_stations, [output_html_fleet, output_html_root])
    
    logger.info("Процесс успешно завершен!")


if __name__ == "__main__":
    main()
