import threading
import time
import numpy as np
from faster_whisper import WhisperModel
from .config import load_settings

class Transcriber:
    def __init__(self, on_transcript_update_cb=None, on_ready_cb=None):
        """
        on_transcript_update_cb: callback функция, принимающая (speaker, text, is_final)
        """
        self.settings = load_settings()
        self.on_transcript_update = on_transcript_update_cb
        self.on_ready = on_ready_cb
        
        self.model = None
        self.model_lock = threading.Lock()
        
        self.is_running = False
        self.threads = []
        
        # Параметры VAD и накопления буфера
        self.silence_threshold = 0.015  # Порог тишины (RMS)
        self.silence_duration = 1.6     # Секунд тишины для закрытия фразы (увеличено для цельности фраз)
        self.sample_rate = 16000
        
    def load_model(self):
        with self.model_lock:
            if self.model is None:
                # Если в настройках явно нет whisper_model, используем "base" для хорошего русского языка
                whisper_model = self.settings.get("whisper_model", "base")
                print(f"Загрузка локальной модели Whisper '{whisper_model}'...")
                self.model = WhisperModel(whisper_model, device="cpu", compute_type="int8")
                print("Модель Whisper успешно загружена!")
                if self.on_ready:
                    self.on_ready()

    def start(self, audio_capture):
        if self.is_running:
            return
            
        self.is_running = True
        self.audio_capture = audio_capture
        
        # Ленивая загрузка Whisper в отдельном потоке, чтобы не блокировать интерфейс
        threading.Thread(target=self.load_model, daemon=True).start()
        
        # Запускаем потоки обработки для микрофона и loopback отдельно
        t1 = threading.Thread(target=self._process_stream, args=("user", audio_capture.mic_queue), daemon=True)
        t2 = threading.Thread(target=self._process_stream, args=("colleague", audio_capture.loopback_queue), daemon=True)
        
        self.threads = [t1, t2]
        for t in self.threads:
            t.start()
        print("Потоки транскрибации запущены.")

    def stop(self):
        self.is_running = False
        for t in self.threads:
            if t.is_alive():
                t.join(timeout=1.0)
        self.threads = []
        print("Потоки транскрибации остановлены.")

    def _process_stream(self, speaker, audio_queue):
        """
        Обработка аудиопотока из очереди.
        speaker: 'user' или 'colleague'
        """
        buffer = []
        silence_start = None
        last_transcript_time = time.time()
        
        while self.is_running:
            chunks = []
            try:
                # Блокируемся на первом чанке для экономии CPU
                first_chunk = audio_queue.get(timeout=0.2)
                chunks.append(first_chunk)
                
                # Быстро выгребаем все остальные накопившиеся чанки
                while not audio_queue.empty():
                    try:
                        chunk = audio_queue.get_nowait()
                        chunks.append(chunk)
                    except Exception:
                        break
            except Exception:
                pass # Таймаут ожидания данных
                
            if chunks:
                audio_chunk = np.concatenate(chunks)
                buffer.append(audio_chunk)
                
                # Вычисляем громкость (RMS)
                rms = np.sqrt(np.mean(audio_chunk**2)) if len(audio_chunk) > 0 else 0
                
                if rms < self.silence_threshold:
                    if silence_start is None:
                        silence_start = time.time()
                else:
                    silence_start = None
                    
                # Если накопилось достаточно звука, делаем промежуточную (draft) транскрибацию
                current_audio = np.concatenate(buffer)
                duration = len(current_audio) / self.sample_rate
                
                # Транскрибируем раз в 2 секунды, если идет речь (промежуточный драфт с beam_size=1 для скорости)
                if duration > 1.5 and (time.time() - last_transcript_time > 1.5) and self.model is not None:
                    text = self._transcribe_audio(current_audio, beam_size=1)
                    if text and self.on_transcript_update:
                        self.on_transcript_update(speaker, text, is_final=False)
                    last_transcript_time = time.time()
                
                # Если зафиксирована тишина дольше silence_duration, закрываем фразу (финал с beam_size=3 для точности)
                if silence_start and (time.time() - silence_start > self.silence_duration):
                    if len(current_audio) > self.sample_rate * 0.5: # Игнорируем фразы короче 0.5 сек
                        if self.model is not None:
                            text = self._transcribe_audio(current_audio, beam_size=3)
                            if text and self.on_transcript_update:
                                self.on_transcript_update(speaker, text, is_final=True)
                        buffer = []
                    silence_start = None
            else:
                # Если очередь пуста и тишина длится долго, тоже закрываем фразу (финал с beam_size=3)
                if silence_start and (time.time() - silence_start > self.silence_duration) and buffer:
                    current_audio = np.concatenate(buffer)
                    if len(current_audio) > self.sample_rate * 0.5 and self.model is not None:
                        text = self._transcribe_audio(current_audio, beam_size=3)
                        if text and self.on_transcript_update:
                            self.on_transcript_update(speaker, text, is_final=True)
                    buffer = []
                    silence_start = None
                    
            time.sleep(0.05)

    def _transcribe_audio(self, audio_data, beam_size=1):
        """Вызов локального Whisper для распознавания текста"""
        with self.model_lock:
            if self.model is None:
                return ""
            try:
                # Начальный промпт для подсказки словаря Whisper
                initial_prompt = (
                    "Вендинговые аппараты, выручка, ремонт, техническое обслуживание, "
                    "инженеры, курьеры, Ижевск, Киров, Рязань, Сургут, Омск, Ульяновск, "
                    "Чебоксары, Магнитогорск, Орёл, задачи курьеров, статус аппаратов."
                )
                # beam_size=1 для скорости (черновики), beam_size=3 для точности (финальный текст)
                segments, info = self.model.transcribe(
                    audio_data, 
                    beam_size=beam_size, 
                    language="ru", 
                    initial_prompt=initial_prompt,
                    vad_filter=True, 
                    vad_parameters=dict(min_speech_duration_ms=250)
                )
                
                text_segments = [seg.text for seg in segments]
                text = " ".join(text_segments).strip()
                return text
            except Exception as e:
                print(f"Ошибка транскрибации Whisper: {e}")
                return ""
