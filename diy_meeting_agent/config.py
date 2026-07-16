import os
import json

# Базовые пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "meetings_history")
CONFIG_PATH = os.path.join(BASE_DIR, "settings.json")

# Создаем папку для истории встреч, если её нет
os.makedirs(HISTORY_DIR, exist_ok=True)

# Значения по умолчанию
DEFAULT_SETTINGS = {
    # Аудио
    "mic_device_id": None,          # ID микрофона пользователя (None - по умолчанию)
    "loopback_device_id": None,     # ID системного вывода (None - автопоиск WASAPI loopback)
    "sample_rate": 16000,           # Частота дискретизации для Whisper
    
    # Агент
    "wake_word": "вопрос к залу",
    "language": "ru",
    "user_name": "Пользователь",
    
    # LLM
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:7b",   # Рекомендуемая модель для русского языка
    "ollama_embed_model": "nomic-embed-text", # Модель для векторной базы знаний RAG
    
    # TTS
    "tts_voice": "ru-RU-DmitryNeural", # Голоса: ru-RU-DmitryNeural, ru-RU-SvetlanaNeural
    "tts_enabled": True,
    
    # Whisper
    "whisper_model": "base"
}

def load_settings():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
                # Дополняем отсутствующие ключи дефолтными
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in settings:
                        settings[k] = v
                return settings
        except Exception as e:
            print(f"Ошибка загрузки настроек: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"Ошибка сохранения настроек: {e}")
        return False
