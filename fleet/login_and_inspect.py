# -*- coding: utf-8 -*-
import os
import sys
import time
from playwright.sync_api import sync_playwright

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    print(f"Используем папку профиля Chrome: {profile_dir}")

    with sync_playwright() as p:
        print("Запуск браузера Chromium (в видимом режиме)...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            viewport={"width": 1280, "height": 800}
        )

        page = context.pages[0] if context.pages else context.new_page()

        url = "https://fleet.yandex.ru/snickers/vendings?park_id=bb7d9e38e7044432adb1f30c684ab751"
        print(f"Переход на страницу: {url}")
        page.goto(url)

        print("\n" + "="*80)
        print("ИНСТРУКЦИЯ:")
        print("1. В открывшемся окне браузера войдите в аккаунт Яндекс (если вы еще не вошли).")
        print("2. Дождитесь полной загрузки страницы Яндекс.Таксометр / Вендинги.")
        print("3. Как только страница с аппаратами/вендингами загрузится, вернитесь в консоль")
        print("   и нажмите ENTER (или введите любой текст и нажмите ENTER) для сканирования кнопок.")
        print("="*80 + "\n")

        # Ждем ввода от пользователя
        try:
            val = input("Нажмите ENTER после того как успешно вошли в аккаунт и видите список аппаратов: ")
        except Exception as e:
            print(f"Не удалось прочитать ввод из стандартного потока ввода: {e}")
            print("Ожидаем 30 секунд для ручного входа...")
            time.sleep(30)

        print("Начинаем сканирование кнопок...")
        # Дадим еще 2 секунды на всякий случай
        page.wait_for_timeout(2000)

        # Сканируем элементы кнопок
        elements = page.query_selector_all("button, [role='button'], a, div[class*='button']")
        
        debug_tools_dir = os.path.abspath(os.path.join(base_dir, "..", "debug_tools"))
        os.makedirs(debug_tools_dir, exist_ok=True)
        output_file = os.path.join(debug_tools_dir, "detected_buttons.txt")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"Найдено кликабельных элементов после входа: {len(elements)}\n\n")
            for idx, el in enumerate(elements, 1):
                try:
                    tag_name = el.evaluate("el => el.tagName").lower()
                    inner_text = el.inner_text().strip().replace('\n', ' ')
                    outer_html = el.evaluate("el => el.outerHTML")
                    attributes = el.evaluate("""el => {
                        let attrs = {};
                        for (let attr of el.attributes) {
                            attrs[attr.name] = attr.value;
                        }
                        return attrs;
                    }""")
                    
                    short_html = outer_html[:300] + "..." if len(outer_html) > 300 else outer_html
                    
                    f.write(f"Элемент #{idx}:\n")
                    f.write(f"  Тег: {tag_name}\n")
                    f.write(f"  Текст: '{inner_text}'\n")
                    f.write(f"  Атрибуты: {attributes}\n")
                    f.write(f"  HTML: {short_html}\n")
                    f.write("-" * 50 + "\n")
                except Exception:
                    pass

        print(f"\nСканирование завершено! Результаты записаны в файл: {os.path.abspath(output_file)}")
        print("Закрываем браузер через 3 секунды...")
        page.wait_for_timeout(3000)
        context.close()

if __name__ == "__main__":
    main()
