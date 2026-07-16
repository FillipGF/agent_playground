# -*- coding: utf-8 -*-
import os
import sys
import json
import glob
import logging
import argparse
import re
from datetime import datetime, timedelta
import pandas as pd

# Настройка вывода в консоль
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Добавляем путь к текущей папке в sys.path для импорта downloader
base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.append(base_dir)

from yandex_fleet_downloader import download_revenue_report

def load_config():
    """
    Загружает файл конфигурации config.json из корня проекта.
    """
    config_path = os.path.abspath(os.path.join(base_dir, "..", "config.json"))
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def aggregate_data(csv_paths):
    """
    Читает CSV-файлы отчетов по выручке и агрегирует статистику по категориям jewelry
    в разрезе франшиз (столбец franchise), а также вычисляет топ 15 локаций по выручке.
    
    :param csv_paths: словарь {park_name: csv_path}
    :return: кортеж (overall_counts, franchise_data, all_franchises, overall_top, franchise_top)
    """
    all_dfs = []
    
    for park_name, path in csv_paths.items():
        if not os.path.exists(path):
            logger.warning(f"Файл отчета для '{park_name}' отсутствует по пути: {path}")
            continue
            
        try:
            df = pd.read_csv(path)
            # Проверяем наличие ключевых колонок
            required_cols = ['vending_id', 'jewelry', 'franchise', 'place_name', 'address', 'fact', 'office_status', 'remove_date']
            missing_cols = [c for c in required_cols if c not in df.columns]
            if missing_cols:
                logger.error(f"В файле {path} отсутствуют необходимые колонки: {missing_cols}")
                continue
                
            # Фильтруем только установленные на локациях аппараты (office_status == 'placed')
            df = df[df['office_status'].astype(str).str.strip().str.lower() == 'placed']
            
            # Исключаем демонтированные аппараты (оставляем только те, у которых remove_date равен заглушке 2222-02-01 или 01.02.2222)
            df = df[df['remove_date'].astype(str).str.strip().str.contains('2222-02-01|01.02.2222', regex=True)]
            
            # Очищаем и нормализуем fact, place_name, address
            df['fact'] = df['fact'].astype(str).str.replace(r'[\s\xa0]', '', regex=True).str.replace(',', '.')
            df['fact'] = pd.to_numeric(df['fact'], errors='coerce').fillna(0.0)
            df['place_name'] = df['place_name'].fillna('Неизвестная локация').astype(str).str.strip()
            df['address'] = df['address'].fillna('Адрес не указан').astype(str).str.strip()
            df['jewelry'] = df['jewelry'].fillna('0').astype(str).str.strip().replace('', '0').replace('nan', '0').replace('Не указано', '0')
            df['franchise'] = df['franchise'].fillna('Неизвестная франшиза').astype(str).str.strip()
            
            all_dfs.append(df[required_cols])
        except Exception as e:
            logger.error(f"Ошибка при чтении файла {path}: {e}")
            
    if not all_dfs:
        logger.error("Нет данных для анализа.")
        sys.exit(1)
        
    # Объединяем все датафреймы
    df_overall = pd.concat(all_dfs, ignore_index=True)
    
    # Дополнительная очистка объединенного фрейма
    df_overall['jewelry'] = df_overall['jewelry'].fillna('0').astype(str).str.strip().replace('', '0').replace('nan', '0').replace('Не указано', '0')
    df_overall['franchise'] = df_overall['franchise'].fillna('Неизвестная франшиза').astype(str).str.strip()
    
    # 1. Агрегируем общие данные по всей сети
    overall_counts = df_overall['jewelry'].value_counts().to_dict()
    
    # 2. Агрегируем данные в разрезе каждой франшизы
    franchise_data = {}
    grouped = df_overall.groupby('franchise')
    
    for franchise_name, group in grouped:
        counts = group['jewelry'].value_counts().to_dict()
        franchise_data[franchise_name] = counts
        
    all_franchises = sorted(list(franchise_data.keys()))
    
    # 3. Топ-15 локаций по выручке суммарно
    df_loc_overall = df_overall.groupby(['place_name', 'address', 'franchise'])['fact'].sum().reset_index()
    df_loc_overall = df_loc_overall.sort_values(by='fact', ascending=False).head(15)
    overall_top = df_loc_overall.to_dict(orient='records')
    
    # 4. Топ-15 локаций по каждой франшизе
    franchise_top = {}
    for franchise_name, group in grouped:
        df_loc_group = group.groupby(['place_name', 'address', 'franchise'])['fact'].sum().reset_index()
        df_loc_group = df_loc_group.sort_values(by='fact', ascending=False).head(15)
        franchise_top[franchise_name] = df_loc_group.to_dict(orient='records')
        
    return overall_counts, franchise_data, all_franchises, overall_top, franchise_top

def generate_html_report(overall_counts, franchise_data, all_franchises, overall_top, franchise_top, yandex_parks, output_path):
    """
    Генерирует интерактивный HTML-файл с графиками Chart.js и таблицами распределения и топ-локаций.
    
    :param overall_counts: dict с общими подсчетами jewelry
    :param franchise_data: dict с подсчетами по франшизам
    :param all_franchises: список всех уникальных франшиз
    :param overall_top: список словарей топ-15 локаций по всей сети
    :param franchise_top: словарь со списками топ-15 локаций для каждой франшизы
    :param yandex_parks: словарь yandex_parks из конфига для очистки названий городов
    :param output_path: путь к сохранению HTML-файла
    """
    # Цвета для категорий jewelry (красивая неоновая палитра на темном фоне)
    jewelry_colors = {
        "Platinum": "#d946ef",  # Ярко-розовый
        "Gold": "#eab308",      # Золотой/желтый
        "Silver": "#94a3b8",    # Серебряный/серый
        "Bronze": "#ca8a04",    # Бронзовый/коричневый
        "new": "#06b6d4",       # Голубой/бирюзовый
        "0": "#475569"          # Темно-серый
    }
    
    # Подготавливаем данные для передачи в JavaScript
    data_js = {
        "Все франшизы": overall_counts,
        **franchise_data
    }
    
    locations_js = {
        "Все франшизы": overall_top,
        **franchise_top
    }
    
    # Генерация опций для селектора (обрезаем названия до городов, проверяя вхождение известных имен)
    selector_options = ['<option value="Все франшизы">Все города (Суммарно)</option>']
    for franchise in all_franchises:
        city_display = franchise
        for city in yandex_parks.keys():
            if city.lower() in franchise.lower():
                city_display = city
                break
        selector_options.append(f'<option value="{franchise}">{city_display}</option>')
        
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Анализ выручки по категориям Jewelry</title>
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-primary: #0b0f19;
            --bg-secondary: #151b2c;
            --bg-card: #1e2640;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-glow: rgba(99, 102, 241, 0.15);
            --border-color: #2e3c63;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-main);
            min-height: 100vh;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        @media (max-width: 600px) {{
            body {{
                padding: 0.75rem;
            }}
        }}

        .container {{
            max-width: 1200px;
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}

        h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }}

        @media (max-width: 600px) {{
            h1 {{
                font-size: 1.4rem;
                letter-spacing: -0.3px;
            }}
        }}

        .subtitle {{
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }}

        .selector-wrapper {{
            position: relative;
        }}

        select {{
            background-color: var(--bg-secondary);
            color: var(--text-main);
            border: 1px solid var(--border-color);
            padding: 0.75rem 2.5rem 0.75rem 1.25rem;
            border-radius: 12px;
            font-family: inherit;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            outline: none;
            transition: all 0.3s ease;
            appearance: none;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            width: 100%;
        }}

        select:hover, select:focus {{
            border-color: #6366f1;
            box-shadow: 0 0 15px var(--accent-glow);
        }}

        .selector-wrapper::after {{
            content: '▼';
            font-size: 0.8rem;
            color: var(--text-muted);
            position: absolute;
            right: 1.25rem;
            top: 50%;
            transform: translateY(-50%);
            pointer-events: none;
        }}

        @media (max-width: 600px) {{
            .selector-wrapper {{
                width: 100%;
            }}
        }}

        /* Grid Layout */
        .dashboard-grid {{
            display: grid;
            grid-template-columns: 1fr 1.2fr;
            gap: 2rem;
        }}

        @media (max-width: 900px) {{
            .dashboard-grid {{
                grid-template-columns: 1fr;
            }}
        }}

        /* Cards */
        .card {{
            background-color: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 2rem;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            display: flex;
            flex-direction: column;
            justify-content: center;
            position: relative;
            overflow: hidden;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}

        .card:hover {{
            transform: translateY(-3px);
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.4), 0 0 15px rgba(99, 102, 241, 0.05);
        }}

        .stats-summary {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}

        @media (max-width: 700px) {{
            .stats-summary {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}

        @media (max-width: 480px) {{
            .stats-summary {{
                grid-template-columns: 1fr 1fr;
                gap: 0.75rem;
            }}
        }}

        .stat-card {{
            background: linear-gradient(145deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.25rem;
            text-align: center;
        }}

        .stat-label {{
            color: var(--text-muted);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 0.5rem;
        }}

        .stat-value {{
            font-size: 1.8rem;
            font-weight: 800;
            color: #f8fafc;
        }}

        .chart-container {{
            position: relative;
            height: 350px;
            width: 100%;
            display: flex;
            justify-content: center;
            align-items: center;
        }}

        @media (max-width: 600px) {{
            .chart-container {{
                height: 260px;
            }}
        }}

        /* Table Styles */
        .table-scroll-wrapper {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            margin-top: 1rem;
        }}

        table {{
            width: 100%;
            min-width: 480px;
            border-collapse: collapse;
        }}

        th, td {{
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85rem;
            letter-spacing: 0.5px;
            white-space: nowrap;
        }}

        td {{
            font-size: 1rem;
        }}

        @media (max-width: 600px) {{
            th, td {{
                padding: 0.65rem 0.75rem;
                font-size: 0.88rem;
            }}
        }}

        .jewelry-badge {{
            display: inline-block;
            padding: 0.35rem 0.75rem;
            border-radius: 8px;
            font-weight: 800;
            font-size: 0.85rem;
            text-transform: uppercase;
        }}

        .badge-platinum {{ background-color: rgba(217, 70, 239, 0.15); color: #d946ef; border: 1px solid rgba(217, 70, 239, 0.3); }}
        .badge-gold {{ background-color: rgba(234, 179, 8, 0.15); color: #eab308; border: 1px solid rgba(234, 179, 8, 0.3); }}
        .badge-silver {{ background-color: rgba(148, 163, 184, 0.15); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.3); }}
        .badge-bronze {{ background-color: rgba(202, 138, 4, 0.15); color: #ca8a04; border: 1px solid rgba(202, 138, 4, 0.3); }}
        .badge-new {{ background-color: rgba(6, 182, 212, 0.15); color: #06b6d4; border: 1px solid rgba(6, 182, 212, 0.3); }}
        .badge-none {{ background-color: rgba(71, 85, 109, 0.15); color: #94a3b8; border: 1px solid rgba(71, 85, 109, 0.3); }}

        footer {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 3rem;
            border-top: 1px solid var(--border-color);
            padding-top: 1.5rem;
            width: 100%;
        }}

        @media (max-width: 600px) {{
            .card {{
                padding: 1.25rem;
                border-radius: 14px;
            }}
            footer {{
                margin-top: 1.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Анализ выручки по категориям Jewelry</h1>
                <p class="subtitle">Интерактивный срез распределения аппаратов</p>
            </div>
            <div class="selector-wrapper">
                <select id="franchise-selector" onchange="updateDashboard(this.value)">
                    {"".join(selector_options)}
                </select>
            </div>
        </header>

        <!-- Сводные метрики -->
        <div class="stats-summary">
            <div class="stat-card">
                <div class="stat-label">Всего аппаратов</div>
                <div class="stat-value" id="stat-total">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Лидирующая группа</div>
                <div class="stat-value" id="stat-top">new</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Премиум доля (Plat/Gold)</div>
                <div class="stat-value" id="stat-premium">0%</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <!-- График -->
            <div class="card">
                <div class="chart-container">
                    <canvas id="jewelry-chart"></canvas>
                </div>
            </div>

            <!-- Таблица данных -->
            <div class="card">
                <h2>Детальное распределение</h2>
                <div class="table-scroll-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Категория</th>
                            <th>Количество</th>
                            <th>Доля (процент)</th>
                        </tr>
                    </thead>
                    <tbody id="data-table-body">
                        <!-- Заполняется динамически -->
                    </tbody>
                </table>
                </div>
            </div>
        </div>

        <!-- Таблица топ 15 локаций по выручке -->
        <div class="card">
            <h2 style="margin-bottom: 1rem; background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Топ-15 локаций по выручке</h2>
            <div class="table-scroll-wrapper">
            <table>
                <thead>
                    <tr>
                        <th style="width: 5%;">#</th>
                        <th style="width: 35%;">Название локации</th>
                        <th style="width: 35%;">Адрес</th>
                        <th style="width: 13%;">Франшиза / Город</th>
                        <th style="width: 12%; text-align: right;">Выручка</th>
                    </tr>
                </thead>
                <tbody id="locations-table-body">
                    <!-- Заполняется динамически -->
                </tbody>
            </table>
            </div>
        </div>

        <footer>
            Автоматическая генерация аналитики Yandex Fleet • {datetime.now().strftime("%d.%m.%Y")}
        </footer>
    </div>

    <script>
        // Данные, переданные из Python
        const rawData = {json.dumps(data_js, ensure_ascii=False)};
        const topLocationsData = {json.dumps(locations_js, ensure_ascii=False)};
        
        // Настройка цветов Chart.js
        const colorsMap = {json.dumps(jewelry_colors)};
        
        let currentChart = null;

        function getBadgeClass(category) {{
            const mapping = {{
                "Platinum": "badge-platinum",
                "Gold": "badge-gold",
                "Silver": "badge-silver",
                "Bronze": "badge-bronze",
                "new": "badge-new",
                "0": "badge-none"
            }};
            return mapping[category] || "badge-none";
        }}

        function updateDashboard(selectedFranchise) {{
            const counts = rawData[selectedFranchise] || {{}};
            
            // Сортировка категорий по значимости
            const orderedCategories = ["Platinum", "Gold", "Silver", "Bronze", "new", "0"];
            
            const labels = [];
            const dataValues = [];
            const backgroundColors = [];
            
            let totalSum = 0;
            let premiumSum = 0;
            let maxCount = 0;
            let topCategory = "0";

            orderedCategories.forEach(cat => {{
                const val = counts[cat] || 0;
                if (val > 0 || selectedFranchise === "Все франшизы") {{
                    labels.push(cat);
                    dataValues.push(val);
                    backgroundColors.push(colorsMap[cat] || "#ccc");
                    totalSum += val;
                    if (cat === "Platinum" || cat === "Gold") {{
                        premiumSum += val;
                    }}
                    if (val > maxCount) {{
                        maxCount = val;
                        topCategory = cat;
                    }}
                }}
            }});

            // 1. Обновление карточек
            document.getElementById("stat-total").innerText = totalSum;
            document.getElementById("stat-top").innerText = topCategory;
            document.getElementById("stat-premium").innerText = totalSum > 0 
                ? ((premiumSum / totalSum) * 100).toFixed(1) + "%" 
                : "0%";

            // 2. Обновление таблицы
            const tableBody = document.getElementById("data-table-body");
            tableBody.innerHTML = "";

            labels.forEach((cat, index) => {{
                const val = dataValues[index];
                const pct = totalSum > 0 ? ((val / totalSum) * 100).toFixed(1) + "%" : "0%";
                
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td><span class="jewelry-badge ${{getBadgeClass(cat)}}">${{cat}}</span></td>
                    <td><strong>${{val}}</strong></td>
                    <td style="color: var(--text-muted)">${{pct}}</td>
                `;
                tableBody.appendChild(row);
            }});

            // 3. Обновление таблицы топ 15 локаций
            const locBody = document.getElementById("locations-table-body");
            locBody.innerHTML = "";
            
            const locs = topLocationsData[selectedFranchise] || [];
            locs.forEach((loc, index) => {{
                const row = document.createElement("tr");
                
                // Извлекаем город для отображения в столбце
                const match = loc.franchise.match(/\\((.*?)\\)/);
                let cityCol = match ? match[1] : loc.franchise;
                
                // Если имя франшизы содержит запятую, попробуем взять часть до запятой
                if (!match && loc.franchise.includes(',')) {{
                    cityCol = loc.franchise.split(',')[0].trim();
                }}
                
                // Форматируем выручку с разделителями тысяч
                const formattedRevenue = Math.round(loc.fact).toLocaleString('ru-RU');
                
                row.innerHTML = `
                    <td style="color: var(--text-muted)">${{index + 1}}</td>
                    <td><strong>${{loc.place_name}}</strong></td>
                    <td style="color: var(--text-muted); font-size: 0.9rem;">${{loc.address}}</td>
                    <td style="color: var(--text-muted)">${{cityCol}}</td>
                    <td style="text-align: right; color: #10b981; font-weight: 600;">${{formattedRevenue}} ₽</td>
                `;
                locBody.appendChild(row);
            }});

            // 4. Обновление или создание графика
            const ctx = document.getElementById('jewelry-chart').getContext('2d');
            
            if (currentChart) {{
                currentChart.destroy();
            }}

            currentChart = new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: labels,
                    datasets: [{{
                        data: dataValues,
                        backgroundColor: backgroundColors,
                        borderWidth: 2,
                        borderColor: '#151b2c',
                        hoverOffset: 15
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            position: 'bottom',
                            labels: {{
                                color: '#f8fafc',
                                font: {{
                                    family: "'Outfit', sans-serif",
                                    size: 13
                                }},
                                padding: 20
                            }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    const value = context.raw;
                                    const percentage = totalSum > 0 ? ((value / totalSum) * 100).toFixed(1) + "%" : "0%";
                                    return ` ${{context.label}}: ${{value}} шт. (${{percentage}})`;
                                }}
                            }}
                        }}
                    }},
                    cutout: '65%'
                }}
            }});
        }}

        // Первая инициализация при загрузке страницы
        window.onload = function() {{
            updateDashboard("Все франшизы");
        }};
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info(f"HTML-отчет успешно сгенерирован: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Анализ распределения jewelry по франшизам Yandex Fleet")
    parser.add_argument("--date", default="yesterday", help="Дата отчета в формате ГГГГ-ММ-ДД или 'yesterday'")
    parser.add_argument("--no-download", action="store_true", help="Пропустить выгрузку, использовать имеющиеся файлы в inputs/")
    parser.add_argument("--headful", action="store_true", help="Запустить браузер в видимом режиме")
    args = parser.parse_args()
    
    config = load_config()
    yandex_parks = config.get("yandex_parks", {})
    if not yandex_parks:
        logger.error("В config.json отсутствует раздел 'yandex_parks'!")
        sys.exit(1)
        
    if args.date == "yesterday":
        report_date = datetime.now() - timedelta(days=1)
    else:
        report_date = datetime.strptime(args.date, "%Y-%m-%d")
        
    date_str = report_date.strftime("%Y-%m-%d")
    logger.info(f"Анализ выручки за дату: {date_str}")
    
    csv_paths = {}
    headless = not args.headful
    
    inputs_dir = os.path.join(base_dir, "inputs")
    if not os.path.exists(inputs_dir):
        os.makedirs(inputs_dir)
        
    # 1. Сбор CSV файлов
    if args.no_download:
        logger.info(f"Режим офлайн. Поиск файлов в папке {inputs_dir}...")
        for park_name in yandex_parks.keys():
            pattern = os.path.join(inputs_dir, f"revenue_{park_name}_{date_str}.csv")
            matches = glob.glob(pattern)
            if matches:
                csv_paths[park_name] = matches[0]
            else:
                # Попробуем найти любые другие файлы этого парка в папке inputs
                fallback = os.path.join(inputs_dir, f"revenue_{park_name}_*.csv")
                fallback_matches = glob.glob(fallback)
                if fallback_matches:
                    latest = max(fallback_matches, key=os.path.getmtime)
                    csv_paths[park_name] = latest
                    logger.info(f"Файл за {date_str} не найден для '{park_name}', используем свежий: {latest}")
                else:
                    logger.warning(f"Файлы для парка '{park_name}' отсутствуют.")
    else:
        logger.info("Режим онлайн. Скачивание отчетов...")
        for park_name, park_id in yandex_parks.items():
            try:
                csv_path = download_revenue_report(park_name, park_id, report_date, headless=headless)
                csv_paths[park_name] = csv_path
            except Exception as e:
                logger.error(f"Не удалось скачать отчет для '{park_name}': {e}")
                # Попробуем взять локальный бэкап
                fallback = os.path.join(inputs_dir, f"revenue_{park_name}_*.csv")
                fallback_matches = glob.glob(fallback)
                if fallback_matches:
                    latest = max(fallback_matches, key=os.path.getmtime)
                    csv_paths[park_name] = latest
                    logger.warning(f"Используем локальный бэкап для '{park_name}': {latest}")
                    
    # 2. Агрегация данных
    overall_counts, franchise_data, all_franchises, overall_top, franchise_top = aggregate_data(csv_paths)
    
    # 3. Генерация HTML
    outputs_dir = os.path.join(base_dir, "outputs")
    if not os.path.exists(outputs_dir):
        os.makedirs(outputs_dir)
        
    output_html_path = os.path.join(outputs_dir, "revenue_analysis.html")
    generate_html_report(overall_counts, franchise_data, all_franchises, overall_top, franchise_top, yandex_parks, output_html_path)

if __name__ == "__main__":
    main()
