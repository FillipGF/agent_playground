# -*- coding: utf-8 -*-
import os
import sys
import shutil
import zipfile

def pack_profile(profile_dir=None, output_zip=None):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if profile_dir is None:
        profile_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))
    if output_zip is None:
        output_zip = os.path.abspath(os.path.join(base_dir, "chrome_profile.zip"))

    if not os.path.exists(profile_dir):
        print(f"Ошибка: Директория {profile_dir} не существует.")
        return False
    
    print(f"Упаковка профиля '{profile_dir}' в архив '{output_zip}'...")
    try:
        # Исключаем временные файлы, кеш и сокеты для уменьшения размера
        exclude_dirs = {"Cache", "CachedData", "Code Cache", "GPUCache", "Crashpad"}
        
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(profile_dir):
                # Фильтруем папки кеша на лету
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(profile_dir))
                    zipf.write(file_path, arcname)
                    
        size_mb = os.path.getsize(output_zip) / (1024 * 1024)
        print(f"Успешно упаковано! Размер архива: {size_mb:.2f} MB")
        print("Теперь вы можете скопировать этот файл на сервер.")
        return True
    except Exception as e:
        print(f"Ошибка при упаковке: {e}")
        return False

def unpack_profile(zip_path=None, target_dir=None):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if zip_path is None:
        zip_path = os.path.abspath(os.path.join(base_dir, "chrome_profile.zip"))
    if target_dir is None:
        target_dir = os.path.abspath(os.path.join(base_dir, ".chrome_profile"))

    if not os.path.exists(zip_path):
        print(f"Ошибка: Архив {zip_path} не найден.")
        return False
        
    print(f"Распаковка архива '{zip_path}' в '{target_dir}'...")
    try:
        if os.path.exists(target_dir):
            print(f"Удаление существующей папки {target_dir}...")
            shutil.rmtree(target_dir)
            
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(os.path.dirname(target_dir))
        print(f"Успешно распаковано в {target_dir}!")
        return True
    except Exception as e:
        print(f"Ошибка при распаковке: {e}")
        return False

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python pack_profile.py pack    - упаковать .chrome_profile в chrome_profile.zip")
        print("  python pack_profile.py unpack  - распаковать chrome_profile.zip в .chrome_profile")
        sys.exit(1)
        
    cmd = sys.argv[1].lower()
    if cmd == "pack":
        pack_profile()
    elif cmd == "unpack":
        unpack_profile()
    else:
        print(f"Неизвестная команда: {cmd}")
