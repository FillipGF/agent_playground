import asyncio
import edge_tts
import miniaudio
import sounddevice as sd
import numpy as np
import threading
import os
from .config import load_settings

class TTSEngine:
    def __init__(self, on_speak_status_cb=None):
        self.settings = load_settings()
        self.playback_thread = None
        self.playback_lock = threading.Lock()
        self.on_speak_status = on_speak_status_cb
        
    def say(self, text, output_device_id=None):
        """
        Синтезирует текст и проигрывает его в фоновом потоке на указанном устройстве.
        """
        # Запускаем воспроизведение в отдельном потоке, чтобы не блокировать бэкенд
        thread = threading.Thread(target=self._generate_and_play, args=(text, output_device_id), daemon=True)
        thread.start()
        return thread

    def _generate_and_play(self, text, output_device_id):
        with self.playback_lock:
            # Запускаем event loop для асинхронного edge-tts
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                mp3_data = loop.run_until_complete(self._generate_audio(text))
                loop.close()
                
                if not mp3_data:
                    return
                    
                # Декодируем
                decoded = miniaudio.decode(mp3_data)
                samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
                
                # Если девайс вывода не указан, берем из настроек или дефолтный
                self.settings = load_settings()
                if output_device_id is None:
                    # Для озвучивания "от лица пользователя" на встрече, звук нужно выводить 
                    # либо в динамики (для теста), либо в виртуальный кабель (для созвона).
                    # Мы берем mic_device_id или loopback_device_id или системный дефолт.
                    # По умолчанию sd.play использует дефолтное устройство вывода.
                    output_device_id = self.settings.get("tts_device_id") # Специальная настройка для TTS вывода
                    
                if self.on_speak_status:
                    self.on_speak_status(True)
                    
                # Проигрываем через sounddevice
                sd.play(samples, decoded.sample_rate, device=output_device_id)
                sd.wait() # Ждем окончания воспроизведения
                
            except Exception as e:
                print(f"Ошибка воспроизведения TTS: {e}")
            finally:
                if self.on_speak_status:
                    self.on_speak_status(False)

    async def _generate_audio(self, text):
        self.settings = load_settings()
        voice = self.settings.get("tts_voice", "ru-RU-DmitryNeural")
        rate = self.settings.get("tts_rate", "+15%")
        
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            mp3_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_data += chunk["data"]
            return mp3_data
        except Exception as e:
            print(f"Ошибка генерации edge-tts: {e}")
            return None

    def save_to_file(self, text, filename):
        """Синтезирует речь и сохраняет в MP3 файл (для веб-интерфейса)"""
        self.settings = load_settings()
        voice = self.settings.get("tts_voice", "ru-RU-DmitryNeural")
        rate = self.settings.get("tts_rate", "+15%")
        
        async def _save():
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(filename)
            
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_save())
            loop.close()
            return True
        except Exception as e:
            print(f"Ошибка сохранения TTS в файл {filename}: {e}")
            return False
