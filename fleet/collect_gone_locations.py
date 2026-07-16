# -*- coding: utf-8 -*-
import os
import sys
import shutil
import logging
import pandas as pd
import openpyxl

# Настройка вывода в консоль для корректной обработки UTF-8 на Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def clean_and_get_active(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filters the revenue dataframe to keep only active placed vending machines on locations.
    
    Args:
        df (pd.DataFrame): Raw DataFrame from the revenue CSV report.
        
    Returns:
        pd.DataFrame: Cleaned and filtered DataFrame with active machines.
    """
    if df.empty:
        return pd.DataFrame(columns=['vending_id', 'address', 'place_name', 'place_date', 'city'])
        
    # Ensure vending_id is present and clean it
    df_clean = df.dropna(subset=['vending_id']).copy()
    df_clean['vending_id'] = pd.to_numeric(df_clean['vending_id'], errors='coerce')
    df_clean = df_clean.dropna(subset=['vending_id'])
    df_clean['vending_id'] = df_clean['vending_id'].astype(int)
    
    # Filter by office_status == 'placed' (case-insensitive and trimmed)
    placed_mask = df_clean['office_status'].fillna('').astype(str).str.strip().str.lower() == 'placed'
    
    # Filter by remove_date containing the dummy placeholder date '2222-02-01' or '01.02.2222'
    active_mask = df_clean['remove_date'].astype(str).str.strip().str.contains('2222-02-01|01.02.2222', regex=True)
    
    df_filtered = df_clean[placed_mask & active_mask]
    return df_filtered

def get_gone_devices(df_prev: pd.DataFrame, df_curr: pd.DataFrame, month_label: str, default_city: str) -> pd.DataFrame:
    """
    Identifies machines that were active in the previous month but became inactive in the current month.
    Routes Orel park machines with vending_id < 60000 to Kirov sheet.
    
    Args:
        df_prev (pd.DataFrame): Filtered active DataFrame of the previous month.
        df_curr (pd.DataFrame): Filtered active DataFrame of the current month.
        month_label (str): Name of the month when machines went away ('Май' or 'Июнь').
        default_city (str): Standard city name of the current file.
        
    Returns:
        pd.DataFrame: DataFrame containing gone machines with columns matching the destination report.
    """
    vids_prev = set(df_prev['vending_id'].dropna().astype(int))
    vids_curr = set(df_curr['vending_id'].dropna().astype(int))
    
    gone_vids = vids_prev - vids_curr
    
    if not gone_vids:
        return pd.DataFrame(columns=['vending_id', 'address', 'place_name', 'place_date', 'Когда ушли', 'target_city'])
        
    # Extract details for gone machines from their last active month report (df_prev)
    df_gone_details = df_prev[df_prev['vending_id'].isin(gone_vids)].copy()
    
    # Map to final format
    columns_mapping = {
        'vending_id': 'vending_id',
        'address': 'address',
        'place_name': 'place_name',
        'place_date': 'place_date'
    }
    
    df_result = df_gone_details[list(columns_mapping.keys())].rename(columns=columns_mapping)
    df_result['Когда ушли'] = month_label
    
    # Route to appropriate target city sheet
    target_cities = []
    for _, row in df_result.iterrows():
        vid = int(row['vending_id'])
        if default_city == 'Орёл':
            # Timofeev franchise exception: vending_ids < 60000 belong to Kirov
            if vid < 60000:
                target_cities.append('Киров')
            else:
                target_cities.append('Орёл')
        else:
            target_cities.append(default_city)
            
    df_result['target_city'] = target_cities
    return df_result

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    inputs_dir = os.path.join(base_dir, "inputs")
    debug_dir = os.path.abspath(os.path.join(base_dir, "..", "debug_tools"))
    
    # Определяем имя результирующего файла на основе текущего месяца
    def get_current_month_ru():
        months_ru = {
            1: 'январь', 2: 'февраль', 3: 'март', 4: 'апрель',
            5: 'май', 6: 'июнь', 7: 'июль', 8: 'август',
            9: 'сентябрь', 10: 'октябрь', 11: 'ноябрь', 12: 'декабрь'
        }
        import datetime
        now = datetime.datetime.now()
        return months_ru[now.month]

    target_month = get_current_month_ru()
    excel_path = os.path.join(inputs_dir, f"Список локаций откуда ушли_{target_month}.xlsx")
    
    if not os.path.exists(excel_path):
        import glob
        pattern = os.path.join(inputs_dir, "Список локаций откуда ушли_*.xlsx")
        existing_files = glob.glob(pattern)
        existing_files = [f for f in existing_files if "temp" not in f and f != excel_path]
        if existing_files:
            existing_files.sort(key=os.path.getmtime)
            source_path = existing_files[-1]
            logger.info(f"Файл {os.path.basename(excel_path)} не найден. Копируем за основу {os.path.basename(source_path)}")
            shutil.copy(source_path, excel_path)
        else:
            logger.error(f"Не найден ни один базовый файл по шаблону Список локаций откуда ушли_*.xlsx")
            sys.exit(1)
        
    cities = ['Омск', 'Магнитогорск', 'Сургут', 'Ижевск', 'Ульяновск', 'Рязань', 'Киров', 'Чебоксары', 'Орёл']
    
    # Временный путь для сохранения во избежание блокировок Excel
    temp_excel_path = os.path.join(debug_dir, "Список локаций откуда ушли_temp.xlsx")
    os.makedirs(debug_dir, exist_ok=True)
    
    # Загружаем отчеты
    def load_csv(path):
        try:
            return pd.read_csv(path, encoding='utf-8')
        except pd.errors.EmptyDataError:
            logger.warning(f"Файл пустой: {path}")
            return pd.DataFrame()
        except Exception:
            try:
                return pd.read_csv(path, encoding='cp1251')
            except pd.errors.EmptyDataError:
                logger.warning(f"Файл пустой: {path}")
                return pd.DataFrame()
            except Exception as e:
                logger.error(f"Не удалось прочитать {path}: {e}")
                return pd.DataFrame()

    # Собираем глобальный справочник дат установки vending_id -> place_date из исторических отчетов
    global_dates = {}
    logger.info("Сбор справочника дат установки (vending_id -> place_date) из исторических отчетов...")
    for filename in os.listdir(inputs_dir):
        if filename.startswith("revenue_") and filename.endswith(".csv"):
            path = os.path.join(inputs_dir, filename)
            try:
                df = load_csv(path)
                if not df.empty and 'vending_id' in df.columns and 'place_date' in df.columns:
                    df_valid = df.dropna(subset=['vending_id', 'place_date']).copy()
                    df_valid['vending_id'] = pd.to_numeric(df_valid['vending_id'], errors='coerce')
                    df_valid = df_valid.dropna(subset=['vending_id'])
                    for _, row in df_valid.iterrows():
                        vid = int(row['vending_id'])
                        pdate = row['place_date']
                        if pd.notna(pdate) and str(pdate).strip() != '':
                            try:
                                dt = pd.to_datetime(pdate, errors='coerce')
                                if pd.notna(dt):
                                    global_dates[vid] = dt
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"Ошибка при чтении {filename} для справочника дат: {e}")
    logger.info(f"Собрано уникальных дат установки для {len(global_dates)} аппаратов.")

    # Считываем все существующие листы
    logger.info(f"Чтение существующего отчета: {excel_path}")
    xls = pd.ExcelFile(excel_path)
    sheet_dfs = {}
    for sheet in xls.sheet_names:
        sheet_dfs[sheet] = pd.read_excel(excel_path, sheet_name=sheet)
        
        # Дополняем отсутствующие place_date из собранного справочника
        df_sheet = sheet_dfs[sheet]
        if not df_sheet.empty and 'vending_id' in df_sheet.columns and 'place_date' in df_sheet.columns:
            # Приводим к числовому типу для надежного сопоставления
            df_sheet['vending_id'] = pd.to_numeric(df_sheet['vending_id'], errors='coerce')
            # Перед присвоением убедимся, что колонка place_date приведена к datetime64
            df_sheet['place_date'] = pd.to_datetime(df_sheet['place_date'], errors='coerce')
            empty_mask = df_sheet['place_date'].isna()
            updated_count = 0
            for idx, row in df_sheet[empty_mask].iterrows():
                if pd.notna(row['vending_id']):
                    vid = int(row['vending_id'])
                    if vid in global_dates:
                        df_sheet.at[idx, 'place_date'] = global_dates[vid]
                        updated_count += 1
            if updated_count > 0:
                logger.info(f"  Лист '{sheet}': восстановлено place_date для {updated_count} аппаратов.")
        
    # Инициализируем новые листы, если их не было в исходном файле
    for city in cities:
        if city not in sheet_dfs:
            sheet_dfs[city] = pd.DataFrame(columns=['vending_id', 'address', 'place_name', 'place_date', 'Когда ушли'])
            logger.info(f"Инициализирован новый пустой лист для города: {city}")
                
    # Собираем все новые ушедшие локации со всех отчетов
    all_new_gone_list = []
    
    for city in cities:
        logger.info(f"--- Обработка города (отчет): {city} ---")
        
        # Пути к отчетам по выручке за Апрель, Май, Июнь
        rev_apr_path = os.path.join(inputs_dir, f"revenue_{city}_2026-04-30.csv")
        rev_may_path = os.path.join(inputs_dir, f"revenue_{city}_2026-05-31.csv")
        rev_jun_path = os.path.join(inputs_dir, f"revenue_{city}_2026-06-30.csv")
        
        # Ищем самый свежий июльский отчет по маске revenue_{city}_2026-07-*.csv
        import glob
        jul_pattern = os.path.join(inputs_dir, f"revenue_{city}_2026-07-*.csv")
        jul_files = glob.glob(jul_pattern)
        if jul_files:
            jul_files.sort()
            rev_jul_path = jul_files[-1]
            logger.info(f"  Найден файл за Июль: {os.path.basename(rev_jul_path)}")
        else:
            rev_jul_path = None
            
        # Проверяем наличие всех 4 файлов
        missing_files = []
        for p, d in [(rev_apr_path, 'Апрель'), (rev_may_path, 'Май'), (rev_jun_path, 'Июнь')]:
            if not os.path.exists(p):
                missing_files.append(d)
        if not rev_jul_path:
            missing_files.append('Июль')
            
        if missing_files:
            logger.error(f"Для города '{city}' отсутствуют файлы отчетов за: {', '.join(missing_files)}. Пропуск.")
            continue
            
        df_apr_raw = load_csv(rev_apr_path)
        df_may_raw = load_csv(rev_may_path)
        df_jun_raw = load_csv(rev_jun_path)
        df_jul_raw = load_csv(rev_jul_path)
        
        # Фильтруем активные
        df_apr = clean_and_get_active(df_apr_raw)
        df_may = clean_and_get_active(df_may_raw)
        df_jun = clean_and_get_active(df_jun_raw)
        df_jul = clean_and_get_active(df_jul_raw)
        
        logger.info(f"  Активных станций: Апрель={len(df_apr)}, Май={len(df_may)}, Июнь={len(df_jun)}, Июль={len(df_jul)}")
        
        # Ушедшие в Мае (Апрель -> Май)
        df_gone_may = get_gone_devices(df_apr, df_may, 'Май', city)
        # Ушедшие в Июне (Май -> Июнь)
        df_gone_jun = get_gone_devices(df_may, df_jun, 'Июнь', city)
        # Ушедшие в Июле (Июнь -> Июль)
        df_gone_jul = get_gone_devices(df_jun, df_jul, 'Июль', city)
        
        logger.info(f"  Ушедших станций: в Мае={len(df_gone_may)}, в Июне={len(df_gone_jun)}, в Июле={len(df_gone_jul)}")
        
        if not df_gone_may.empty:
            all_new_gone_list.append(df_gone_may)
        if not df_gone_jun.empty:
            all_new_gone_list.append(df_gone_jun)
        if not df_gone_jul.empty:
            all_new_gone_list.append(df_gone_jul)
            
    if not all_new_gone_list:
        logger.info("Новых ушедших станций не обнаружено.")
        return
        
    df_all_new_gone = pd.concat(all_new_gone_list, ignore_index=True)
    
    # Теперь распределяем по целевым городам и дополняем Excel
    for city in cities:
        logger.info(f"--- Запись обновлений для листа: {city} ---")
        
        # Выбираем новые ушедшие, предназначенные для этого города
        df_city_new = df_all_new_gone[df_all_new_gone['target_city'] == city].copy()
        
        if df_city_new.empty:
            logger.info(f"  Нет новых ушедших станций для листа '{city}'")
            continue
            
        # Убираем временную колонку target_city
        df_city_new = df_city_new.drop(columns=['target_city'])
        
        # Существующий лист города
        df_existing = sheet_dfs[city].copy()
        
        # Дедупликация: добавляем только те vending_id, которых еще нет в существующем списке
        if not df_existing.empty and 'vending_id' in df_existing.columns:
            existing_vids = set(df_existing['vending_id'].dropna().astype(int))
            df_new_filtered = df_city_new[~df_city_new['vending_id'].isin(existing_vids)].copy()
        else:
            df_new_filtered = df_city_new.copy()
            
        logger.info(f"  Будет добавлено новых уникальных строк: {len(df_new_filtered)}")
        
        if not df_new_filtered.empty:
            # Преобразование даты в формат datetime.date перед записью
            if 'place_date' in df_new_filtered.columns:
                df_new_filtered['place_date'] = pd.to_datetime(df_new_filtered['place_date'], errors='coerce')
                df_new_filtered['place_date'] = df_new_filtered['place_date'].dt.date
                
            df_updated = pd.concat([df_existing, df_new_filtered], ignore_index=True)
            sheet_dfs[city] = df_updated
            
    # Записываем обновленные листы во временный файл
    logger.info(f"Сохранение обновленных данных во временный файл: {temp_excel_path}")
    from openpyxl.utils import get_column_letter
    
    with pd.ExcelWriter(temp_excel_path, engine='openpyxl') as writer:
        for sheet_name, df_sheet in sheet_dfs.items():
            if not df_sheet.empty and 'vending_id' in df_sheet.columns:
                df_sheet['vending_id'] = pd.to_numeric(df_sheet['vending_id'], errors='coerce').fillna(0).astype(int)
                
            if not df_sheet.empty and 'place_date' in df_sheet.columns:
                df_sheet['place_date'] = pd.to_datetime(df_sheet['place_date'], errors='coerce')
                df_sheet['place_date'] = df_sheet['place_date'].apply(lambda x: x.date() if pd.notna(x) else None)
                
            df_sheet.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # Автоподбор ширины колонок
            ws = writer.sheets[sheet_name]
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val = str(cell.value or '')
                    if len(val) > max_len:
                        max_len = len(val)
                ws.column_dimensions[col_letter].width = max(max_len + 3, 10)
            
    # Копируем временный файл поверх оригинального
    logger.info(f"Копирование временного файла поверх оригинального: {excel_path}")
    try:
        shutil.copy(temp_excel_path, excel_path)
        logger.info("Файл успешно обновлен!")
    except PermissionError:
        logger.warning(f"Файл {excel_path} заблокирован (открыт в Excel). Пробуем принудительно через PowerShell...")
        try:
            import subprocess
            cmd = f'Copy-Item -Path "{temp_excel_path}" -Destination "{excel_path}" -Force'
            subprocess.run(["powershell", "-Command", cmd], check=True)
            logger.info("Файл принудительно обновлен через PowerShell!")
        except Exception as e:
            logger.error(f"Не удалось обновить файл: {e}. Сводные данные сохранены во временном файле: {temp_excel_path}")
            sys.exit(1)

if __name__ == '__main__':
    main()
