import pyaudiowpatch as pyaudio
import numpy as np
import threading
import queue
import time
from .config import load_settings

class AudioCapture:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.settings = load_settings()
        
        self.mic_stream = None
        self.loopback_stream = None
        
        self.mic_queue = queue.Queue()
        self.loopback_queue = queue.Queue()
        
        self.is_recording = False
        self.lock = threading.Lock()
        
        self.target_sample_rate = 16000 # Whisper ожидает 16кГц
        self.is_speaking = False
        
    def get_devices(self):
        """Возвращает список доступных устройств ввода и loopback устройств"""
        devices = {
            "mics": [],
            "loopbacks": []
        }
        for i in range(self.p.get_device_count()):
            dev = self.p.get_device_info_by_index(i)
            # Проверяем, WASAPI ли это
            try:
                host_api = self.p.get_host_api_info_by_index(dev["hostApi"])
                is_wasapi = "wasapi" in host_api["name"].lower()
            except Exception:
                is_wasapi = False
                
            device_info = {
                "id": dev["index"],
                "name": dev["name"],
                "channels": dev["maxInputChannels"],
                "rate": int(dev["defaultSampleRate"]),
                "is_loopback": dev.get("isLoopbackDevice", False)
            }
            
            if dev.get("isLoopbackDevice", False):
                devices["loopbacks"].append(device_info)
            elif dev["maxInputChannels"] > 0:
                devices["mics"].append(device_info)
                
        return devices

    def resample(self, data, orig_rate, target_rate):
        """Быстрый ресемплинг аудио с помощью линейной интерполяции numpy"""
        if orig_rate == target_rate:
            return data
        duration = len(data) / orig_rate
        target_length = int(duration * target_rate)
        orig_indices = np.arange(len(data))
        target_indices = np.linspace(0, len(data) - 1, target_length)
        return np.interp(target_indices, orig_indices, data).astype(np.float32)

    def _mic_callback(self, in_data, frame_count, time_info, status):
        if status:
            pass # Можно логировать переполнение буфера
        # Преобразуем байты во float32
        audio_data = np.frombuffer(in_data, dtype=np.float32)
        
        # Если каналов > 1, переводим в моно
        channels = self.mic_channels
        if channels > 1:
            audio_data = np.mean(audio_data.reshape(-1, channels), axis=1)
            
        # Ресемплинг до 16кГц
        audio_16k = self.resample(audio_data, self.mic_rate, self.target_sample_rate)
        self.mic_queue.put(audio_16k)
        return (None, pyaudio.paContinue)

    def _loopback_callback(self, in_data, frame_count, time_info, status):
        if status:
            pass
        if self.is_speaking:
            # Если бот говорит сам, мы подменяем звук системы на тишину (нули), чтобы избежать эхо
            audio_data = np.zeros(frame_count * self.loopback_channels, dtype=np.float32)
        else:
            audio_data = np.frombuffer(in_data, dtype=np.float32)
        
        # Переводим в моно
        channels = self.loopback_channels
        if channels > 1:
            audio_data = np.mean(audio_data.reshape(-1, channels), axis=1)
            
        # Ресемплинг до 16кГц
        audio_16k = self.resample(audio_data, self.loopback_rate, self.target_sample_rate)
        self.loopback_queue.put(audio_16k)
        return (None, pyaudio.paContinue)

    def _open_loopback_stream(self, device_id):
        dev_info = self.p.get_device_info_by_index(device_id)
        self.loopback_channels = dev_info["maxInputChannels"]
        self.loopback_rate = int(dev_info["defaultSampleRate"])
        
        self.loopback_stream = self.p.open(
            format=pyaudio.paFloat32,
            channels=self.loopback_channels,
            rate=self.loopback_rate,
            input=True,
            input_device_index=device_id,
            stream_callback=self._loopback_callback,
            frames_per_buffer=1024
        )
        print(f"Запущен захват системы (loopback): {dev_info['name']}")

    def start(self):
        with self.lock:
            if self.is_recording:
                return
            
            self.settings = load_settings()
            
            # Очищаем очереди
            while not self.mic_queue.empty():
                self.mic_queue.get()
            while not self.loopback_queue.empty():
                self.loopback_queue.get()
                
            # Инициализируем микрофон
            mic_id = self.settings.get("mic_device_id")
            if mic_id is None:
                # Берем дефолтный микрофон
                try:
                    default_mic = self.p.get_default_input_device_info()
                    mic_id = default_mic["index"]
                except OSError:
                    print("Микрофон ввода не найден!")
                    mic_id = None
            
            # Инициализируем loopback
            loopback_id = self.settings.get("loopback_device_id")
            is_valid_loopback = False
            if loopback_id is not None:
                try:
                    dev_info = self.p.get_device_info_by_index(loopback_id)
                    if dev_info.get("isLoopbackDevice", False):
                        is_valid_loopback = True
                except Exception:
                    pass
            
            if not is_valid_loopback:
                if loopback_id is not None:
                    print(f"Устройство #{loopback_id} не является WASAPI Loopback. Ищем дефолтное...")
                try:
                    default_loopback = self.p.get_default_wasapi_loopback()
                    loopback_id = default_loopback["index"]
                except OSError:
                    print("WASAPI Loopback-устройство по умолчанию не найдено!")
                    loopback_id = None
            
            # Запускаем поток микрофона
            if mic_id is not None:
                try:
                    dev_info = self.p.get_device_info_by_index(mic_id)
                    self.mic_channels = dev_info["maxInputChannels"]
                    self.mic_rate = int(dev_info["defaultSampleRate"])
                    
                    self.mic_stream = self.p.open(
                        format=pyaudio.paFloat32,
                        channels=self.mic_channels,
                        rate=self.mic_rate,
                        input=True,
                        input_device_index=mic_id,
                        stream_callback=self._mic_callback,
                        frames_per_buffer=1024
                    )
                    print(f"Запущен захват микрофона: {dev_info['name']}")
                except Exception as e:
                    print(f"Ошибка запуска микрофона #{mic_id}: {e}")
                    self.mic_stream = None
            
            # Запускаем поток loopback
            if loopback_id is not None:
                try:
                    self._open_loopback_stream(loopback_id)
                except Exception as e:
                    print(f"Ошибка запуска loopback #{loopback_id}: {e}. Пробуем WASAPI loopback по умолчанию...")
                    try:
                        default_loopback = self.p.get_default_wasapi_loopback()
                        default_id = default_loopback["index"]
                        if default_id != loopback_id:
                            self._open_loopback_stream(default_id)
                        else:
                            self.loopback_stream = None
                    except Exception as e_default:
                        print(f"Ошибка запуска WASAPI loopback по умолчанию: {e_default}")
                        self.loopback_stream = None
                    
            self.is_recording = True

    def stop(self):
        with self.lock:
            if not self.is_recording:
                return
                
            if self.mic_stream:
                try:
                    self.mic_stream.stop_stream()
                    self.mic_stream.close()
                except Exception:
                    pass
                self.mic_stream = None
                
            if self.loopback_stream:
                try:
                    self.loopback_stream.stop_stream()
                    self.loopback_stream.close()
                except Exception:
                    pass
                self.loopback_stream = None
                
            self.is_recording = False
            print("Аудиозахват остановлен.")

    def get_audio_chunks(self):
        """Возвращает накопленные порции аудио из очередей"""
        mic_chunks = []
        while not self.mic_queue.empty():
            mic_chunks.append(self.mic_queue.get())
            
        loopback_chunks = []
        while not self.loopback_queue.empty():
            loopback_chunks.append(self.loopback_queue.get())
            
        mic_audio = np.concatenate(mic_chunks) if mic_chunks else np.array([], dtype=np.float32)
        loopback_audio = np.concatenate(loopback_chunks) if loopback_chunks else np.array([], dtype=np.float32)
        
        return mic_audio, loopback_audio

    def terminate(self):
        self.stop()
        self.p.terminate()
