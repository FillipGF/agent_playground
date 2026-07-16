import os
import csv
import re
import json
import httpx
import asyncio
import numpy as np
from datetime import datetime
from .config import load_settings, BASE_DIR

# Папка базы знаний
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge_base")
INDEX_PATH = os.path.join(KNOWLEDGE_DIR, "knowledge_index.json")

os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

def parse_month_from_filename(filename):
    """
    Извлекает дату из названия файла (например, 2026-06-30 или 2026-06-01_to_2026-06-30)
    и возвращает название месяца и год на русском языке.
    """
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        year, month, _ = match.groups()
        months_ru = {
            "01": "январь", "02": "февраль", "03": "март", "04": "апрель",
            "05": "май", "06": "июнь", "07": "июль", "08": "август",
            "09": "сентябрь", "10": "октябрь", "11": "ноябрь", "12": "декабрь"
        }
        month_name = months_ru.get(month, "")
        if month_name:
            return f"{month_name} {year} года"
    return ""

def parse_city_from_filename(filename):
    """
    Извлекает название города на русском языке в нижнем регистре из имени файла.
    """
    cities = ["ижевск", "киров", "рязань", "сургут", "омск", "ульяновск", "чебоксары", "магнитогорск", "орёл", "орел"]
    fn_lower = filename.lower()
    for city in cities:
        if city in fn_lower:
            if city == "орёл":
                return "орел"
            return city
    return ""

def parse_month_name_from_filename(filename):
    """
    Извлекает название месяца на русском языке в нижнем регистре из имени файла.
    """
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        _, month, _ = match.groups()
        months_ru = {
            "01": "январь", "02": "февраль", "03": "март", "04": "апрель",
            "05": "май", "06": "июнь", "07": "июль", "08": "август",
            "09": "сентябрь", "10": "октябрь", "11": "ноябрь", "12": "декабрь"
        }
        return months_ru.get(month, "")
    return ""

class RagEngine:
    def __init__(self):
        self.settings = load_settings()
        self.chunks = []       # Список словарей {"text": str, "source": str, "type": str, ...}
        self.embeddings = None # NumPy матрица эмбеддингов
        self.vector_chunk_indices = []
        self.is_indexing = False
        
        # Попробуем загрузить существующий индекс
        self.load_index()

    def load_index(self):
        """Загружает сохраненный индекс из файла"""
        if os.path.exists(INDEX_PATH):
            try:
                with open(INDEX_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.chunks = data.get("chunks", [])
                    embed_list = data.get("embeddings", [])
                    self.vector_chunk_indices = data.get("vector_chunk_indices", [])
                    
                    # Обратная совместимость: если индексов нет, считаем, что все чанки векторизованы
                    if embed_list:
                        self.embeddings = np.array(embed_list, dtype=np.float32)
                        if not self.vector_chunk_indices:
                            self.vector_chunk_indices = list(range(len(self.chunks)))
                    else:
                        self.embeddings = None
                        self.vector_chunk_indices = []
                print(f"Загружена база знаний: {len(self.chunks)} чанков. Из них {len(self.vector_chunk_indices)} векторизовано.")
            except Exception as e:
                print(f"Ошибка загрузки индекса RAG: {e}")

    def save_index(self):
        """Сохраняет индекс в файл"""
        if not self.chunks:
            return
        try:
            embed_list = []
            if self.embeddings is not None:
                embed_list = self.embeddings.tolist()
            data = {
                "chunks": self.chunks,
                "embeddings": embed_list,
                "vector_chunk_indices": getattr(self, "vector_chunk_indices", [])
            }
            with open(INDEX_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"Индекс базы знаний сохранен: {len(self.chunks)} чанков ({len(data['vector_chunk_indices'])} с эмбеддингами).")
        except Exception as e:
            print(f"Ошибка сохранения индекса RAG: {e}")

    def _parse_txt(self, filepath, prefix=""):
        """Парсит TXT / MD файлы, разбивая по абзацам"""
        chunks = []
        filename = os.path.basename(filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            # Разбиваем по двойному переносу строки (абзацы)
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            for p in paragraphs:
                prefix_text = f"[{prefix}] " if prefix else ""
                chunks.append({
                    "text": f"{prefix_text}{p}",
                    "source": filename,
                    "type": "document",
                    "city": "",
                    "month": "",
                    "vending_id": ""
                })
        except Exception as e:
            print(f"Ошибка парсинга TXT {filepath}: {e}")
        return chunks

    def _parse_csv(self, filepath, prefix=""):
        """Парсит CSV файлы, очищает от технических хэшей/логов и делает человекочитаемый текст с префиксом категории"""
        chunks = []
        filename = os.path.basename(filepath)
        
        # Точные совпадения колонок для полного игнорирования (хэши, технические ID)
        ignored_exact_headers = [
            "id", "parkid", "park_id", "locationid", "location_id", 
            "location_fleet_id", "locationfleetid", "account", "account/sales", 
            "beaconmac", "beacon_mac", "mac", "sim", "iccid", "imei", "token", 
            "key", "disconnections"
        ]
        # Вхождения подстрок
        ignored_substrings = ["uuid", "guid", "connection", "duration", "reward", "rssi", "color"]
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                # Определяем разделитель
                sample = f.read(4096)
                f.seek(0)
                
                # Простая эвристика разделителя
                delimiter = ';'
                if ',' in sample and sample.count(',') > sample.count(';'):
                    delimiter = ','
                elif '\t' in sample:
                    delimiter = '\t'
                
                reader = csv.DictReader(f, delimiter=delimiter)
                headers = reader.fieldnames
                if not headers:
                    return []
                    
                # Очищаем заголовки от BOM символов
                headers = [h.replace('\ufeff', '').strip() for h in headers]
                f.seek(0)
                # Пропускаем строку заголовков
                next(f, None)
                # Перечитываем с чистыми заголовками
                reader = csv.DictReader(f, delimiter=delimiter, fieldnames=headers)
                rows = list(reader)
                
                month_str = parse_month_from_filename(filename)
                period_text = f"за {month_str}" if month_str else "за отчетный период"
                
                # --- ГЕНЕРАЦИЯ СВОДНЫХ ЧАНКОВ (AGGREGATION) ---
                fn_lower = filename.lower()
                
                # Сначала пытаемся определить город по названию файла
                city_name_from_fn = parse_city_from_filename(filename)
                if city_name_from_fn:
                    city_name_from_fn = city_name_from_fn.capitalize()
                
                if "revenue" in fn_lower and rows:
                    total_revenue = 0.0
                    total_orders = 0
                    machines_count = 0
                    city_name = city_name_from_fn
                    active_franchises = set()
                    
                    # Собираем данные по каждому аппарату для нахождения топ/худших
                    vending_stats = []
                    
                    for r in rows:
                        fact_val = r.get("fact", r.get("выручка", "0"))
                        if fact_val is None: fact_val = "0"
                        fact_val = fact_val.strip()
                        
                        orders_val = r.get("orders", "0")
                        if orders_val is None: orders_val = "0"
                        orders_val = orders_val.strip()
                        
                        if not city_name:
                            city_val = r.get("city", r.get("город", "")).strip()
                            if city_val:
                                city_name = city_val
                            
                        franchise_val = r.get("franchise", "").strip()
                        if franchise_val:
                            active_franchises.add(franchise_val)
                            
                        f_num = 0.0
                        try:
                            fact_val = fact_val.replace(",", ".")
                            f_num = float(fact_val) if fact_val else 0.0
                            total_revenue += f_num
                        except ValueError:
                            pass
                            
                        o_num = 0
                        try:
                            o_num = int(orders_val) if orders_val else 0
                            total_orders += o_num
                        except ValueError:
                            pass
                            
                        vending_id = r.get("vending_id", r.get("аппарат", "")).strip()
                        place_val = r.get("place_name", r.get("локация", "")).strip()
                        if vending_id:
                            machines_count += 1
                            vending_stats.append({
                                "id": vending_id,
                                "place": place_val,
                                "revenue": f_num,
                                "orders": o_num
                            })
                            
                    if not city_name:
                        city_match = re.search(r'revenue_([^_]+)_', filename)
                        if city_match:
                            city_name = city_match.group(1)
                            
                    # Сортируем аппараты по выручке для вывода лучших и худших
                    vending_stats.sort(key=lambda x: x["revenue"], reverse=True)
                    
                    top_5 = vending_stats[:5]
                    bottom_5 = vending_stats[-5:]
                    # Исключаем дублирование, если аппаратов меньше 5
                    if len(vending_stats) <= 5:
                        bottom_5 = []
                    
                    top_5_str = ", ".join([f"№{item['id']} ({item['place']}) - {item['revenue']:.2f} руб." for item in top_5])
                    
                    bottom_5_str = ""
                    if bottom_5:
                        bottom_5_str = ", ".join([f"№{item['id']} ({item['place']}) - {item['revenue']:.2f} руб." for item in bottom_5])
                    
                    if total_revenue > 0 or total_orders > 0:
                        franchise_str = f" (Франшиза: {', '.join(active_franchises)})" if active_franchises else ""
                        summary_text = (
                            f"[{prefix}] Сводные (итоговые) показатели отчета {filename} ({period_text}): "
                            f"Общая суммарная выручка по городу {city_name} {period_text} составила {total_revenue:.2f} руб.{franchise_str}. "
                            f"Всего по всем аппаратам выполнено заказов: {total_orders}. "
                            f"Общее количество аппаратов в отчете: {machines_count}. "
                            f"Средняя выручка на один аппарат: {total_revenue / (machines_count or 1):.2f} руб. "
                            f"ТОП-5 аппаратов по самой высокой выручке: {top_5_str}. "
                        )
                        if bottom_5_str:
                            summary_text += f"Худшие 5 аппаратов по выручке: {bottom_5_str}."
                            
                        chunks.append({
                            "text": summary_text,
                            "source": filename,
                            "type": "summary",
                            "city": city_name.lower() if city_name else parse_city_from_filename(filename),
                            "month": parse_month_name_from_filename(filename),
                            "vending_id": ""
                        })
                        
                elif "vending" in fn_lower and rows:
                    total_machines = len(rows)
                    statuses = {}
                    free_cells = 0
                    total_cells = 0
                    franchises = set()
                    city_name = city_name_from_fn
                    locations = []
                    
                    for r in rows:
                        status_val = r.get("статус", r.get("status", "unknown")).strip().lower()
                        statuses[status_val] = statuses.get(status_val, 0) + 1
                        
                        try:
                            free_cells += int(r.get("FreeCellsCount", "0") or "0")
                        except ValueError:
                            pass
                        try:
                            total_cells += int(r.get("CellsTotal", r.get("cells_total", "0")) or "0")
                        except ValueError:
                            pass
                            
                        franchise_val = r.get("FranchiseeName", r.get("franchise", "")).strip()
                        if franchise_val:
                            franchises.add(franchise_val)
                            
                        if not city_name:
                            city_val = r.get("город", r.get("city", "")).strip()
                            if city_val:
                                city_name = city_val
                            
                        loc_val = r.get("place_name", r.get("локация", "")).strip()
                        if loc_val and len(locations) < 5:
                            locations.append(loc_val)
                            
                    if not city_name:
                        city_match = re.search(r'vendings_([^\.]+)\.', filename)
                        if city_match:
                            city_name = city_match.group(1)
                            
                    status_parts = [f"{k}: {v}" for k, v in statuses.items()]
                    franchise_str = f"Франчайзи: {', '.join(franchises)}. " if franchises else ""
                    loc_str = f"Примеры локаций: {', '.join(locations)}. " if locations else ""
                    
                    summary_text = (
                        f"[{prefix}] Сводные показатели по оборудованию из отчета {filename}: "
                        f"Всего аппаратов в городе {city_name}: {total_machines}. {franchise_str}"
                        f"Распределение статусов оборудования — {', '.join(status_parts)}. "
                        f"Свободные ячейки во всем городе: {free_cells} шт. из {total_cells} шт. всего. "
                        f"{loc_str}"
                    )
                    chunks.append({
                        "text": summary_text,
                        "source": filename,
                        "type": "summary",
                        "city": city_name.lower() if city_name else parse_city_from_filename(filename),
                        "month": parse_month_name_from_filename(filename),
                        "vending_id": ""
                    })
                    
                elif "operation" in fn_lower and rows:
                    total_tasks = len(rows)
                    completed_tasks = 0
                    fios = set()
                    city_name = city_name_from_fn
                    task_types = {}
                    engineer_task_counts = {}
                    
                    for r in rows:
                        status_val = r.get("Итоговый статус", r.get("status", "")).strip()
                        if status_val.lower() in ["выполнена", "completed", "done", "ok"]:
                            completed_tasks += 1
                        fio_val = r.get("ФИО исполнителя", r.get("FIO", "")).strip()
                        if fio_val:
                            fios.add(fio_val)
                            engineer_task_counts[fio_val] = engineer_task_counts.get(fio_val, 0) + 1
                        if not city_name:
                            city_val = r.get("Название региона", r.get("city", "")).strip()
                            if city_val:
                                city_name = city_val
                        t_type = r.get("Тип задачи", r.get("type", "")).strip()
                        if t_type:
                            task_types[t_type] = task_types.get(t_type, 0) + 1
                            
                    if not city_name:
                        city_match = re.search(r'operations_([^_]+)_', filename)
                        if city_match:
                            city_name = city_match.group(1)
                            
                    task_parts = [f"{k} ({v} шт)" for k, v in task_types.items()]
                    
                    # Сортируем инженеров по активности
                    sorted_engineers = sorted(engineer_task_counts.items(), key=lambda x: x[1], reverse=True)
                    engineers_str = ", ".join([f"{name} ({count} зад.)" for name, count in sorted_engineers])
                    
                    summary_text = (
                        f"[{prefix}] Сводные показатели по сервису из отчета {filename} ({period_text}): "
                        f"Всего сервисных задач по региону {city_name} {period_text} запланировано/зафиксировано: {total_tasks}. "
                        f"Успешно выполнено задач: {completed_tasks} (процент выполнения: {completed_tasks / (total_tasks or 1) * 100:.1f}%). "
                        f"Задачи по типам: {', '.join(task_parts) if task_parts else 'нет данных'}. "
                        f"Работали инженеры (по количеству задач): {engineers_str if engineers_str else 'нет данных'}."
                    )
                    chunks.append({
                        "text": summary_text,
                        "source": filename,
                        "type": "summary",
                        "city": city_name.lower() if city_name else parse_city_from_filename(filename),
                        "month": parse_month_name_from_filename(filename),
                        "vending_id": ""
                    })
                
                # --- ПОСТРОЧНЫЙ ПАРСИНГ ---
                for row in rows:
                    fn_lower = filename.lower()
                    row_text = ""
                    
                    if "revenue" in fn_lower:
                        # Финансовый отчет
                        vending_id = row.get("vending_id", "").strip()
                        place_name = row.get("place_name", "").strip()
                        address = row.get("address", "").strip()
                        orders = row.get("orders", "").strip()
                        fact = row.get("fact", "").strip()
                        model = row.get("model", "").strip()
                        city = row.get("city", "").strip()
                        status = row.get("office_status", "").strip()
                        franchise = row.get("franchise", "").strip()
                        turnover = row.get("cell_turnover", "").strip()
                        
                        row_text = (
                            f"[{prefix}] Данные из финансового отчета {filename} ({period_text}): "
                            f"Аппарат №{vending_id} (модель {model}) установлен на локации '{place_name}' по адресу {address} (город {city}). "
                            f"Статус размещения: {status}. Франшиза: {franchise}. "
                            f"Статистика {period_text}: выполнено заказов = {orders}, выручка = {fact} руб., оборачиваемость ячеек = {turnover}."
                        )
                    elif "vending" in fn_lower:
                        # Статус аппарата
                        vending_id = row.get("vending_id", "").strip()
                        vending_type = row.get("VendingType", row.get("model", "")).strip()
                        serial = row.get("SerialNumber", "").strip()
                        status = row.get("статус", row.get("status", "")).strip()
                        cells = row.get("CellsTotal", row.get("cells_total", "")).strip()
                        free = row.get("FreeCellsCount", "").strip()
                        address = row.get("address", "").strip()
                        place_name = row.get("place_name", "").strip()
                        city = row.get("город", row.get("city", "")).strip()
                        franchise = row.get("FranchiseeName", row.get("franchise", "")).strip()
                        
                        row_text = (
                            f"[{prefix}] Данные об аппарате из отчета {filename}: "
                            f"Аппарат №{vending_id} (модель {vending_type}, серийный номер {serial}) расположен на локации '{place_name}' по адресу {address} ({city}). "
                            f"Франчайзи: {franchise}. Состояние аппарата: статус = {status}, свободные ячейки = {free} из {cells}."
                        )
                    elif "operation" in fn_lower:
                        # Сервисный отчет
                        fio = row.get("ФИО исполнителя", row.get("FIO", "")).strip()
                        vending_id = row.get("ID вендинга", row.get("vending_id", "")).strip()
                        task_type = row.get("Тип задачи", row.get("type", "")).strip()
                        status = row.get("Итоговый статус", row.get("status", "")).strip()
                        place_name = row.get("Название локации", row.get("place_name", "")).strip()
                        city = row.get("Название региона", row.get("city", "")).strip()
                        plan_qty = row.get("Выдать/Сдать (план)", "").strip()
                        fact_qty = row.get("Выдать/Сдать (факт)", "").strip()
                        resolution = row.get("Резолюция", "").strip()
                        date_done = row.get("Дата итогового статуса", "").strip()
                        
                        row_text = (
                            f"[{prefix}] Данные о сервисной операции из отчета {filename} ({period_text}): "
                            f"Сервисный инженер {fio} выполнил задачу '{task_type}' для аппарата №{vending_id} (локация '{place_name}', регион {city}). "
                            f"Итоговый статус задачи: {status} (завершено: {date_done}, отчет {period_text}). Объем работ (план/факт): {plan_qty}/{fact_qty} шт. "
                            f"Резолюция: {resolution}."
                        )
                    else:
                        # Резервный вариант: объединяем очищенные поля через запятую
                        row_parts = []
                        for h in headers:
                            if not h:
                                continue
                            h_lower = h.lower().strip()
                            if h_lower in ignored_exact_headers or any(sub in h_lower for sub in ignored_substrings) or re.match(r'^[or]_\\d{2}$', h_lower):
                                continue
                            val = row.get(h)
                            if val is None:
                                continue
                            val = val.strip()
                            if val == "" or val.lower() in ["null", "none", "n/a", "undefined"]:
                                continue
                            if len(val) > 30 and (any(c in val for c in ["-", "[", "{", "/"]) or val.isalnum()):
                                continue
                            label = h
                            if h_lower in ["displaynumber", "vending_id", "vending"]:
                                label = "аппарат"
                            elif h_lower in ["fact", "revenue", "выручка"]:
                                label = "выручка"
                            elif h_lower in ["place_name", "placename", "location", "address", "адрес"]:
                                label = "локация/адрес"
                            elif h_lower in ["city", "город"]:
                                label = "город"
                            elif h_lower in ["status", "статус"]:
                                label = "статус"
                            row_parts.append(f"{label}: {val}")
                        
                        if row_parts:
                            prefix_text = f"[{prefix}] " if prefix else ""
                            row_text = f"{prefix_text}Данные из отчета {filename}: " + ", ".join(row_parts)
                    
                    # Извлекаем vending_id для метаданных
                    vending_id_meta = ""
                    if "revenue" in fn_lower:
                        vending_id_meta = row.get("vending_id", "").strip()
                    elif "vending" in fn_lower:
                        vending_id_meta = row.get("vending_id", "").strip()
                    elif "operation" in fn_lower:
                        vending_id_meta = row.get("ID вендинга", row.get("vending_id", "")).strip()
                    else:
                        for k, v in row.items():
                            if k and any(x in k.lower() for x in ["vending", "аппарат"]):
                                vending_id_meta = (v or "").strip()
                                break
                    
                    if row_text:
                        chunks.append({
                            "text": row_text,
                            "source": filename,
                            "type": "raw_row",
                            "city": parse_city_from_filename(filename),
                            "month": parse_month_name_from_filename(filename),
                            "vending_id": vending_id_meta
                        })
        except Exception as e:
            print(f"Ошибка парсинга CSV {filepath}: {e}")
        return chunks

    def _parse_pdf(self, filepath, prefix=""):
        """Парсит PDF файлы с помощью pypdf"""
        chunks = []
        filename = os.path.basename(filepath)
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            
            # Извлекаем весь текст
            full_text = ""
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"
            
            # Нарезаем текст на чанки по ~600 символов с перекрытием 100 символов
            chunk_size = 600
            overlap = 100
            
            i = 0
            while i < len(full_text):
                chunk = full_text[i:i+chunk_size].strip()
                if chunk:
                    prefix_text = f"[{prefix}] " if prefix else ""
                    chunks.append({
                        "text": f"{prefix_text}{chunk}",
                        "source": filename,
                        "type": "document",
                        "city": "",
                        "month": "",
                        "vending_id": ""
                    })
                i += (chunk_size - overlap)
                
        except ImportError:
            print("Библиотека pypdf не установлена. Пропускаем PDF.")
        except Exception as e:
            print(f"Ошибка парсинга PDF {filepath}: {e}")
        return chunks

    async def get_embedding(self, text, client, ollama_url, model):
        """Запрашивает вектор для текста у Ollama"""
        # Пробуем стандартный эндпоинт /api/embeddings
        try:
            response = await client.post(
                f"{ollama_url}/api/embeddings",
                json={
                    "model": model,
                    "prompt": text
                },
                timeout=10.0
            )
            if response.status_code == 200:
                return response.json().get("embedding")
        except Exception:
            pass
            
        # Пробуем альтернативный эндпоинт /api/embed (в новых версиях)
        try:
            response = await client.post(
                f"{ollama_url}/api/embed",
                json={
                    "model": model,
                    "input": text
                },
                timeout=10.0
            )
            if response.status_code == 200:
                embeds = response.json().get("embeddings")
                if embeds:
                    return embeds[0]
        except Exception as e:
            print(f"Ошибка получения эмбеддинга для '{text[:20]}...': {e}")
            
        return None

    async def index_documents(self):
        """Сканирует папку и строит векторный индекс"""
        if self.is_indexing:
            return {"status": "error", "message": "Индексация уже запущена"}
            
        self.is_indexing = True
        self.settings = load_settings()
        ollama_url = self.settings.get("ollama_url", "http://localhost:11434")
        model = self.settings.get("ollama_embed_model", "nomic-embed-text")
        
        # Категории папок и ключевые слова
        category_prefixes = {
            "revenue": "Финансовый отчет и выручка аппарата",
            "stations": "Информация об аппарате и локации (станция)",
            "vendings": "Информация об аппарате и локации (станция)",
            "operations": "Операционный отчет о задачах и техническом обслуживании",
            "instructions": "Инструкция и регламент обслуживания"
        }
        
        def get_category_prefix(fpath):
            parent_dir = os.path.basename(os.path.dirname(fpath)).lower()
            if parent_dir in category_prefixes:
                return category_prefixes[parent_dir]
            
            fn_lower = os.path.basename(fpath).lower()
            if "revenue" in fn_lower:
                return category_prefixes["revenue"]
            elif "vending" in fn_lower:
                return category_prefixes["stations"]
            elif "operation" in fn_lower:
                return category_prefixes["operations"]
            elif fn_lower.endswith(".pdf") or "slave" in fn_lower:
                return category_prefixes["instructions"]
            return ""

        print("Начало индексации базы знаний...")
        
        all_chunks = []
        files_indexed = 0
        
        # Сканируем папку рекурсивно с поддержкой подпапок
        for root, dirs, files in os.walk(KNOWLEDGE_DIR):
            for filename in files:
                if filename == "knowledge_index.json":
                    continue
                filepath = os.path.join(root, filename)
                
                prefix = get_category_prefix(filepath)
                chunks = []
                if filename.endswith(".txt") or filename.endswith(".md"):
                    chunks = self._parse_txt(filepath, prefix=prefix)
                elif filename.endswith(".csv"):
                    chunks = self._parse_csv(filepath, prefix=prefix)
                elif filename.endswith(".pdf"):
                    chunks = self._parse_pdf(filepath, prefix=prefix)
                    
                if chunks:
                    all_chunks.extend(chunks)
                    files_indexed += 1
                    print(f"Файл {filename} (категория: '{prefix}') распарсен: {len(chunks)} чанков.")
                    
        if not all_chunks:
            self.is_indexing = False
            # Если папка пуста, сбрасываем индекс
            self.chunks = []
            self.embeddings = None
            self.vector_chunk_indices = []
            if os.path.exists(INDEX_PATH):
                try: os.remove(INDEX_PATH)
                except Exception: pass
            return {"status": "success", "message": "База знаний пуста. Индекс очищен.", "files": 0, "chunks": 0}

        # Определяем чанки, требующие эмбеддингов (summary, document)
        vectorizable_chunks = []
        for idx, chunk in enumerate(all_chunks):
            if chunk.get("type") in ["summary", "document"]:
                vectorizable_chunks.append((idx, chunk))

        print(f"Всего чанков: {len(all_chunks)}. Векторизуются: {len(vectorizable_chunks)} чанков.")
        
        embeddings_list = []
        vector_chunk_indices = []
        
        async with httpx.AsyncClient(trust_env=False, timeout=30.0) as client:
            for progress_idx, (orig_idx, chunk) in enumerate(vectorizable_chunks):
                vector = await self.get_embedding(chunk["text"], client, ollama_url, model)
                if vector:
                    embeddings_list.append(vector)
                    vector_chunk_indices.append(orig_idx)
                if progress_idx % 10 == 0 and progress_idx > 0:
                    print(f"Прогресс эмбеддингов: {progress_idx}/{len(vectorizable_chunks)}")
                    
        self.chunks = all_chunks
        self.vector_chunk_indices = vector_chunk_indices
        if embeddings_list:
            self.embeddings = np.array(embeddings_list, dtype=np.float32)
            self.save_index()
            self.is_indexing = False
            return {
                "status": "success",
                "message": "База знаний успешно переиндексирована",
                "files": files_indexed,
                "chunks": len(self.chunks),
                "vectorized_chunks": len(self.vector_chunk_indices)
            }
        else:
            self.embeddings = None
            self.save_index()
            self.is_indexing = False
            return {
                "status": "warning",
                "message": "База знаний сохранена без векторных эмбеддингов. Проверьте подключение к Ollama."
            }

    async def search(self, query, top_k=3):
        """Ищет наиболее релевантные чанки для вопроса с использованием гибридного скоринга (ID и города)"""
        if not self.chunks:
            return []
            
        self.settings = load_settings()
        ollama_url = self.settings.get("ollama_url", "http://localhost:11434")
        model = self.settings.get("ollama_embed_model", "nomic-embed-text")
        
        # Получаем эмбеддинг запроса, если доступен
        query_vector = None
        if self.embeddings is not None and len(self.embeddings) > 0:
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                    query_vector = await self.get_embedding(query, client, ollama_url, model)
            except Exception as e:
                print(f"Ошибка получения эмбеддинга запроса при поиске: {e}")
                
        # Инициализируем скоры для всех чанков нулями
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        
        # Если есть эмбеддинг, рассчитываем косинусное сходство для векторизованных чанков
        if query_vector is not None:
            q_vec = np.array(query_vector, dtype=np.float32)
            dot_products = np.dot(self.embeddings, q_vec)
            norms_matrix = np.linalg.norm(self.embeddings, axis=1)
            norm_query = np.linalg.norm(q_vec)
            similarities = dot_products / (norms_matrix * norm_query + 1e-8)
            
            # Переносим сходство в общий массив скоров
            for i, orig_idx in enumerate(self.vector_chunk_indices):
                if orig_idx < len(scores):
                    scores[orig_idx] = float(similarities[i])
                    
        # --- ГИБРИДНЫЙ БУСТИНГ И ФИЛЬТРАЦИЯ ---
        # 1. Извлекаем ID аппаратов (4-6 значные числа)
        query_numbers = re.findall(r'\b\d{4,6}\b', query)
        
        # 2. Извлекаем города
        city_stems = {
            "ижевск": ["ижевск"],
            "киров": ["киров"],
            "рязань": ["рязан"],
            "сургут": ["сургут"],
            "омск": ["омск"],
            "ульяновск": ["ульянов"],
            "чебоксары": ["чебоксар"],
            "магнитогорск": ["магнитогор"],
            "орёл": ["орл", "орел", "орёл"]
        }
        
        query_lower = query.lower()
        found_cities = []
        for city_name, stems in city_stems.items():
            if any(stem in query_lower for stem in stems):
                found_cities.append(city_name)
                if city_name == "орёл":
                    found_cities.append("орел")
                    
        # 3. Извлекаем месяцы
        month_stems = {
            "январь": ["январ"], "февраль": ["феврал"], "март": ["март"], "апрель": ["апрел"],
            "май": ["май", "мая"], "июнь": ["июн"], "июль": ["июл"], "август": ["август"],
            "сентябрь": ["сентябр"], "октябрь": ["октябр"], "ноябрь": ["ноябр"], "декабрь": ["декабр"]
        }
        found_months = []
        for m_name, stems in month_stems.items():
            if any(stem in query_lower for stem in stems):
                found_months.append(m_name)
                
        # 4. Проверяем, является ли запрос финансовым
        revenue_keywords = ["выручка", "выручку", "заработал", "доход", "прибыль", "fact", "revenue"]
        is_revenue_query = any(kw in query_lower for kw in revenue_keywords)
        
        # Проходим по всем чанкам и корректируем их скоры
        for idx in range(len(self.chunks)):
            chunk = self.chunks[idx]
            chunk_type = chunk.get("type", "document")
            chunk_city = chunk.get("city", "")
            chunk_month = chunk.get("month", "")
            chunk_vending_id = chunk.get("vending_id", "")
            source_lower = chunk.get("source", "").lower()
            
            # Фильтрация/бустинг по ID аппарата
            if query_numbers:
                if chunk_vending_id in query_numbers:
                    scores[idx] += 1.5
                else:
                    scores[idx] -= 0.5
                    
            # Фильтрация/бустинг по городу
            if found_cities:
                if chunk_city:
                    if chunk_city not in found_cities:
                        scores[idx] -= 5.0 # Жесткий штраф для чужих городов
                    else:
                        scores[idx] += 0.3 # Буст для совпавшего города
                        
            # Бустинг сводок при совпадении города и месяца
            if chunk_type == "summary":
                if found_cities and chunk_city in found_cities:
                    scores[idx] += 0.4
                    if found_months and chunk_month in found_months:
                        scores[idx] += 1.0 # Огромный буст за точное совпадение города и периода
                    elif not found_months and not chunk_month:
                        scores[idx] += 1.0 # Буст для сводок без месяца (например, vendings)
                        
            # Буст финансовых сводок для финансовых вопросов
            if is_revenue_query:
                if "revenue" in source_lower:
                    if chunk_type == "summary":
                        scores[idx] += 0.3
                    else:
                        scores[idx] += 0.1
                elif "operations" in source_lower:
                    scores[idx] -= 0.2
                    
        # Сортируем индексы по убыванию скоров
        top_indices = np.argsort(scores)[::-1]
        
        results = []
        for idx in top_indices:
            score = float(scores[idx])
            # Порог отсечения
            if score > 0.25:
                chunk = self.chunks[idx].copy()
                chunk["score"] = score
                results.append(chunk)
                if len(results) >= top_k:
                    break
                    
        return results
