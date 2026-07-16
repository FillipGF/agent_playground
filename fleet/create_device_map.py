# -*- coding: utf-8 -*-
"""
Интерактивная карта аппаратов Яндекс.Флит.

Источники данных:
  * revenue_{Город}_{дата}.csv  — поля: vending_id, place_name, address,
                                         place_date, jewelry, office_status,
                                         remove_date
  * vendings_{Город}.csv        — поля: DisplayNumber, LocationLatitude,
                                         LocationLongitude, PlaceName, Address

Фильтрация:
  - office_status == "placed"
  - remove_date == "01.02.2222"  (заглушка = аппарат ещё на локации)

Запуск:
    uv run --with pandas --with folium python create_device_map.py
"""

import pathlib
import re
import glob
import json
from datetime import datetime
import pandas as pd
import folium

# ---------- Конфигурация ----------
BASE_DIR   = pathlib.Path(__file__).resolve().parent   # fleet/
INPUTS_DIR = BASE_DIR / "inputs"
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUT_FILE = OUTPUTS_DIR / "devices_map.html"

# Ровно 9 городов, чьи файлы лежат в inputs/
CITIES = [
    "Омск",
    "Магнитогорск",
    "Сургут",
    "Ижевск",
    "Ульяновск",
    "Рязань",
    "Киров",
    "Чебоксары",
    "Орёл",
]

# Заглушка remove_date = аппарат ещё на локации
REMOVE_DATE_PLACEHOLDER = "2222-02-01"

# Цветовая схема по категории jewelry (значения в CSV: Gold, Silver, Bronze, Platinum, 0, new)
JEWELRY_COLORS = {
    "gold":     "#DAA520",   # золотой
    "silver":   "#A8A9AD",   # серебряный
    "bronze":   "#CD7F32",   # бронзовый
    "platinum": "#D5D6D8",   # платиновый
    "new":      "#60A5FA",   # новый аппарат — голубой
    "0":        "#6B7280",   # серый (нет категории)
}

# Радиус маркера по категории (крупнее = важнее)
JEWELRY_RADIUS = {
    "gold":     10,
    "platinum": 8,
    "silver":   7,
    "bronze":   6,
    "new":      5,
    "0":        5,
}


# ---------- Вспомогательные функции ----------

def _extract_date(path: pathlib.Path) -> datetime:
    """Извлекает дату из имени файла (формат ГГГГ-ММ-ДД или ГГГГММДД)."""
    match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", path.name)
    if not match:
        return datetime.min
    y, m, d = match.groups()
    return datetime(int(y), int(m), int(d))


def _latest_revenue(city: str) -> pathlib.Path | None:
    """Возвращает самый свежий revenue-файл для города."""
    candidates = [pathlib.Path(p)
                  for p in glob.glob(str(INPUTS_DIR / f"revenue*{city}*.csv"))]
    if not candidates:
        return None
    return max(candidates, key=_extract_date)


# ---------- Загрузка данных ----------

def load_city(city: str) -> pd.DataFrame | None:
    """
    Загружает и объединяет revenue + vendings для одного города.
    Применяет фильтры:
      - office_status == "placed"
      - remove_date == REMOVE_DATE_PLACEHOLDER

    Args:
        city (str): Название города (совпадает с частью имени файла).

    Returns:
        pd.DataFrame | None: объединённый датафрейм или None при отсутствии файлов.
    """
    rev_path = _latest_revenue(city)
    ven_path = INPUTS_DIR / f"vendings_{city}.csv"

    if rev_path is None:
        print(f"[WARN] revenue-файл для '{city}' не найден — город пропущен.")
        return None
    if not ven_path.exists():
        print(f"[WARN] vendings-файл для '{city}' не найден — город пропущен.")
        return None

    df_rev = pd.read_csv(rev_path, sep=",", encoding="utf-8", decimal=".")
    df_ven = pd.read_csv(ven_path, sep=",", encoding="utf-8", decimal=".")

    n_before = len(df_rev)

    # --- ФИЛЬТР 1: только аппараты на локации ---
    df_rev = df_rev[df_rev["office_status"].astype(str).str.strip() == "placed"]

    # --- ФИЛЬТР 2: заглушка remove_date = ещё стоит ---
    df_rev = df_rev[df_rev["remove_date"].astype(str).str.strip() == REMOVE_DATE_PLACEHOLDER]

    n_after = len(df_rev)
    print(f"[INFO] {city}: {n_before} -> {n_after} аппаратов (placed + not removed)")

    if df_rev.empty:
        print(f"[WARN] {city}: после фильтрации нет аппаратов.")
        return None

    # --- QA: проверяем DisplayNumber на NULL ---
    null_dn = df_ven["DisplayNumber"].isna().sum()
    if null_dn:
        print(f"[WARN] {city}: {null_dn} строк в vendings без DisplayNumber — исключены.")
    df_ven = df_ven.dropna(subset=["DisplayNumber"])
    df_ven["DisplayNumber"] = df_ven["DisplayNumber"].astype(int)

    # Нам нужны только необходимые колонки из vendings
    ven_cols = ["DisplayNumber", "LocationLatitude", "LocationLongitude",
                "PlaceName", "Address"]
    df_ven = df_ven[ven_cols].drop_duplicates(subset=["DisplayNumber"])

    # Джойн: revenue.vending_id == vendings.DisplayNumber
    df_rev["vending_id"] = pd.to_numeric(df_rev["vending_id"], errors="coerce")
    df_merged = df_rev.merge(
        df_ven,
        left_on="vending_id",
        right_on="DisplayNumber",
        how="inner"
    )

    # Оставляем только аппараты с координатами
    df_merged = df_merged.dropna(subset=["LocationLatitude", "LocationLongitude"])
    df_merged["LocationLatitude"]  = df_merged["LocationLatitude"].astype(float)
    df_merged["LocationLongitude"] = df_merged["LocationLongitude"].astype(float)

    df_merged["_city"] = city
    return df_merged


def load_all() -> pd.DataFrame:
    """
    Загружает данные по всем 9 городам и возвращает единый датафрейм.
    """
    frames = [load_city(c) for c in CITIES]
    frames = [f for f in frames if f is not None]
    if not frames:
        raise RuntimeError("Не найдено ни одного файла данных. Проверьте папку inputs/.")
    return pd.concat(frames, ignore_index=True)


# ---------- Построение карты ----------

def _city_centers(df: pd.DataFrame) -> dict:
    """
    Вычисляет центр каждого города как среднее координат всех его аппаратов.

    Args:
        df (pd.DataFrame): общий датафрейм.

    Returns:
        dict: {city_name: [lat, lon]}
    """
    centers = {}
    for city, grp in df.groupby("_city"):
        centers[city] = [
            round(grp["LocationLatitude"].mean(), 5),
            round(grp["LocationLongitude"].mean(), 5),
        ]
    return centers


def build_map(df: pd.DataFrame) -> folium.Map:
    """
    Создаёт Folium-карту с CircleMarker для каждого аппарата.

    Маркеры разбиты по Leaflet FeatureGroup — по одной группе на категорию jewelry.
    Панель «Jewelry» в нижнем левом углу содержит кнопки-переключатели:
        клик → скрыть категорию, повторный клик → показать обратно.

    Popup содержит:
        * Номер аппарата (vending_id)
        * Название заведения (PlaceName)
        * Адрес (Address)
        * Дата установки (place_date)
        * Город
        * Категория Jewelry

    Args:
        df (pd.DataFrame): объединённый датафрейм всех городов.

    Returns:
        folium.Map: готовая карта.
    """
    avg_lat = df["LocationLatitude"].mean()
    avg_lon = df["LocationLongitude"].mean()

    m = folium.Map(
        location=[avg_lat, avg_lon],
        zoom_start=5,
        tiles="CartoDB positron"
    )

    map_var = m.get_name()   # JS-имя объекта карты, например "map_abc123"

    # ---------- FeatureGroup на каждую категорию jewelry ----------
    # Порядок отображения: от важнейшей к наименее важной
    JEWELRY_ORDER = ["gold", "platinum", "silver", "bronze", "new", "0"]
    JEWELRY_LABELS = {
        "gold":     "Gold",
        "platinum": "Platinum",
        "silver":   "Silver",
        "bronze":   "Bronze",
        "new":      "New",
        "0":        "0",
    }

    groups: dict[str, folium.FeatureGroup] = {}
    for key in JEWELRY_ORDER:
        fg = folium.FeatureGroup(name=key, show=True)
        groups[key] = fg

    for _, row in df.iterrows():
        lat = row["LocationLatitude"]
        lon = row["LocationLongitude"]
        jewelry_raw = str(row.get("jewelry", "0")).strip()
        jewelry_key = jewelry_raw.lower()
        color  = JEWELRY_COLORS.get(jewelry_key, "#6B7280")
        radius = JEWELRY_RADIUS.get(jewelry_key, 5)

        place_name = row.get("PlaceName") or row.get("place_name", "—")
        address    = row.get("Address")  or row.get("address",    "—")

        popup_html = (
            "<div style='font-family:Arial,sans-serif;font-size:13px;line-height:1.7'>"
            f"<b>Аппарат №:</b> {int(row['vending_id'])}<br>"
            f"<b>Заведение:</b> {place_name}<br>"
            f"<b>Адрес:</b> {address}<br>"
            f"<b>Дата установки:</b> {row.get('place_date', '—')}<br>"
            f"<b>Город:</b> {row.get('_city', '—')}<br>"
            f"<b>Jewelry:</b> <span style='color:{color};font-weight:bold'>"
            f"{jewelry_raw.title()}</span>"
            "</div>"
        )

        marker = folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=color,
            weight=1.5,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=f"#{int(row['vending_id'])} | {jewelry_raw.title()}",
        )
        # Добавляем в соответствующую группу (или в "0" если ключ не известен)
        target_group = groups.get(jewelry_key, groups["0"])
        marker.add_to(target_group)

    # Добавляем все группы на карту
    for fg in groups.values():
        fg.add_to(m)

    # ---------- Панель Jewelry с кнопками-переключателями ----------
    # Формируем JS-имена FeatureGroup объектов через их get_name()
    group_js_names = {key: groups[key].get_name() for key in JEWELRY_ORDER}

    toggle_buttons = []
    for key in JEWELRY_ORDER:
        color   = JEWELRY_COLORS.get(key, "#6B7280")
        label   = JEWELRY_LABELS[key]
        fg_var  = group_js_names[key]
        count   = int((df["jewelry"].astype(str).str.strip().str.lower() == key).sum())
        # btn_id используется для управления стилем из JS
        btn_id  = f"jwbtn-{key}"
        toggle_buttons.append(
            f'<button id="{btn_id}" '
            f'onclick="toggleJewelry(\'{btn_id}\', {fg_var}, \'{map_var}\')" '
            f'style="display:flex;align-items:center;gap:8px;width:100%;'
            f'margin-bottom:5px;padding:7px 10px;'
            f'background:white;color:#333;border:1.5px solid #e2e8f0;'
            f'border-radius:8px;font-size:12px;cursor:pointer;text-align:left;'
            f'transition:all 0.18s;font-family:Arial,sans-serif;">'
            f'<span style="width:13px;height:13px;border-radius:50%;'
            f'background:{color};display:inline-block;flex-shrink:0;"></span>'
            f'<span>{label}</span>'
            f'<span style="margin-left:auto;opacity:.55;font-size:11px;">({count})</span>'
            f'</button>'
        )

    legend_html = (
        '<div style="position:fixed;bottom:30px;left:30px;z-index:1000;'
        'background:white;padding:12px 14px;border-radius:12px;'
        'box-shadow:0 2px 12px rgba(0,0,0,.2);font-family:Arial,sans-serif;'
        'min-width:180px;">'
        '<b style="font-size:13px;display:block;margin-bottom:8px;color:#1e293b;">'
        '&#9671;&nbsp;Jewelry</b>'
        '<div style="font-size:11px;color:#94a3b8;margin-bottom:8px;">'
        'Нажмите для скрытия/показа</div>'
        + "".join(toggle_buttons) +
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # JS: функция переключения видимости группы
    toggle_js = f"""
    <script>
    function toggleJewelry(btnId, featureGroup, mapVarName) {{
        var map = window[mapVarName] || {map_var};
        var btn = document.getElementById(btnId);
        if (map.hasLayer(featureGroup)) {{
            map.removeLayer(featureGroup);
            btn.style.opacity = '0.4';
            btn.style.textDecoration = 'line-through';
        }} else {{
            map.addLayer(featureGroup);
            btn.style.opacity = '1';
            btn.style.textDecoration = 'none';
        }}
    }}
    </script>
    """
    m.get_root().html.add_child(folium.Element(toggle_js))

    # ---------- Кнопки навигации по городам (верхний правый угол) ----------
    city_centers = _city_centers(df)
    city_counts  = df.groupby("_city").size().to_dict()
    sorted_cities = sorted(city_centers.keys())

    city_buttons = []
    city_js = []
    for i, city in enumerate(sorted_cities):
        lat, lon = city_centers[city]
        count    = city_counts.get(city, 0)
        fn       = f"goTo_{i}"
        city_js.append(
            f"function {fn}() {{ {map_var}.setView([{lat}, {lon}], 12); }}"
        )
        city_buttons.append(
            f'<button onclick="{fn}()" '
            f'style="display:block;width:100%;margin-bottom:4px;padding:6px 10px;'
            f'background:#1e3a5f;color:white;border:none;border-radius:7px;'
            f'font-size:12px;cursor:pointer;text-align:left;transition:background 0.2s;'
            f'font-family:Arial,sans-serif;" '
            f'onmouseover="this.style.background=\'#2d5490\'" '
            f'onmouseout="this.style.background=\'#1e3a5f\'">'
            f'{city} <span style="opacity:.65;font-size:11px;">({count})</span>'
            f'</button>'
        )

    nav_html = (
        '<div style="position:fixed;top:80px;right:10px;z-index:1000;'
        'background:white;padding:10px 12px;border-radius:12px;'
        'box-shadow:0 2px 10px rgba(0,0,0,.25);font-family:Arial,sans-serif;'
        'min-width:175px;">'
        '<b style="font-size:13px;display:block;margin-bottom:7px;color:#1e293b;">'
        '&#128205;&nbsp;Города</b>'
        + "".join(city_buttons) +
        "</div>"
    )
    city_js_script = f"<script>{''.join(city_js)}</script>"

    m.get_root().html.add_child(folium.Element(nav_html))
    m.get_root().html.add_child(folium.Element(city_js_script))

    return m


# ---------- Точка входа ----------

def main():
    df = load_all()
    print(f"[INFO] Итого аппаратов на карте: {len(df)}")

    m = build_map(df)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(OUTPUT_FILE))
    print(f"[OK] Karta sokhranena: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
