# -*- coding: utf-8 -*-
import os
import sys
import glob
import json
import logging
import pandas as pd
from datetime import datetime

# Configure logging
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def clean_jewelry(val):
    if pd.isna(val):
        return "0"
    val_str = str(val).strip().lower()
    if val_str in ["не указано", "nan", "", "0", "none"]:
        return "0"
    if "bronze" in val_str:
        return "Bronze"
    return str(val).strip()

def process_city_data(city, inputs_dir):
    # Find latest revenue file for this city
    pattern = os.path.join(inputs_dir, f"revenue_{city}_*.csv")
    revenue_files = glob.glob(pattern)
    if not revenue_files:
        logger.warning(f"No revenue files found for city: {city}")
        return None
    
    # Sort to find the latest
    revenue_files.sort()
    latest_rev_file = revenue_files[-1]
    logger.info(f"Using revenue file for {city}: {latest_rev_file}")
    
    # Find vendings file
    vendings_path = os.path.join(inputs_dir, f"vendings_{city}.csv")
    if not os.path.exists(vendings_path):
        logger.warning(f"No vendings file found for city: {city} at {vendings_path}")
        return None
    logger.info(f"Using vendings file for {city}: {vendings_path}")
    
    try:
        # Read CSVs
        df_rev = pd.read_csv(latest_rev_file, decimal=',')
        df_vend = pd.read_csv(vendings_path, decimal=',')
        
        # Guardrail checks / cleaning
        if 'vending_id' not in df_rev.columns or 'office_status' not in df_rev.columns:
            logger.error(f"Required columns missing in revenue report of {city}")
            return None
        if 'DisplayNumber' not in df_vend.columns or 'OwnedBy' not in df_vend.columns:
            logger.error(f"Required columns missing in vendings report of {city}")
            return None
            
        # Keep only remove_date == "01.02.2222" / "2222-02-01"
        if 'remove_date' in df_rev.columns:
            df_rev = df_rev[df_rev['remove_date'].astype(str).str.strip().str.contains('2222-02-01|01.02.2222', regex=True)]
        else:
            logger.warning(f"'remove_date' not found in revenue file for {city}")
            
        # Standardize join keys
        df_rev['vending_id_int'] = pd.to_numeric(df_rev['vending_id'], errors='coerce')
        df_vend['DisplayNumber_int'] = pd.to_numeric(df_vend['DisplayNumber'], errors='coerce')
        
        # Merge datasets
        df_merged = pd.merge(df_rev, df_vend, left_on='vending_id_int', right_on='DisplayNumber_int', how='left')
        
        # Fill missing values and standardize string values
        df_merged['office_status_std'] = df_merged['office_status'].fillna('').astype(str).str.strip().str.lower()
        df_merged['OwnedBy_std'] = df_merged['OwnedBy'].fillna('').astype(str).str.strip().str.lower()
        df_merged['jewelry_std'] = df_merged['jewelry'].apply(clean_jewelry)
        
        # Calculate KPIs
        placed_mask = df_merged['office_status_std'].str.contains('placed')
        office_mask = df_merged['office_status_std'].str.contains('офис')
        
        placed_total = int(placed_mask.sum())
        placed_partner = int((placed_mask & (df_merged['OwnedBy_std'] == 'partner')).sum())
        placed_berizaryad = int((placed_mask & (df_merged['OwnedBy_std'] == 'berizaryad')).sum())
        
        office_total = int(office_mask.sum())
        office_partner = int((office_mask & (df_merged['OwnedBy_std'] == 'partner')).sum())
        office_berizaryad = int((office_mask & (df_merged['OwnedBy_std'] == 'berizaryad')).sum())
        
        kpi = {
            "placed_total": placed_total,
            "placed_partner": placed_partner,
            "placed_berizaryad": placed_berizaryad,
            "office_total": office_total,
            "office_partner": office_partner,
            "office_berizaryad": office_berizaryad
        }
        
        # Table data filters: only "placed" AND (jewelry == "0" OR jewelry == "Bronze")
        df_table_source = df_merged[placed_mask].copy()
        
        # Extract row info helper
        def extract_rows(df_subset):
            rows = []
            for _, r in df_subset.iterrows():
                # Safe casting
                v_id = int(r['vending_id_int']) if pd.notna(r['vending_id_int']) else int(r['vending_id'])
                rows.append({
                    "vending_id": v_id,
                    "place_name": str(r.get('place_name', r.get('PlaceName', ''))).strip(),
                    "address": str(r.get('address_x', r.get('Address_x', r.get('address', '')))).strip(),
                    "model": str(r.get('model', r.get('VendingType', ''))).strip(),
                    "OwnedBy": str(r.get('OwnedBy', '')).strip(),
                    "fact": float(r.get('fact', 0)) if pd.notna(r.get('fact')) else 0.0,
                    "orders": int(r.get('orders', 0)) if pd.notna(r.get('orders')) else 0,
                    "cell_turnover": float(r.get('cell_turnover', 0)) if pd.notna(r.get('cell_turnover')) else 0.0,
                    "cells_total": int(r.get('cells_total', 0)) if pd.notna(r.get('cells_total')) else 0,
                    "onplace_time": int(r.get('onplace_time', 0)) if pd.notna(r.get('onplace_time')) else 0
                })
            return rows
            
        jewelry_0_rows = extract_rows(df_table_source[df_table_source['jewelry_std'] == '0'])
        jewelry_bronze_rows = extract_rows(df_table_source[df_table_source['jewelry_std'] == 'Bronze'])
        
        # Фильтрация для изменения объема ячеек
        # 1. Увеличение объема
        increase_mask = (df_table_source['cell_turnover'] > 0.5) | (
            (df_table_source['cell_turnover'] > 0.4) & (
                ((df_table_source['cells_total'].isin([6, 3])) & (df_table_source['fact'] >= 10000)) |
                ((df_table_source['cells_total'] == 12) & (df_table_source['fact'] >= 15000)) |
                ((~df_table_source['cells_total'].isin([6, 3, 12])) & (df_table_source['fact'] > 20000))
            )
        )
        
        # 2. Уменьшение объема
        decrease_mask = (df_table_source['cells_total'] >= 12) & (
            (df_table_source['cell_turnover'] < 0.15) |
            ((df_table_source['cell_turnover'] >= 0.15) & (df_table_source['fact'] < 10000))
        )
        
        # Загрузка оффлайн-станций из кэша
        disconnected_rows = []
        disconnected_path = os.path.join(inputs_dir, f"disconnected_{city}.json")
        if os.path.exists(disconnected_path):
            try:
                with open(disconnected_path, "r", encoding="utf-8") as f:
                    disconnected_rows = json.load(f)
            except Exception as e:
                logger.error(f"Error loading disconnected JSON for {city}: {e}")
                
        volume_increase_rows = extract_rows(df_table_source[increase_mask])
        volume_decrease_rows = extract_rows(df_table_source[decrease_mask])
        
        return {
            "kpi": kpi,
            "jewelry_0": jewelry_0_rows,
            "jewelry_bronze": jewelry_bronze_rows,
            "volume_increase": volume_increase_rows,
            "volume_decrease": volume_decrease_rows,
            "disconnected": disconnected_rows
        }
    except Exception as e:
        logger.error(f"Error processing city {city}: {e}", exc_info=True)
        return None

def main():
    config = load_config()
    yandex_parks = config.get("yandex_parks", {})
    if not yandex_parks:
        logger.error("No 'yandex_parks' found in config.json")
        sys.exit(1)
        
    base_dir = os.path.dirname(os.path.abspath(__file__))
    inputs_dir = os.path.join(base_dir, "inputs")
    
    city_data = {}
    
    # Process each city
    for city in yandex_parks.keys():
        res = process_city_data(city, inputs_dir)
        if res:
            city_data[city] = res
            
    if not city_data:
        logger.error("No data could be processed for any city.")
        sys.exit(1)
        
    # Aggregate "Общее"
    total_kpi = {
        "placed_total": 0,
        "placed_partner": 0,
        "placed_berizaryad": 0,
        "office_total": 0,
        "office_partner": 0,
        "office_berizaryad": 0
    }
    total_j0 = []
    total_jb = []
    total_vol_inc = []
    total_vol_dec = []
    total_disconnected = []
    
    for city, data in city_data.items():
        for k in total_kpi.keys():
            total_kpi[k] += data["kpi"][k]
        # Append city info to rows to know which city they belong to in the "Общее" view
        for r in data["jewelry_0"]:
            r_copy = r.copy()
            r_copy["city"] = city
            total_j0.append(r_copy)
        for r in data["jewelry_bronze"]:
            r_copy = r.copy()
            r_copy["city"] = city
            total_jb.append(r_copy)
        for r in data.get("volume_increase", []):
            r_copy = r.copy()
            r_copy["city"] = city
            total_vol_inc.append(r_copy)
        for r in data.get("volume_decrease", []):
            r_copy = r.copy()
            r_copy["city"] = city
            total_vol_dec.append(r_copy)
        for r in data.get("disconnected", []):
            r_copy = r.copy()
            r_copy["city"] = city
            total_disconnected.append(r_copy)
            
    city_data["Общее"] = {
        "kpi": total_kpi,
        "jewelry_0": total_j0,
        "jewelry_bronze": total_jb,
        "volume_increase": total_vol_inc,
        "volume_decrease": total_vol_dec,
        "disconnected": total_disconnected
    }
    
    # Prepare HTML template
    html_template = r"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>План развития городов (Июнь 2026)</title>
    <meta name="description" content="Интерактивный отчет по аналитике текущей ситуации размещения аппаратов по городам">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 28, 45, 0.6);
            --card-border: rgba(255, 255, 255, 0.08);
            --accent-primary: #3b82f6;
            --accent-primary-hover: #2563eb;
            --accent-purple: #8b5cf6;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --bronze: #cd7f32;
            --font-outfit: 'Outfit', 'Inter', sans-serif;
            --font-inter: 'Inter', sans-serif;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: var(--font-inter);
            min-height: 100vh;
            background-image: 
                radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.1) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(139, 92, 246, 0.1) 0px, transparent 50%);
            background-attachment: fixed;
            padding: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 1.5rem 2rem;
            border-radius: 16px;
            backdrop-filter: blur(12px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }

        h1 {
            font-family: var(--font-outfit);
            font-size: 1.8rem;
            font-weight: 700;
            background: linear-gradient(135deg, #60a5fa, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .selector-container {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .selector-label {
            font-size: 0.9rem;
            color: var(--text-muted);
            font-weight: 500;
        }

        select {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 0.6rem 2.5rem 0.6rem 1.2rem;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            outline: none;
            transition: all 0.3s ease;
            appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 1rem center;
            background-size: 1.2rem;
        }

        select:hover, select:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }

        /* KPI Dashboard Grid */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .kpi-master-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.3s ease, box-shadow 0.3s ease;
            position: relative;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
        }

        .kpi-master-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 5px;
            height: 100%;
        }

        .kpi-master-card.placed::before {
            background: linear-gradient(to bottom, #3b82f6, #10b981);
        }

        .kpi-master-card.office::before {
            background: linear-gradient(to bottom, #8b5cf6, #f59e0b);
        }

        .kpi-master-card:hover {
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 12px 24px rgba(0, 0, 0, 0.4);
        }

        .kpi-master-header {
            font-family: var(--font-outfit);
            font-size: 1.15rem;
            font-weight: 600;
            color: var(--text-main);
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .kpi-master-body {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1rem;
            text-align: center;
        }

        .kpi-sub-item {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            padding: 0.75rem 0.5rem;
            border-radius: 10px;
            background: rgba(15, 23, 42, 0.35);
            border: 1px solid rgba(255, 255, 255, 0.02);
            transition: background-color 0.2s;
        }

        .kpi-sub-item:hover {
            background: rgba(15, 23, 42, 0.5);
        }

        .kpi-sub-title {
            font-size: 0.8rem;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .kpi-sub-value {
            font-family: var(--font-outfit);
            font-size: 1.8rem;
            font-weight: 700;
            color: #ffffff;
        }

        /* Tabs System */
        .tabs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 0.5rem;
            gap: 1.5rem;
            flex-wrap: wrap;
        }

        .tabs-nav {
            display: flex;
            gap: 0.5rem;
        }

        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 0.8rem 1.5rem;
            font-size: 1rem;
            font-weight: 600;
            font-family: var(--font-outfit);
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }

        .tab-btn:hover {
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.03);
        }

        .tab-btn.active {
            color: var(--text-main);
            background: rgba(59, 130, 246, 0.15);
            box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.3);
        }

        .tab-badge {
            background: rgba(255, 255, 255, 0.1);
            color: var(--text-muted);
            padding: 0.2rem 0.6rem;
            font-size: 0.8rem;
            border-radius: 20px;
            font-weight: 600;
            transition: all 0.3s ease;
        }

        .tab-btn.active .tab-badge {
            background: var(--accent-primary);
            color: #ffffff;
        }

        .search-container {
            position: relative;
            flex-grow: 1;
            max-width: 400px;
        }

        .search-input {
            width: 100%;
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 0.7rem 1rem 0.7rem 2.5rem;
            border-radius: 10px;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.3s ease;
        }

        .search-input:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
        }

        .search-icon {
            position: absolute;
            left: 0.9rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            width: 1.1rem;
            height: 1.1rem;
            pointer-events: none;
        }

        /* Table section */
        .table-wrapper {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            overflow-x: auto;
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
            max-height: 600px;
            overflow-y: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }

        th {
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-muted);
            font-weight: 600;
            padding: 1rem 1.2rem;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--card-border);
            position: sticky;
            top: 0;
            z-index: 10;
            cursor: pointer;
            user-select: none;
        }

        th:hover {
            color: var(--text-main);
            background: rgba(30, 41, 59, 0.9);
        }

        th::after {
            content: ' ↕';
            font-size: 0.75rem;
            opacity: 0.4;
        }

        th.sort-asc::after {
            content: ' ▲';
            opacity: 1;
            color: var(--accent-primary);
        }

        th.sort-desc::after {
            content: ' ▼';
            opacity: 1;
            color: var(--accent-primary);
        }

        td {
            padding: 1rem 1.2rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-main);
            transition: background-color 0.2s ease;
        }

        tr:hover td {
            background-color: rgba(255, 255, 255, 0.02);
        }

        .badge {
            display: inline-block;
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .badge-partner {
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        .badge-berizaryad {
            background: rgba(139, 92, 246, 0.15);
            color: #a78bfa;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }

        .badge-city {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-main);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .badge-indigo {
            background: rgba(99, 102, 241, 0.15);
            color: #818cf8;
            border: 1px solid rgba(99, 102, 241, 0.3);
        }

        .cell-turnover {
            font-weight: 600;
        }
        .turnover-high {
            color: var(--danger);
        }
        .turnover-medium {
            color: var(--warning);
        }
        .turnover-low {
            color: var(--success);
        }

        /* Разделы Kanban и Задач */
        .section-container {
            margin-top: 3rem;
            margin-bottom: 2rem;
        }

        .section-title {
            font-family: var(--font-outfit);
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 1.5rem;
            color: #ffffff;
            border-left: 4px solid var(--accent-primary);
            padding-left: 0.75rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        /* Канбан-доска */
        .kanban-board {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .kanban-column {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.2rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            min-height: 450px;
            backdrop-filter: blur(12px);
        }

        .kanban-column-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 600;
            font-family: var(--font-outfit);
            font-size: 1.1rem;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 0.75rem;
            color: var(--text-main);
        }

        .add-card-btn {
            background: rgba(59, 130, 246, 0.15);
            border: none;
            color: #60a5fa;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: bold;
        }

        .add-card-btn:hover {
            background: var(--accent-primary);
            color: #ffffff;
        }

        .kanban-cards {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            flex-grow: 1;
            overflow-y: auto;
            max-height: 500px;
            padding: 0.2rem;
        }

        .kanban-card {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 1rem;
            cursor: grab;
            transition: all 0.3s ease;
            position: relative;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .kanban-card:hover {
            border-color: rgba(255, 255, 255, 0.15);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .kanban-card:active {
            cursor: grabbing;
        }

        .kanban-card-text {
            font-size: 0.95rem;
            color: var(--text-main);
            outline: none;
            word-break: break-word;
        }

        .kanban-card-actions {
            display: flex;
            justify-content: flex-end;
            gap: 0.4rem;
            margin-top: 0.5rem;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 0.5rem;
        }

        .card-action-btn {
            background: transparent;
            border: none;
            cursor: pointer;
            font-size: 0.8rem;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            transition: all 0.2s;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 0.2rem;
        }

        .card-action-btn:hover {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-main);
        }

        .card-btn-delete:hover {
            color: var(--danger);
            background: rgba(239, 68, 68, 0.1);
        }

        /* Таск-менеджер */
        .tasks-manager {
            display: grid;
            grid-template-columns: 350px 1fr;
            gap: 1.5rem;
        }

        .task-input-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.2rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            backdrop-filter: blur(12px);
            align-self: start;
        }

        .task-input-card textarea {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            color: var(--text-main);
            padding: 0.8rem;
            font-size: 0.95rem;
            resize: none;
            height: 120px;
            outline: none;
            transition: border-color 0.2s;
            font-family: inherit;
        }

        .task-input-card textarea:focus {
            border-color: var(--accent-primary);
        }

        .save-task-btn {
            background: var(--accent-primary);
            border: none;
            color: #ffffff;
            padding: 0.8rem;
            border-radius: 10px;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.2s;
            font-family: var(--font-outfit);
        }

        .save-task-btn:hover {
            background: var(--accent-primary-hover);
        }

        .tasks-list {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.2rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            backdrop-filter: blur(12px);
            max-height: 400px;
            overflow-y: auto;
        }

        .task-item {
            background: rgba(15, 23, 42, 0.4);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 1rem;
            display: flex;
            align-items: flex-start;
            gap: 1rem;
            transition: background-color 0.2s, opacity 0.2s;
        }

        .task-item.completed {
            opacity: 0.6;
            background: rgba(15, 23, 42, 0.1);
        }

        .task-checkbox-container {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 1.2rem;
        }

        .task-checkbox {
            width: 1.2rem;
            height: 1.2rem;
            cursor: pointer;
            accent-color: var(--accent-primary);
        }

        .task-content {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            gap: 0.4rem;
        }

        .task-text {
            font-size: 0.95rem;
            color: var(--text-main);
            word-break: break-word;
            line-height: 1.4;
        }

        .task-item.completed .task-text {
            text-decoration: line-through;
            color: var(--text-muted);
        }

        .task-dates {
            font-size: 0.75rem;
            color: var(--text-muted);
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
        }

        .task-date-created::before {
            content: 'Создано: ';
            color: var(--text-muted);
        }

        .task-date-completed {
            color: var(--success);
        }

        .task-date-completed::before {
            content: 'Выполнено: ';
            color: var(--text-muted);
        }

        .task-delete-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            transition: color 0.2s;
            padding: 0.2rem;
            align-self: center;
        }

        .task-delete-btn:hover {
            color: var(--danger);
        }

        /* Фильтры и Решения */
        .filter-controls {
            display: flex;
            gap: 1rem;
            align-items: center;
            flex-grow: 1;
            max-width: 600px;
            justify-content: flex-end;
        }

        .decision-filter-select {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 0.6rem 2rem 0.6rem 1rem;
            border-radius: 10px;
            font-size: 0.9rem;
            cursor: pointer;
            appearance: none;
            outline: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 0.75rem center;
            background-size: 1rem;
            min-width: 180px;
            transition: border-color 0.2s;
        }

        .decision-filter-select:focus {
            border-color: var(--accent-primary);
        }

        /* Выпадающий список решения в таблице */
        .status-select {
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid var(--card-border);
            color: var(--text-muted);
            padding: 0.35rem 1.75rem 0.35rem 0.6rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            appearance: none;
            outline: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 0.5rem center;
            background-size: 0.8rem;
            transition: all 0.2s ease;
        }

        .status-select:focus {
            border-color: var(--accent-primary);
        }

        .status-select.status-dismantle {
            color: #f87171 !important;
            border-color: rgba(239, 68, 68, 0.4) !important;
            background-color: rgba(239, 68, 68, 0.1) !important;
        }

        .status-select.status-account {
            color: #60a5fa !important;
            border-color: rgba(59, 130, 246, 0.4) !important;
            background-color: rgba(59, 130, 246, 0.1) !important;
        }

        .status-select.status-b2b_wait {
            color: #fbbf24 !important;
            border-color: rgba(245, 158, 11, 0.4) !important;
            background-color: rgba(245, 158, 11, 0.1) !important;
        }

        .status-select.status-module_add {
            color: #34d399 !important;
            border-color: rgba(52, 211, 153, 0.4) !important;
            background-color: rgba(52, 211, 153, 0.1) !important;
        }
        .status-select.status-second_station {
            color: #c084fc !important;
            border-color: rgba(192, 132, 252, 0.4) !important;
            background-color: rgba(192, 132, 252, 0.1) !important;
        }
        .status-select.status-refusal {
            color: #f87171 !important;
            border-color: rgba(239, 68, 68, 0.4) !important;
            background-color: rgba(239, 68, 68, 0.1) !important;
        }
        .status-select.status-module_remove {
            color: #fb923c !important;
            border-color: rgba(251, 146, 60, 0.4) !important;
            background-color: rgba(251, 146, 60, 0.1) !important;
        }
        .status-select.status-recently_placed {
            color: #60a5fa !important;
            border-color: rgba(96, 165, 250, 0.4) !important;
            background-color: rgba(96, 165, 250, 0.1) !important;
        }
        .status-select.status-visit {
            color: #2dd4bf !important;
            border-color: rgba(45, 212, 191, 0.4) !important;
            background-color: rgba(45, 212, 191, 0.1) !important;
        }
        .status-select.status-negotiate {
            color: #f472b6 !important;
            border-color: rgba(244, 114, 182, 0.4) !important;
            background-color: rgba(244, 114, 182, 0.1) !important;
        }
        .status-select.status-call {
            color: #a78bfa !important;
            border-color: rgba(167, 139, 250, 0.4) !important;
            background-color: rgba(167, 139, 250, 0.1) !important;
        }
        .status-select.status-account_done {
            color: #10b981 !important;
            border-color: rgba(16, 185, 129, 0.4) !important;
            background-color: rgba(16, 185, 129, 0.1) !important;
        }

        .empty-state {
            padding: 4rem 2rem;
            text-align: center;
            color: var(--text-muted);
            font-size: 1.1rem;
        }

        @media (max-width: 768px) {
            body {
                padding: 1rem;
            }
            header {
                flex-direction: column;
                gap: 1rem;
                align-items: stretch;
            }
            .tabs-header {
                flex-direction: column;
                align-items: stretch;
            }
            .search-container {
                max-width: 100%;
            }
        }

        /* Аккордеоны / Спойлеры */
        .accordion-section {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            margin-bottom: 1.5rem;
            overflow: hidden;
            backdrop-filter: blur(12px);
            transition: border-color 0.3s ease;
        }

        .accordion-section:hover {
            border-color: rgba(255, 255, 255, 0.15);
        }

        .accordion-header {
            padding: 1.25rem 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            user-select: none;
            transition: background-color 0.2s;
        }

        .accordion-header:hover {
            background: rgba(255, 255, 255, 0.02);
        }

        .accordion-header h2 {
            margin: 0;
            border-left: none;
            padding-left: 0;
            display: flex;
            align-items: center;
        }

        .accordion-icon {
            transition: transform 0.3s ease;
            color: var(--text-muted);
        }

        .accordion-section.active .accordion-icon {
            transform: rotate(180deg);
            color: var(--accent-primary);
        }

        .accordion-content {
            max-height: 0;
            opacity: 0;
            overflow: hidden;
            padding: 0 1.5rem;
            transition: max-height 0.4s cubic-bezier(0, 1, 0, 1), opacity 0.3s ease, padding 0.4s ease;
        }

        .accordion-section.active .accordion-content {
            max-height: 3000px;
            opacity: 1;
            padding: 0 1.5rem 1.5rem 1.5rem;
            overflow: visible;
            transition: max-height 0.4s cubic-bezier(0.99, 0, 1, 1), opacity 0.3s ease, padding 0.4s ease;
        }

        /* Боковое меню навигации */
        .quick-nav {
            position: fixed;
            right: 2rem;
            top: 50%;
            transform: translateY(-50%);
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            z-index: 1000;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--card-border);
            padding: 0.75rem;
            border-radius: 30px;
            backdrop-filter: blur(16px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        }

        .quick-nav-btn {
            width: 42px;
            height: 42px;
            border-radius: 50%;
            border: 1px solid rgba(255, 255, 255, 0.05);
            background: rgba(30, 41, 59, 0.5);
            color: var(--text-muted);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s ease;
            position: relative;
        }

        .quick-nav-btn:hover {
            background: var(--accent-primary);
            color: #ffffff;
            border-color: var(--accent-primary);
            transform: scale(1.1);
        }

        .quick-nav-btn.active {
            background: var(--accent-primary);
            color: #ffffff;
            border-color: var(--accent-primary);
            box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
        }

        /* Тултипы */
        .quick-nav-btn::after {
            content: attr(data-tooltip);
            position: absolute;
            right: 55px;
            top: 50%;
            transform: translateY(-50%) scale(0.9);
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 0.4rem 0.8rem;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 500;
            white-space: nowrap;
            opacity: 0;
            pointer-events: none;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }

        .quick-nav-btn:hover::after {
            opacity: 1;
            transform: translateY(-50%) scale(1);
        }

        /* Плавная прокрутка */
        html {
            scroll-behavior: smooth;
        }

        /* История изменений */
        .history-item {
            display: flex;
            gap: 1rem;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.02);
            margin-bottom: 0.5rem;
            font-size: 0.9rem;
            align-items: center;
        }

        .history-time {
            color: var(--text-muted);
            font-size: 0.8rem;
            font-family: var(--font-outfit);
            white-space: nowrap;
        }

        .history-city-badge {
            background: rgba(59, 130, 246, 0.1);
            color: var(--accent-primary);
            padding: 0.2rem 0.5rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            white-space: nowrap;
        }

        .history-details {
            color: var(--text-main);
            flex-grow: 1;
        }

        @media (max-width: 1200px) {
            .quick-nav {
                display: none;
            }
        }
    </style>
</head>
<body>
    <header>
        <h1>План развития городов</h1>
        <div class="selector-container">
            <span class="selector-label">Выбрать город:</span>
            <select id="city-selector" onchange="switchCity(this.value)">
                <option value="Общее">Общее (Все города)</option>
            </select>
        </div>
    </header>

    <!-- Боковое меню быстрого перехода -->
    <div class="quick-nav">
        <button class="quick-nav-btn active" data-tooltip="Панель KPI" onclick="scrollToSection('kpi-section')" id="nav-kpi-section">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="Таблицы категорий" onclick="scrollToSection('section-categories')" id="nav-section-categories">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="Установки (Канбан)" onclick="scrollToSection('section-kanban')" id="nav-section-kanban">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/><line x1="3" y1="9" x2="21" y2="9"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="Задачи (Таски)" onclick="scrollToSection('section-tasks')" id="nav-section-tasks">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="Объем ячеек" onclick="scrollToSection('section-volume')" id="nav-section-volume">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.21 15.89A10 10 0 1 1 8 2.83M22 12A10 10 0 0 0 12 2v10z"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="Давно оффлайн" onclick="scrollToSection('section-disconnected')" id="nav-section-disconnected">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        </button>
        <button class="quick-nav-btn" data-tooltip="История изменений" onclick="scrollToSection('section-history')" id="nav-section-history">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        </button>
    </div>

    <main>
        <!-- KPI Row -->
        <div class="kpi-grid" id="kpi-section">
            <!-- Станции в полях -->
            <div class="kpi-master-card placed">
                <div class="kpi-master-header">Станции в полях</div>
                <div class="kpi-master-body">
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Всего</div>
                        <div class="kpi-sub-value" id="kpi-placed-total">0</div>
                    </div>
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Наши</div>
                        <div class="kpi-sub-value" id="kpi-placed-partner">0</div>
                    </div>
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Управление</div>
                        <div class="kpi-sub-value" id="kpi-placed-berizaryad">0</div>
                    </div>
                </div>
            </div>
            <!-- Остатки за офисом -->
            <div class="kpi-master-card office">
                <div class="kpi-master-header">Остатки за офисом</div>
                <div class="kpi-master-body">
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Всего</div>
                        <div class="kpi-sub-value" id="kpi-office-total">0</div>
                    </div>
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Наши</div>
                        <div class="kpi-sub-value" id="kpi-office-partner">0</div>
                    </div>
                    <div class="kpi-sub-item">
                        <div class="kpi-sub-title">Управление</div>
                        <div class="kpi-sub-value" id="kpi-office-berizaryad">0</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Section: Categories Tables -->
        <section class="accordion-section" id="section-categories">
            <div class="accordion-header" onclick="toggleAccordion('categories')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--accent-primary);"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
                    Таблицы по категориям
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-categories">
                <div class="tabs-header" style="margin-top: 1rem;">
                    <div class="tabs-nav">
                        <button class="tab-btn active" onclick="switchTab('j0')">
                            Категория '0'
                            <span class="tab-badge" id="badge-j0">0</span>
                        </button>
                        <button class="tab-btn" onclick="switchTab('jb')">
                            Категория 'Bronze'
                            <span class="tab-badge" id="badge-jb">0</span>
                        </button>
                    </div>
                    <div class="filter-controls">
                        <select id="decision-filter" onchange="handleDecisionFilter()" class="decision-filter-select">
                            <option value="all">Все решения</option>
                            <option value="none">Не выбрано (—)</option>
                            <option value="dismantle">Демонтировать</option>
                            <option value="account">Проаккаунтить</option>
                            <option value="b2b_wait">Ожидание B2B</option>
                            <option value="account_done">Аккаунтинг проведен</option>
                        </select>
                        <div class="search-container">
                            <svg class="search-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                            </svg>
                            <input type="text" class="search-input" id="search-box" placeholder="Поиск по ID, адресу..." oninput="handleSearch()">
                        </div>
                    </div>
                </div>

                <div class="table-wrapper">
                    <table id="data-table">
                        <thead>
                            <tr id="table-headers">
                                <!-- Headers injected dynamically -->
                            </tr>
                        </thead>
                        <tbody id="table-body">
                            <!-- Rows injected dynamically -->
                        </tbody>
                    </table>
                    <div class="empty-state" id="empty-state" style="display: none;">
                        Ничего не найдено
                    </div>
                </div>
            </div>
        </section>

        <!-- Установки (Kanban-доска) -->
        <section class="accordion-section" id="section-kanban">
            <div class="accordion-header" onclick="toggleAccordion('kanban')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--accent-primary);"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line><line x1="15" y1="3" x2="15" y2="21"></line><line x1="3" y1="9" x2="21" y2="9"></line></svg>
                    Установки
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-kanban">
                <div class="kanban-board" style="margin-top: 1rem;">
                    <div class="kanban-column" id="col-potential">
                        <div class="kanban-column-header">
                            <span>Потенциальные</span>
                            <button class="add-card-btn" onclick="createKanbanCard('potential')">+</button>
                        </div>
                        <div class="kanban-cards" id="cards-potential" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'potential')"></div>
                    </div>
                    <div class="kanban-column" id="col-agreed">
                        <div class="kanban-column-header">
                            <span>100% договоренности</span>
                            <button class="add-card-btn" onclick="createKanbanCard('agreed')">+</button>
                        </div>
                        <div class="kanban-cards" id="cards-agreed" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'agreed')"></div>
                    </div>
                    <div class="kanban-column" id="col-installed">
                        <div class="kanban-column-header">
                            <span>Установлено</span>
                            <button class="add-card-btn" onclick="createKanbanCard('installed')">+</button>
                        </div>
                        <div class="kanban-cards" id="cards-installed" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'installed')"></div>
                    </div>
                </div>
            </div>
        </section>

        <!-- Задачи (Таск-менеджер) -->
        <section class="accordion-section" id="section-tasks">
            <div class="accordion-header" onclick="toggleAccordion('tasks')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--accent-purple);"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
                    Задачи
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-tasks">
                <div class="tasks-manager" style="margin-top: 1rem;">
                    <div class="task-input-card">
                        <textarea id="task-text-input" placeholder="Введите текст задачи для этого города..."></textarea>
                        <button class="save-task-btn" onclick="saveTask()">Сохранить</button>
                    </div>
                    <div class="tasks-list" id="tasks-list">
                        <!-- Задачи будут отрендерены через JS -->
                    </div>
                </div>
            </div>
        </section>

        <!-- Станции, которые давно не в сети -->
        <section class="accordion-section" id="section-disconnected">
            <div class="accordion-header" onclick="toggleAccordion('disconnected')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--danger);"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                    Станции, которые давно не в сети
                    <span class="tab-badge" id="badge-disconnected-count" style="margin-left: 8px; background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid rgba(239, 68, 68, 0.3);">0</span>
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-disconnected">
                <div class="table-wrapper" style="margin-top: 1rem;">
                    <table id="disconnected-table">
                        <thead>
                            <tr id="disconnected-table-headers">
                                <!-- Headers injected dynamically -->
                            </tr>
                        </thead>
                        <tbody id="disconnected-table-body">
                            <!-- Rows injected dynamically -->
                        </tbody>
                    </table>
                    <div class="empty-state" id="disconnected-empty-state" style="display: none;">
                        Все станции в сети
                    </div>
                </div>
            </div>
        </section>

        <!-- Увеличение/уменьшение объема аппарата -->
        <section class="accordion-section" id="section-volume">
            <div class="accordion-header" onclick="toggleAccordion('volume')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--accent-primary);"><path d="M21.21 15.89A10 10 0 1 1 8 2.83M22 12A10 10 0 0 0 12 2v10z"></path></svg>
                    Увеличение/уменьшение объема аппарата
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-volume">
                <div class="tabs-header" style="margin-top: 1rem;">
                    <div class="tabs-nav">
                        <button class="tab-btn active" id="btn-vol-inc" onclick="switchVolumeTab('increase')">
                            На увеличение объема
                            <span class="tab-badge" id="badge-vol-inc">0</span>
                        </button>
                        <button class="tab-btn" id="btn-vol-dec" onclick="switchVolumeTab('decrease')">
                            На уменьшение объема
                            <span class="tab-badge" id="badge-vol-dec">0</span>
                        </button>
                    </div>
                </div>

                <div class="table-wrapper">
                    <table id="volume-table">
                        <thead>
                            <tr id="volume-table-headers">
                                <!-- Headers injected dynamically -->
                            </tr>
                        </thead>
                        <tbody id="volume-table-body">
                            <!-- Rows injected dynamically -->
                        </tbody>
                    </table>
                    <div class="empty-state" id="volume-empty-state" style="display: none;">
                        Ничего не найдено
                    </div>
                </div>
            </div>
        </section>

        <!-- История изменений -->
        <section class="accordion-section" id="section-history">
            <div class="accordion-header" onclick="toggleAccordion('history')">
                <h2 class="section-title" style="margin: 0; border: none; font-size: 1.25rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 8px; color: var(--accent-primary);"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    История изменений
                </h2>
                <svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </div>
            <div class="accordion-content" id="content-history">
                <div class="history-filters" style="display: flex; gap: 1rem; margin-top: 1rem; margin-bottom: 1rem; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        <span style="color: var(--text-muted); font-size: 0.85rem;">Дата:</span>
                        <input type="date" id="history-date-filter" class="search-input" style="width: auto; min-width: 150px; background: rgba(15, 23, 42, 0.5); border: 1px solid var(--card-border); color: var(--text-light); padding: 0.4rem 0.6rem; border-radius: 6px;" onchange="renderHistory()">
                        <button onclick="clearHistoryDate()" style="background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: var(--danger); padding: 0.4rem 0.6rem; border-radius: 6px; cursor: pointer; transition: all 0.2s;" onmouseover="this.style.background='rgba(239, 68, 68, 0.25)';" onmouseout="this.style.background='rgba(239, 68, 68, 0.15)';">Сброс</button>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem; flex-grow: 1; max-width: 400px;">
                        <span style="color: var(--text-muted); font-size: 0.85rem;">Поиск:</span>
                        <input type="text" id="history-search-input" placeholder="Введите номер аппарата..." class="search-input" style="flex-grow: 1; background: rgba(15, 23, 42, 0.5); border: 1px solid var(--card-border); color: var(--text-light); padding: 0.4rem 0.6rem; border-radius: 6px;" oninput="renderHistory()">
                    </div>
                </div>
                <div class="history-list" id="history-list" style="margin-top: 1rem; max-height: 400px; overflow-y: auto; padding-right: 0.5rem;">
                    <!-- Логи будут отрендерены через JS -->
                </div>
            </div>
        </section>
    </main>

    <script>
        const reportData = {{REPORT_DATA}};
        let currentCity = 'Общее';
        let currentTab = 'j0'; // 'j0' or 'jb'
        let currentVolumeTab = 'increase'; // 'increase' or 'decrease'
        let searchQuery = '';
        let sortColumn = null;
        let sortAsc = true;
        let volumeSortColumn = null;
        let volumeSortAsc = true;
        let disconnectedSortColumn = null;
        let disconnectedSortAsc = true;

        // Populate selector
        const selector = document.getElementById('city-selector');
        Object.keys(reportData).sort().forEach(city => {
            if (city !== 'Общее') {
                const opt = document.createElement('option');
                opt.value = city;
                opt.textContent = city;
                selector.appendChild(opt);
            }
        });

        function formatCurrency(val) {
            return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0 }).format(val);
        }

        function getTurnoverClass(turnover) {
            if (turnover > 0.5) return 'turnover-high';
            if (turnover >= 0.4) return 'turnover-medium';
            return 'turnover-low';
        }

        function switchCity(city) {
            currentCity = city;
            updateDashboard();
        }

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            if (tab === 'j0') {
                document.querySelector('.tab-btn[onclick="switchTab(\'j0\')"]').classList.add('active');
            } else {
                document.querySelector('.tab-btn[onclick="switchTab(\'jb\')"]').classList.add('active');
            }
            sortColumn = null;
            renderTable();
        }

        function handleSearch() {
            searchQuery = document.getElementById('search-box').value.toLowerCase();
            renderTable();
        }

        function updateDashboard() {
            const data = reportData[currentCity];
            
            // Update KPIs
            document.getElementById('kpi-placed-total').textContent = data.kpi.placed_total;
            document.getElementById('kpi-placed-partner').textContent = data.kpi.placed_partner;
            document.getElementById('kpi-placed-berizaryad').textContent = data.kpi.placed_berizaryad;
            document.getElementById('kpi-office-total').textContent = data.kpi.office_total;
            document.getElementById('kpi-office-partner').textContent = data.kpi.office_partner;
            document.getElementById('kpi-office-berizaryad').textContent = data.kpi.office_berizaryad;

            // Update badges
            document.getElementById('badge-j0').textContent = data.jewelry_0.length;
            document.getElementById('badge-jb').textContent = data.jewelry_bronze.length;
            document.getElementById('badge-vol-inc').textContent = (data.volume_increase || []).length;
            document.getElementById('badge-vol-dec').textContent = (data.volume_decrease || []).length;
            document.getElementById('badge-disconnected-count').textContent = (data.disconnected || []).length;

            sortColumn = null;
            volumeSortColumn = null;
            disconnectedSortColumn = null;
            
            // Load from database instead of offline render
            loadServerData();
        }

        function handleSort(colIndex, type) {
            if (sortColumn === colIndex) {
                sortAsc = !sortAsc;
            } else {
                sortColumn = colIndex;
                sortAsc = true;
            }
            
            // Visual header indicators
            const headers = document.querySelectorAll('th');
            headers.forEach((h, idx) => {
                h.classList.remove('sort-asc', 'sort-desc');
                if (idx === colIndex) {
                    h.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
                }
            });

            renderTable();
        }

        function renderTable() {
            const data = reportData[currentCity];
            const rows = currentTab === 'j0' ? data.jewelry_0 : data.jewelry_bronze;
            const tbody = document.getElementById('table-body');
            const headersRow = document.getElementById('table-headers');
            const emptyState = document.getElementById('empty-state');
            
            tbody.innerHTML = '';
            
            // Build headers depending on whether it's overall or city view
            const showCityCol = currentCity === 'Общее';
            
            headersRow.innerHTML = `
                <th onclick="handleSort(0, 'int')">ID</th>
                ${showCityCol ? `<th onclick="handleSort(1, 'str')">Город</th>` : ''}
                <th onclick="handleSort(${showCityCol ? 2 : 1}, 'str')">Локация</th>
                <th onclick="handleSort(${showCityCol ? 3 : 2}, 'str')">Адрес</th>
                <th onclick="handleSort(${showCityCol ? 4 : 3}, 'str')">Модель</th>
                <th onclick="handleSort(${showCityCol ? 5 : 4}, 'str')">Владелец</th>
                <th onclick="handleSort(${showCityCol ? 6 : 5}, 'num')">Выручка</th>
                <th onclick="handleSort(${showCityCol ? 7 : 6}, 'int')">Заказы</th>
                <th onclick="handleSort(${showCityCol ? 8 : 7}, 'num')">Оборачиваемость</th>
                <th onclick="handleSort(${showCityCol ? 9 : 8}, 'str')">Решение</th>
            `;

            // Filter rows by search query and decision filter
            const decisionFilter = document.getElementById('decision-filter').value;

            let filteredRows = rows.filter(r => {
                const searchStr = `${r.vending_id} ${r.place_name} ${r.address} ${r.model} ${r.OwnedBy} ${r.city || ''}`.toLowerCase();
                if (!searchStr.includes(searchQuery)) return false;

                const status = getStationStatus(r.vending_id) || 'none';
                if (decisionFilter !== 'all') {
                    if (decisionFilter === 'none') {
                        return status === 'none';
                    }
                    return status === decisionFilter;
                }
                return true;
            });

            // Sort rows
            if (sortColumn !== null) {
                filteredRows.sort((a, b) => {
                    let valA, valB;
                    if (showCityCol) {
                        if (sortColumn === 0) { valA = a.vending_id; valB = b.vending_id; }
                        else if (sortColumn === 1) { valA = a.city || ''; valB = b.city || ''; }
                        else if (sortColumn === 2) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (sortColumn === 3) { valA = a.address || ''; valB = b.address || ''; }
                        else if (sortColumn === 4) { valA = a.model || ''; valB = b.model || ''; }
                        else if (sortColumn === 5) { valA = a.OwnedBy || ''; valB = b.OwnedBy || ''; }
                        else if (sortColumn === 6) { valA = a.fact; valB = b.fact; }
                        else if (sortColumn === 7) { valA = a.orders; valB = b.orders; }
                        else if (sortColumn === 8) { valA = a.cell_turnover; valB = b.cell_turnover; }
                        else if (sortColumn === 9) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    } else {
                        if (sortColumn === 0) { valA = a.vending_id; valB = b.vending_id; }
                        else if (sortColumn === 1) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (sortColumn === 2) { valA = a.address || ''; valB = b.address || ''; }
                        else if (sortColumn === 3) { valA = a.model || ''; valB = b.model || ''; }
                        else if (sortColumn === 4) { valA = a.OwnedBy || ''; valB = b.OwnedBy || ''; }
                        else if (sortColumn === 5) { valA = a.fact; valB = b.fact; }
                        else if (sortColumn === 6) { valA = a.orders; valB = b.orders; }
                        else if (sortColumn === 7) { valA = a.cell_turnover; valB = b.cell_turnover; }
                        else if (sortColumn === 8) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    }

                    if (typeof valA === 'string') {
                        return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
                    } else {
                        return sortAsc ? valA - valB : valB - valA;
                    }
                });
            }

            if (filteredRows.length === 0) {
                emptyState.style.display = 'block';
                return;
            }
            emptyState.style.display = 'none';

            filteredRows.forEach(r => {
                const tr = document.createElement('tr');
                const ownedClass = r.OwnedBy.toLowerCase() === 'partner' ? 'badge-partner' : (r.OwnedBy.toLowerCase() === 'berizaryad' ? 'badge-berizaryad' : '');
                const status = getStationStatus(r.vending_id) || 'none';
                const selectClass = status !== 'none' ? `status-${status}` : '';

                tr.innerHTML = `
                    <td><strong>${r.vending_id}</strong></td>
                    ${showCityCol ? `<td><span class="badge badge-city">${r.city}</span></td>` : ''}
                    <td>${r.place_name}</td>
                    <td>${r.address}</td>
                    <td>${r.model}</td>
                    <td><span class="badge ${ownedClass}">${r.OwnedBy}</span></td>
                    <td>${formatCurrency(r.fact)}</td>
                    <td>${r.orders}</td>
                    <td><span class="cell-turnover ${getTurnoverClass(r.cell_turnover)}">${r.cell_turnover.toFixed(2)}</span></td>
                    <td>
                        <select class="status-select ${selectClass}" onchange="changeStationStatus('${r.vending_id}', this)">
                            <option value="none" ${status === 'none' ? 'selected' : ''}>—</option>
                            <option value="dismantle" ${status === 'dismantle' ? 'selected' : ''}>Демонтировать</option>
                            <option value="account" ${status === 'account' ? 'selected' : ''}>Проаккаунтить</option>
                            <option value="b2b_wait" ${status === 'b2b_wait' ? 'selected' : ''}>Ожидание B2B</option>
                            <option value="account_done" ${status === 'account_done' ? 'selected' : ''}>Аккаунтинг проведен</option>
                        </select>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('categories');
            }
        }

        // --- Переменные для синхронизации с сервером ---
        let loadedTasks = [];
        let loadedKanban = [];
        let loadedDecisions = {};
        let loadedLogs = [];
        let isDataLoaded = false;

        // Загрузка всех данных с сервера (один раз на загрузку страницы)
        async function loadServerData() {
            if (isDataLoaded) {
                // Если данные уже загружены, рендерим мгновенно из памяти
                renderTable();
                renderTasks();
                renderKanban();
                renderVolumeTable();
                renderDisconnectedTable();
                renderHistory();
                return;
            }

            try {
                const res = await fetch('/api/dev_plan/all_data').then(r => r.json());
                
                loadedTasks = res.tasks || [];
                loadedKanban = res.kanban || [];
                loadedLogs = res.logs || [];
                
                loadedDecisions = {};
                if (res.decisions) {
                    res.decisions.forEach(d => {
                        loadedDecisions[d.vending_id] = d.status;
                    });
                }
                
                isDataLoaded = true;
                
                renderTable();
                renderTasks();
                renderKanban();
                renderVolumeTable();
                renderDisconnectedTable();
                renderHistory();
            } catch (err) {
                console.error("Ошибка загрузки данных с сервера:", err);
                // Отрисуем пустые таблицы при сбое
                renderTable();
                renderTasks();
                renderKanban();
                renderVolumeTable();
                renderDisconnectedTable();
                renderHistory();
            }
        }

        // --- Логика статусов и решений по станциям ---
        function getStationStatus(vendingId) {
            return loadedDecisions[vendingId] || 'none';
        }

        async function changeStationStatus(vendingId, selectEl) {
            const val = selectEl.value;
            loadedDecisions[vendingId] = val;
            
            selectEl.className = 'status-select';
            if (val !== 'none') {
                selectEl.classList.add(`status-${val}`);
            }
            
            try {
                await fetch('/api/decisions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vending_id: vendingId.toString(), status: val, city: currentCity })
                });
            } catch (err) {
                console.error("Ошибка сохранения статуса:", err);
            }
            
            const decisionFilter = document.getElementById('decision-filter').value;
            if (decisionFilter !== 'all') {
                renderTable();
            }
        }

        function handleDecisionFilter() {
            renderTable();
        }

        // --- Логика Задач ---

        // Форматирование времени по Москве (MSK)
        function getMSKTime() {
            const options = {
                timeZone: 'Europe/Moscow',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            };
            const formatter = new Intl.DateTimeFormat('ru-RU', options);
            return formatter.format(new Date());
        }

        function renderTasks() {
            const listContainer = document.getElementById('tasks-list');
            listContainer.innerHTML = '';

            const cityTasks = loadedTasks.filter(t => t.city === currentCity);

            if (cityTasks.length === 0) {
                listContainer.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 1.5rem;">Нет задач для этого города. Добавьте первую!</div>';
                return;
            }

            const sortedTasks = [...cityTasks].sort((a, b) => {
                if (a.completed !== b.completed) {
                    return a.completed ? 1 : -1;
                }
                return new Date(b.created_raw) - new Date(a.created_raw);
            });

            sortedTasks.forEach(task => {
                const item = document.createElement('div');
                item.className = `task-item ${task.completed ? 'completed' : ''}`;
                
                item.innerHTML = `
                    <div class="task-checkbox-container">
                        <input type="checkbox" class="task-checkbox" ${task.completed ? 'checked' : ''} onchange="toggleTask('${task.id}')">
                    </div>
                    <div class="task-content">
                        <div class="task-text">${escapeHTML(task.text)}</div>
                        <div class="task-dates">
                            <span class="task-date-created">${task.created_at}</span>
                            ${task.completed_at ? `<span class="task-date-completed">${task.completed_at}</span>` : ''}
                        </div>
                    </div>
                    <button class="task-delete-btn" onclick="deleteTask('${task.id}')" title="Удалить задачу">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                `;
                listContainer.appendChild(item);
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('tasks');
            }
        }

        async function saveTask() {
            const input = document.getElementById('task-text-input');
            const text = input.value.trim();
            if (!text) return;

            const now = new Date();
            const newTask = {
                id: 'task_' + now.getTime() + '_' + Math.random().toString(36).substr(2, 9),
                city: currentCity,
                text: text,
                completed: false,
                created_at: getMSKTime(),
                created_raw: now.toISOString(),
                completed_at: null
            };

            loadedTasks.push(newTask);
            renderTasks();
            input.value = '';

            try {
                await fetch('/api/tasks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(newTask)
                });
            } catch (err) {
                console.error("Ошибка сохранения задачи:", err);
            }
        }

        async function toggleTask(id) {
            const task = loadedTasks.find(t => t.id === id);
            if (task) {
                task.completed = !task.completed;
                task.completed_at = task.completed ? getMSKTime() : null;
                renderTasks();

                try {
                    await fetch('/api/tasks', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(task)
                    });
                } catch (err) {
                    console.error("Ошибка переключения задачи:", err);
                }
            }
        }

        async function deleteTask(id) {
            if (!confirm('Вы действительно хотите удалить эту задачу?')) return;
            loadedTasks = loadedTasks.filter(t => t.id !== id);
            renderTasks();

            try {
                await fetch(`/api/tasks/${id}`, {
                    method: 'DELETE'
                });
            } catch (err) {
                console.error("Ошибка удаления задачи:", err);
            }
        }

        function escapeHTML(str) {
            return str
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        // --- Логика Канбан-доски ---

        function createKanbanCard(status) {
            const text = prompt('Введите содержание для новой карточки:');
            if (!text || !text.trim()) return;

            const newCard = {
                id: 'card_' + new Date().getTime() + '_' + Math.random().toString(36).substr(2, 9),
                city: currentCity,
                text: text.trim(),
                status: status
            };

            loadedKanban.push(newCard);
            renderKanban();

            fetch('/api/kanban', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newCard)
            }).catch(err => console.error("Ошибка добавления карточки:", err));
        }

        function renderKanban() {
            const cols = {
                potential: document.getElementById('cards-potential'),
                agreed: document.getElementById('cards-agreed'),
                installed: document.getElementById('cards-installed')
            };

            Object.values(cols).forEach(el => el.innerHTML = '');

            const cityCards = loadedKanban.filter(c => c.city === currentCity);

            const statusGroups = { potential: [], agreed: [], installed: [] };
            cityCards.forEach(c => {
                if (statusGroups[c.status]) {
                    statusGroups[c.status].push(c);
                }
            });

            Object.keys(statusGroups).forEach(status => {
                const groupContainer = cols[status];
                const list = statusGroups[status];

                if (list.length === 0) {
                    groupContainer.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem 1rem; font-size: 0.85rem; border: 1px dashed var(--card-border); border-radius: 10px;">Перетащите карточки сюда или нажмите +</div>';
                    return;
                }

                list.forEach(card => {
                    const cardEl = document.createElement('div');
                    cardEl.className = 'kanban-card';
                    cardEl.draggable = true;
                    cardEl.id = card.id;
                    
                    cardEl.addEventListener('dragstart', (e) => {
                        e.dataTransfer.setData('text/plain', card.id);
                        cardEl.style.opacity = '0.5';
                    });
                    
                    cardEl.addEventListener('dragend', () => {
                        cardEl.style.opacity = '1';
                    });

                    let navButtons = '';
                    if (status === 'potential') {
                        navButtons = `<button class="card-action-btn card-btn-move" onclick="moveCard('${card.id}', 'agreed')" title="Переместить в '100% договоренности'">Вправо ➔</button>`;
                    } else if (status === 'agreed') {
                        navButtons = `
                            <button class="card-action-btn card-btn-move" onclick="moveCard('${card.id}', 'potential')" title="Переместить в 'Потенциальные'">⬅ L</button>
                            <button class="card-action-btn card-btn-move" onclick="moveCard('${card.id}', 'installed')" title="Переместить в 'Установлено'">R ➔</button>
                        `;
                    } else if (status === 'installed') {
                        navButtons = `<button class="card-action-btn card-btn-move" onclick="moveCard('${card.id}', 'agreed')" title="Переместить в '100% договоренности'">⬅ Влево</button>`;
                    }

                    cardEl.innerHTML = `
                        <div class="kanban-card-text">${escapeHTML(card.text)}</div>
                        <div class="kanban-card-actions">
                            ${navButtons}
                            <button class="card-action-btn card-btn-edit" onclick="editCard('${card.id}')" title="Редактировать">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                            </button>
                            <button class="card-action-btn card-btn-delete" onclick="deleteCard('${card.id}')" title="Удалить">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                            </button>
                        </div>
                    `;
                    groupContainer.appendChild(cardEl);
                });
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('kanban');
            }
        }

        async function moveCard(id, newStatus) {
            const card = loadedKanban.find(c => c.id === id);
            if (card) {
                card.status = newStatus;
                renderKanban();

                try {
                    await fetch('/api/kanban', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(card)
                    });
                } catch (err) {
                    console.error("Ошибка перемещения карточки:", err);
                }
            }
        }

        async function editCard(id) {
            const card = loadedKanban.find(c => c.id === id);
            if (card) {
                const newText = prompt('Редактировать карточку:', card.text);
                if (newText && newText.trim()) {
                    card.text = newText.trim();
                    renderKanban();

                    try {
                        await fetch('/api/kanban', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(card)
                        });
                    } catch (err) {
                        console.error("Ошибка изменения карточки:", err);
                    }
                }
            }
        }

        async function deleteCard(id) {
            if (!confirm('Вы действительно хотите удалить эту карточку?')) return;
            loadedKanban = loadedKanban.filter(c => c.id !== id);
            renderKanban();

            try {
                await fetch(`/api/kanban/${id}`, {
                    method: 'DELETE'
                });
            } catch (err) {
                console.error("Ошибка удаления карточки:", err);
            }
        }

        function allowDrop(ev) {
            ev.preventDefault();
        }

        async function handleDrop(ev, status) {
            ev.preventDefault();
            const id = ev.dataTransfer.getData('text/plain');
            const card = loadedKanban.find(c => c.id === id);
            if (card && card.status !== status) {
                card.status = status;
                renderKanban();

                try {
                    await fetch('/api/kanban', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(card)
                    });
                } catch (err) {
                    console.error("Ошибка при переносе карточки:", err);
                }
            }
        }

        // --- Логика Увеличения/Уменьшения Объема ---
        function switchVolumeTab(tab) {
            currentVolumeTab = tab;
            volumeSortColumn = null;
            document.querySelectorAll('#section-volume .tab-btn').forEach(btn => btn.classList.remove('active'));
            if (tab === 'increase') {
                document.getElementById('btn-vol-inc').classList.add('active');
            } else {
                document.getElementById('btn-vol-dec').classList.add('active');
            }
            renderVolumeTable();
        }

        function handleVolumeSort(colIndex, type) {
            if (volumeSortColumn === colIndex) {
                volumeSortAsc = !volumeSortAsc;
            } else {
                volumeSortColumn = colIndex;
                volumeSortAsc = true;
            }
            
            // Visual header indicators for volume table
            const headers = document.querySelectorAll('#volume-table th');
            headers.forEach((h, idx) => {
                h.classList.remove('sort-asc', 'sort-desc');
                if (idx === colIndex) {
                    h.classList.add(volumeSortAsc ? 'sort-asc' : 'sort-desc');
                }
            });

            renderVolumeTable();
        }

        function renderVolumeTable() {
            const data = reportData[currentCity];
            const rows = currentVolumeTab === 'increase' ? (data.volume_increase || []) : (data.volume_decrease || []);
            const tbody = document.getElementById('volume-table-body');
            const headersRow = document.getElementById('volume-table-headers');
            const emptyState = document.getElementById('volume-empty-state');
            
            if (!tbody || !headersRow || !emptyState) return;
            
            tbody.innerHTML = '';
            
            const showCityCol = currentCity === 'Общее';
            
            headersRow.innerHTML = `
                <th onclick="handleVolumeSort(0, 'int')">ID</th>
                ${showCityCol ? `<th onclick="handleVolumeSort(1, 'str')">Город</th>` : ''}
                <th onclick="handleVolumeSort(${showCityCol ? 2 : 1}, 'str')">Локация</th>
                <th onclick="handleVolumeSort(${showCityCol ? 3 : 2}, 'str')">Адрес</th>
                <th onclick="handleVolumeSort(${showCityCol ? 4 : 3}, 'str')">Модель</th>
                <th onclick="handleVolumeSort(${showCityCol ? 5 : 4}, 'int')">Ячеек всего</th>
                <th onclick="handleVolumeSort(${showCityCol ? 6 : 5}, 'str')">Владелец</th>
                <th onclick="handleVolumeSort(${showCityCol ? 7 : 6}, 'num')">Выручка</th>
                <th onclick="handleVolumeSort(${showCityCol ? 8 : 7}, 'int')">Заказы</th>
                <th onclick="handleVolumeSort(${showCityCol ? 9 : 8}, 'num')">Оборачиваемость</th>
                <th onclick="handleVolumeSort(${showCityCol ? 10 : 9}, 'int')">На локации</th>
                <th onclick="handleVolumeSort(${showCityCol ? 11 : 10}, 'str')" style="width: 160px; text-align: center;">Решение</th>
            `;

            // Фильтруем по поисковой строке
            let filteredRows = rows.filter(r => {
                const searchStr = `${r.vending_id} ${r.place_name} ${r.address} ${r.model} ${r.OwnedBy} ${r.city || ''}`.toLowerCase();
                return searchStr.includes(searchQuery);
            });

            // Сортировка
            if (volumeSortColumn !== null) {
                filteredRows.sort((a, b) => {
                    let valA, valB;
                    if (showCityCol) {
                        if (volumeSortColumn === 0) { valA = a.vending_id; valB = b.vending_id; }
                        else if (volumeSortColumn === 1) { valA = a.city || ''; valB = b.city || ''; }
                        else if (volumeSortColumn === 2) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (volumeSortColumn === 3) { valA = a.address || ''; valB = b.address || ''; }
                        else if (volumeSortColumn === 4) { valA = a.model || ''; valB = b.model || ''; }
                        else if (volumeSortColumn === 5) { valA = a.cells_total; valB = b.cells_total; }
                        else if (volumeSortColumn === 6) { valA = a.OwnedBy || ''; valB = b.OwnedBy || ''; }
                        else if (volumeSortColumn === 7) { valA = a.fact; valB = b.fact; }
                        else if (volumeSortColumn === 8) { valA = a.orders; valB = b.orders; }
                        else if (volumeSortColumn === 9) { valA = a.cell_turnover; valB = b.cell_turnover; }
                        else if (volumeSortColumn === 10) { valA = a.onplace_time; valB = b.onplace_time; }
                        else if (volumeSortColumn === 11) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    } else {
                        if (volumeSortColumn === 0) { valA = a.vending_id; valB = b.vending_id; }
                        else if (volumeSortColumn === 1) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (volumeSortColumn === 2) { valA = a.address || ''; valB = b.address || ''; }
                        else if (volumeSortColumn === 3) { valA = a.model || ''; valB = b.model || ''; }
                        else if (volumeSortColumn === 4) { valA = a.cells_total; valB = b.cells_total; }
                        else if (volumeSortColumn === 5) { valA = a.OwnedBy || ''; valB = b.OwnedBy || ''; }
                        else if (volumeSortColumn === 6) { valA = a.fact; valB = b.fact; }
                        else if (volumeSortColumn === 7) { valA = a.orders; valB = b.orders; }
                        else if (volumeSortColumn === 8) { valA = a.cell_turnover; valB = b.cell_turnover; }
                        else if (volumeSortColumn === 9) { valA = a.onplace_time; valB = b.onplace_time; }
                        else if (volumeSortColumn === 10) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    }

                    if (typeof valA === 'string') {
                        return volumeSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
                    } else {
                        return volumeSortAsc ? valA - valB : valB - valA;
                    }
                });
            }

            if (filteredRows.length === 0) {
                emptyState.style.display = 'block';
                document.getElementById('volume-table').style.display = 'none';
                if (typeof adjustAccordionHeight === 'function') {
                    adjustAccordionHeight('volume');
                }
                return;
            }
            
            emptyState.style.display = 'none';
            document.getElementById('volume-table').style.display = 'table';

            filteredRows.forEach(r => {
                const tr = document.createElement('tr');
                const ownedClass = r.OwnedBy.toLowerCase() === 'partner' ? 'badge-partner' : (r.OwnedBy.toLowerCase() === 'berizaryad' ? 'badge-berizaryad' : '');
                
                const status = getStationStatus(r.vending_id);
                let selectOptions = '';
                if (currentVolumeTab === 'increase') {
                    selectOptions = `
                        <option value="none" ${status === 'none' ? 'selected' : ''}>—</option>
                        <option value="module_add" ${status === 'module_add' ? 'selected' : ''}>доп. модуль</option>
                        <option value="second_station" ${status === 'second_station' ? 'selected' : ''}>вторая станция</option>
                        <option value="refusal" ${status === 'refusal' ? 'selected' : ''}>отказ</option>
                    `;
                } else {
                    selectOptions = `
                        <option value="none" ${status === 'none' ? 'selected' : ''}>—</option>
                        <option value="module_remove" ${status === 'module_remove' ? 'selected' : ''}>забираем доп модуль</option>
                        <option value="recently_placed" ${status === 'recently_placed' ? 'selected' : ''}>недавно установлено</option>
                    `;
                }
                
                let selectClass = '';
                if (status !== 'none') {
                    selectClass = `status-${status}`;
                }

                tr.innerHTML = `
                    <td><strong>${r.vending_id}</strong></td>
                    ${showCityCol ? `<td><span class="badge badge-city">${r.city}</span></td>` : ''}
                    <td>${r.place_name}</td>
                    <td>${r.address}</td>
                    <td>${r.model}</td>
                    <td><span class="badge badge-indigo">${r.cells_total}</span></td>
                    <td><span class="badge ${ownedClass}">${r.OwnedBy}</span></td>
                    <td>${formatCurrency(r.fact)}</td>
                    <td>${r.orders}</td>
                    <td><span class="turnover-rate ${getTurnoverClass(r.cell_turnover)}">${r.cell_turnover.toFixed(2)}</span></td>
                    <td><span style="font-weight: 500;">${r.onplace_time} дн.</span></td>
                    <td>
                        <select class="status-select ${selectClass}" onchange="changeStationStatus('${r.vending_id}', this)">
                            ${selectOptions}
                        </select>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('volume');
            }
        }

        // --- Логика Давно не в сети ---
        function handleDisconnectedSort(colIndex, type) {
            if (disconnectedSortColumn === colIndex) {
                disconnectedSortAsc = !disconnectedSortAsc;
            } else {
                disconnectedSortColumn = colIndex;
                disconnectedSortAsc = true;
            }
            
            // Visual header indicators for disconnected table
            const headers = document.querySelectorAll('#disconnected-table th');
            headers.forEach((h, idx) => {
                h.classList.remove('sort-asc', 'sort-desc');
                if (idx === colIndex) {
                    h.classList.add(disconnectedSortAsc ? 'sort-asc' : 'sort-desc');
                }
            });

            renderDisconnectedTable();
        }

        function renderDisconnectedTable() {
            const data = reportData[currentCity];
            const rows = data.disconnected || [];
            const tbody = document.getElementById('disconnected-table-body');
            const headersRow = document.getElementById('disconnected-table-headers');
            const emptyState = document.getElementById('disconnected-empty-state');
            
            if (!tbody || !headersRow || !emptyState) return;
            
            tbody.innerHTML = '';
            
            const showCityCol = currentCity === 'Общее';
            
            headersRow.innerHTML = `
                <th onclick="handleDisconnectedSort(0, 'int')">ID</th>
                ${showCityCol ? `<th onclick="handleDisconnectedSort(1, 'str')">Город</th>` : ''}
                <th onclick="handleDisconnectedSort(${showCityCol ? 2 : 1}, 'str')">Локация</th>
                <th onclick="handleDisconnectedSort(${showCityCol ? 3 : 2}, 'str')">Адрес</th>
                <th onclick="handleDisconnectedSort(${showCityCol ? 4 : 3}, 'int')">Время оффлайна</th>
                <th onclick="handleDisconnectedSort(${showCityCol ? 5 : 4}, 'str')" style="width: 150px; text-align: center;">Решение</th>
                <th style="width: 80px; text-align: center;">Действие</th>
            `;

            // Фильтруем по поисковой строке
            let filteredRows = rows.filter(r => {
                const searchStr = `${r.vending_id} ${r.place_name} ${r.address} ${r.city || ''}`.toLowerCase();
                return searchStr.includes(searchQuery);
            });

            // Сортировка
            if (disconnectedSortColumn !== null) {
                filteredRows.sort((a, b) => {
                    let valA, valB;
                    if (showCityCol) {
                        if (disconnectedSortColumn === 0) { valA = parseInt(a.vending_id); valB = parseInt(b.vending_id); }
                        else if (disconnectedSortColumn === 1) { valA = a.city || ''; valB = b.city || ''; }
                        else if (disconnectedSortColumn === 2) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (disconnectedSortColumn === 3) { valA = a.address || ''; valB = b.address || ''; }
                        else if (disconnectedSortColumn === 4) { valA = a.disconnection_seconds; valB = b.disconnection_seconds; }
                        else if (disconnectedSortColumn === 5) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    } else {
                        if (disconnectedSortColumn === 0) { valA = parseInt(a.vending_id); valB = parseInt(b.vending_id); }
                        else if (disconnectedSortColumn === 1) { valA = a.place_name || ''; valB = b.place_name || ''; }
                        else if (disconnectedSortColumn === 2) { valA = a.address || ''; valB = b.address || ''; }
                        else if (disconnectedSortColumn === 3) { valA = a.disconnection_seconds; valB = b.disconnection_seconds; }
                        else if (disconnectedSortColumn === 4) { valA = getStationStatus(a.vending_id); valB = getStationStatus(b.vending_id); }
                    }

                    if (typeof valA === 'string') {
                        return disconnectedSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
                    } else {
                        return disconnectedSortAsc ? valA - valB : valB - valA;
                    }
                });
            }

            if (filteredRows.length === 0) {
                emptyState.style.display = 'block';
                document.getElementById('disconnected-table').style.display = 'none';
                if (typeof adjustAccordionHeight === 'function') {
                    adjustAccordionHeight('disconnected');
                }
                return;
            }
            
            emptyState.style.display = 'none';
            document.getElementById('disconnected-table').style.display = 'table';

            filteredRows.forEach(r => {
                const tr = document.createElement('tr');
                const status = getStationStatus(r.vending_id);
                let selectClass = '';
                if (status !== 'none') {
                    selectClass = `status-${status}`;
                }

                tr.innerHTML = `
                    <td><strong>${r.vending_id}</strong></td>
                    ${showCityCol ? `<td><span class="badge badge-city">${r.city}</span></td>` : ''}
                    <td>${r.place_name}</td>
                    <td>${r.address}</td>
                    <td><span class="badge" style="background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.3); font-weight: 600;">${r.offline_duration}</span></td>
                    <td>
                        <select class="status-select ${selectClass}" onchange="changeStationStatus('${r.vending_id}', this)">
                            <option value="none" ${status === 'none' ? 'selected' : ''}>—</option>
                            <option value="visit" ${status === 'visit' ? 'selected' : ''}>доехать</option>
                            <option value="dismantle" ${status === 'dismantle' ? 'selected' : ''}>демонтаж</option>
                            <option value="negotiate" ${status === 'negotiate' ? 'selected' : ''}>переговоры</option>
                            <option value="call" ${status === 'call' ? 'selected' : ''}>на прозвон</option>
                        </select>
                    </td>
                    <td style="text-align: center;">
                        <a href="https://fleet.yandex.ru/snickers/vendings/${r.vending_id}?park_id=${r.park_id}" target="_blank" title="Открыть во Флит" style="display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 6px; background: rgba(99,102,241,0.15); color: var(--accent-primary); border: 1px solid rgba(99,102,241,0.2); transition: all 0.2s;" onmouseover="this.style.background='rgba(99,102,241,0.3)'; this.style.transform='scale(1.05)';" onmouseout="this.style.background='rgba(99,102,241,0.15)'; this.style.transform='scale(1)';">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                        </a>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('disconnected');
            }
        }

        // --- Логика Аккордеонов и Быстрого Меню ---
        function toggleAccordion(id) {
            const section = document.getElementById(`section-${id}`);
            const content = document.getElementById(`content-${id}`);
            
            if (section.classList.contains('active')) {
                section.classList.remove('active');
                content.style.maxHeight = '0';
                content.style.opacity = '0';
            } else {
                section.classList.add('active');
                content.style.maxHeight = content.scrollHeight + 100 + 'px';
                content.style.opacity = '1';
                
                // Пересчитываем высоту после завершения анимации рендеринга
                setTimeout(() => {
                    if (section.classList.contains('active')) {
                        content.style.maxHeight = content.scrollHeight + 150 + 'px';
                    }
                }, 300);
            }
        }

        function adjustAccordionHeight(id) {
            const section = document.getElementById(`section-${id}`);
            const content = document.getElementById(`content-${id}`);
            if (section && section.classList.contains('active') && content) {
                content.style.maxHeight = content.scrollHeight + 150 + 'px';
            }
        }

        function scrollToSection(id) {
            const section = document.getElementById(id);
            if (!section) return;
            
            // Если секция свернута (аккордеон), сначала разворачиваем ее
            if (id.startsWith('section-')) {
                const actId = id.replace('section-', '');
                const secEl = document.getElementById(`section-${actId}`);
                if (!secEl.classList.contains('active')) {
                    toggleAccordion(actId);
                }
            }
            
            setTimeout(() => {
                const offset = 80; // Смещение для липкой шапки
                const elementPosition = section.getBoundingClientRect().top;
                const offsetPosition = elementPosition + window.pageYOffset - offset;
                
                window.scrollTo({
                    top: offsetPosition,
                    behavior: 'smooth'
                });
            }, 150);
        }

        // Функция рендеринга логов истории
        function clearHistoryDate() {
            const dateEl = document.getElementById('history-date-filter');
            if (dateEl) dateEl.value = '';
            renderHistory();
        }

        function renderHistory() {
            const container = document.getElementById('history-list');
            if (!container) return;
            container.innerHTML = '';
            
            // Фильтруем логи по текущему городу (если выбран конкретный город)
            let filteredLogs = loadedLogs.filter(l => currentCity === 'Общее' || l.city === currentCity);
            
            // Фильтр по дате
            const dateVal = document.getElementById('history-date-filter') ? document.getElementById('history-date-filter').value : '';
            if (dateVal) {
                const [y, m, d] = dateVal.split('-');
                const datePrefix = `${d}.${m}.${y}`;
                filteredLogs = filteredLogs.filter(l => l.timestamp.startsWith(datePrefix));
            }
            
            // Фильтр по номеру аппарата
            const searchVal = document.getElementById('history-search-input') ? document.getElementById('history-search-input').value.trim() : '';
            if (searchVal) {
                filteredLogs = filteredLogs.filter(l => l.details.includes(searchVal) || l.details.toLowerCase().includes(searchVal.toLowerCase()));
            }
            
            if (filteredLogs.length === 0) {
                container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem; font-size: 0.9rem;">Логи не найдены с текущими фильтрами.</div>';
                if (typeof adjustAccordionHeight === 'function') {
                    adjustAccordionHeight('history');
                }
                return;
            }
            
            filteredLogs.forEach(log => {
                const item = document.createElement('div');
                item.className = 'history-item';
                item.innerHTML = `
                    <span class="history-time">${log.timestamp}</span>
                    <span class="history-city-badge">${log.city}</span>
                    <span class="history-details">${escapeHTML(log.details)}</span>
                `;
                container.appendChild(item);
            });
            
            if (typeof adjustAccordionHeight === 'function') {
                adjustAccordionHeight('history');
            }
        }

        // Scrollspy подсвечивание активного пункта бокового меню
        window.addEventListener('scroll', () => {
            const sections = ['kpi-section', 'section-categories', 'section-kanban', 'section-tasks', 'section-volume', 'section-disconnected', 'section-history'];
            let activeSection = 'kpi-section';
            
            sections.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    // Если верхняя граница секции находится в верхней половине экрана
                    if (rect.top <= 200) {
                        activeSection = id;
                    }
                }
            });
            
            sections.forEach(id => {
                const btn = document.getElementById(`nav-${id}`);
                if (btn) {
                    if (id === activeSection) {
                        btn.classList.add('active');
                    } else {
                        btn.classList.remove('active');
                    }
                }
            });
        });

        // Init
        updateDashboard();
    </script>
</body>
</html>"""

    # Inject data into HTML template
    json_data = json.dumps(city_data, ensure_ascii=False)
    html_content = html_template.replace("{{REPORT_DATA}}", json_data)
    
    # Save the output file in root directory
    output_path = os.path.abspath(os.path.join(base_dir, "..", "План развития городов_Июнь.html"))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    logger.info(f"Interactive City Development Plan report generated successfully at: {output_path}")

if __name__ == "__main__":
    main()
