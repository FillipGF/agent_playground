import os
import json
import httpx
import time
from datetime import datetime
from .config import load_settings, HISTORY_DIR
from .rag_engine import RagEngine

class MeetingAgent:
    def __init__(self):
        self.settings = load_settings()
        self.history = []
        self.meeting_id = None
        self.meeting_start_time = None
        self.rag = RagEngine()
        
    def start_meeting(self):
        """Инициализирует новую встречу"""
        self.settings = load_settings()
        self.history = []
        self.meeting_start_time = datetime.now()
        self.meeting_id = self.meeting_start_time.strftime("%Y%m%d_%H%M%S")
        print(f"Начата новая встреча: {self.meeting_id}")
        
    def add_replica(self, speaker, text):
        """
        Добавляет реплику в историю.
        speaker: 'user' (вы) или 'colleague' (коллеги)
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        replica = {
            "speaker": speaker,
            "text": text,
            "timestamp": timestamp
        }
        self.history.append(replica)
        return replica

    def check_trigger(self, text):
        """Проверяет, содержит ли текст wake word"""
        wake_word = self.settings.get("wake_word", "вопрос к залу").lower()
        return wake_word in text.lower()

    def get_context_string(self, limit=15):
        """Формирует текстовый контекст из последних реплик"""
        recent_replicas = self.history[-limit:]
        context_lines = []
        for r in recent_replicas:
            speaker_label = "Вы" if r["speaker"] == "user" else "Коллега"
            context_lines.append(f"[{r['timestamp']}] {speaker_label}: {r['text']}")
        return "\n".join(context_lines)

    async def _rewrite_query(self, message, history_context_str, ollama_url, model):
        """Переписывает запрос пользователя с учетом контекста беседы для RAG-поиска"""
        if not history_context_str.strip():
            return message
            
        prompt = (
            "Ты — вспомогательный модуль для RAG-системы.\n"
            "Твоя задача: переписать текущий вопрос пользователя в ОДИН поисковый запрос на русском языке, "
            "объединив вопрос с контекстом предыдущей беседы.\n"
            "Правила:\n"
            "1. Обязательно перенеси из контекста конкретные детали: ID аппаратов (например, 40734), названия городов (например, Киров) и отчетный период (например, май или июнь).\n"
            "2. Раскрой местоимения ('он', 'него', 'там', 'этом') — замени их на конкретные названия или ID из контекста.\n"
            "3. Твой ответ должен быть ОДНИМ лаконичным поисковым предложением (например: 'выручка аппарата 40734 в Кирове за июнь').\n"
            "4. Не отвечай на вопрос, не пиши никаких вступлений, пояснений или кавычек.\n\n"
            f"Контекст беседы:\n{history_context_str}\n\n"
            f"Вопрос пользователя: {message}\n"
            "Переписанный запрос: "
        )
        
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                response = await client.post(
                    f"{ollama_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.0,
                            "num_predict": 40
                        }
                    }
                )
                if response.status_code == 200:
                    rewritten = response.json().get("response", "").strip()
                    rewritten = rewritten.strip('"').strip("'").strip()
                    if rewritten:
                        print(f"[Query Rewrite] '{message}' -> '{rewritten}'")
                        return rewritten
        except Exception as e:
            print(f"Ошибка при переписывании запроса: {e}")
            
        return message

    async def generate_response(self, question):
        """Запрашивает у локальной Ollama ответ на основе контекста встречи"""
        self.settings = load_settings()
        ollama_url = self.settings.get("ollama_url", "http://localhost:11434")
        model = self.settings.get("ollama_model", "qwen2.5:7b")
        
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        context = self.get_context_string(limit=10)
        
        # Ищем информацию в базе знаний RAG
        rag_context = ""
        try:
            search_query = await self._rewrite_query(question, context, ollama_url, model)
            rag_results = await self.rag.search(search_query, top_k=5)
            if rag_results:
                rag_lines = []
                for res in rag_results:
                    rag_lines.append(f"- {res['text']} (Источник: {res['source']})")
                rag_context = "\n".join(rag_lines)
                print(f"RAG нашел {len(rag_results)} совпадений для вопроса.")
        except Exception as e:
            print(f"Ошибка поиска в базе знаний RAG: {e}")
            
        system_prompt = (
            "Ты — AI-ассистент, помогающий отвечать на вопросы во время рабочего созвона.\n"
            f"Текущие системные дата и время на твоем компьютере: {now_str}.\n"
        )
        
        if rag_context:
            system_prompt += (
                "Ниже приведены проверенные факты из отчетов компании (базы знаний), которые ты обязан использовать для ответа:\n"
                f"{rag_context}\n\n"
            )
            
        system_prompt += (
            "Вот контекст текущего созвона (последние реплики):\n"
            f"{context}\n\n"
            "Коллега задал вопрос/обратился к тебе: "
            f"\"{question}\"\n\n"
            "Сгенерируй короткий, емкий и профессиональный ответ от первого лица (я, мой, мне). "
            "Ответ должен быть очень кратким (1-3 предложения), так как он будет озвучен голосом. "
            "ПРАВИЛА ИСПОЛЬЗОВАНИЯ ДАННЫХ:\n"
            "1. Обращай внимание на месяц и год в фактах RAG (например, 'за май 2026 года' или 'за июнь 2026 года'). Отвечай именно за тот месяц, о котором спрашивают. Если месяц не указан в вопросе, отвечай по самым свежим доступным данным, явно называя месяц.\n"
            "2. Отдавай абсолютный приоритет цифрам из базы знаний. Никогда не выдумывай показатели и суммы из головы!\n"
            "3. Ссылайся на отчетный период и город, если это уместно (например, 'В июне по Чебоксарам...').\n"
            "4. Если в фактах нет нужной информации (например, нет данных за конкретный месяц или для этого аппарата), скажи: 'В моих отчетах нет точной информации по этому вопросу, мне нужно уточнить этот момент' или что вернешься с ответом позже."
        )
        
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=30.0) as client:
                response = await client.post(
                    f"{ollama_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": system_prompt,
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "").strip()
                else:
                    return f"Ошибка Ollama (код {response.status_code})"
        except Exception as e:
            return f"Не удалось подключиться к Ollama на {ollama_url}. Убедитесь, что Ollama запущена. Ошибка: {e}"

    async def generate_summary(self):
        """Генерирует саммари (минутки) всей встречи"""
        if not self.history:
            return "История встречи пуста. Нечего суммировать."
            
        self.settings = load_settings()
        ollama_url = self.settings.get("ollama_url", "http://localhost:11434")
        model = self.settings.get("ollama_model", "qwen2.5:7b")
        
        # Собираем весь транскрипт
        transcript_lines = []
        for r in self.history:
            speaker_label = "Вы" if r["speaker"] == "user" else "Коллеги"
            transcript_lines.append(f"[{r['timestamp']}] {speaker_label}: {r['text']}")
        full_transcript = "\n".join(transcript_lines)
        
        prompt = (
            "Ты — профессиональный секретарь и аналитик встреч.\n"
            "Перед тобой полный транскрипт рабочего созвона:\n"
            f"{full_transcript}\n\n"
            "Сделай краткий и структурированный протокол встречи (Meeting Minutes) на русском языке:\n"
            "1. Тема и суть обсуждения (1-2 предложения).\n"
            "2. Ключевые принятые решения (списком).\n"
            "3. Action Items (задачи) — что нужно сделать и кто исполнитель (если упоминался).\n"
            "Будь конкретен, пиши по делу, без общих фраз."
        )
        
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
                response = await client.post(
                    f"{ollama_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "").strip()
                else:
                    return f"Ошибка Ollama при генерации саммари (код {response.status_code})"
        except Exception as e:
            return f"Не удалось подключиться к Ollama для генерации саммари. Ошибка: {e}"

    async def generate_chat_response(self, message, chat_history):
        """Отвечает на сообщение в чате, используя RAG контекст и историю диалога"""
        self.settings = load_settings()
        ollama_url = self.settings.get("ollama_url", "http://localhost:11434")
        model = self.settings.get("ollama_model", "qwen2.5:7b")
        
        # 1. Поиск в RAG
        rag_context = ""
        try:
            history_context_str = ""
            for msg in chat_history[-3:]:
                role_str = "Пользователь" if msg.get("role") == "user" else "ИИ"
                history_context_str += f"{role_str}: {msg.get('content')}\n"
                
            search_query = await self._rewrite_query(message, history_context_str, ollama_url, model)
            rag_results = await self.rag.search(search_query, top_k=5)
            if rag_results:
                rag_lines = []
                for res in rag_results:
                    rag_lines.append(f"- {res['text']} (Источник: {res['source']})")
                rag_context = "\n".join(rag_lines)
                print(f"RAG чата нашел {len(rag_results)} совпадений.")
        except Exception as e:
            print(f"Ошибка RAG в чате: {e}")
            
        # 2. Формируем системный промпт
        system_prompt = (
            "Ты — умный AI-ассистент, помогающий пользователю. Ты имеешь доступ к локальной базе "
            "знаний, отчетам по выручке (за разные месяцы), аппаратам и PDF-инструкциям компании.\n"
            "Отвечай вежливо, емко и профессионально на русском языке.\n"
            "ПРАВИЛА ИСПОЛЬЗОВАНИЯ ДАННЫХ:\n"
            "1. При ответе строго опирайся на проверенные факты из базы знаний (отчетов). Обращай внимание на месяц и год в предоставленных фактах (например, 'за май 2026 года', 'за июнь 2026 года') и отвечай строго за тот месяц, о котором спрашивает пользователь. Если период не указан, давай ответ по последним имеющимся данным, явно упоминая период.\n"
            "2. Тебе строго запрещено выдумывать, экстраполировать или прогнозировать финансовые показатели, если они не указаны в фактах базы знаний.\n"
            "3. Всегда отвечай от первого лица (я, мой, мне).\n"
            "4. Если в предоставленных отчетах нет точной информации по нужному аппарату, городу или месяцу, прямо скажи: 'В моих загруженных отчетах нет точной информации по этому вопросу' и вежливо подскажи на основе общих знаний, если можешь."
        )
        
        if rag_context:
            system_prompt += (
                "Проверенные факты из базы знаний (отчетов):\n"
                f"{rag_context}\n\n"
            )
            
        # 3. Собираем историю сообщений для Ollama API /api/chat
        messages = []
        messages.append({"role": "system", "content": system_prompt})
        
        for msg in chat_history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
            
        # Добавляем текущий вопрос пользователя
        messages.append({"role": "user", "content": message})
        
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=60.0) as client:
                response = await client.post(
                    f"{ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("message", {}).get("content", "").strip()
                else:
                    return f"Ошибка Ollama (код {response.status_code})"
        except Exception as e:
            return f"Не удалось подключиться к Ollama для ответа в чате. Ошибка: {e}"
            

    def save_meeting(self, summary_text=""):
        """Сохраняет транскрипт и саммари встречи в файлы"""
        if not self.meeting_id:
            return None
            
        meeting_data = {
            "meeting_id": self.meeting_id,
            "start_time": self.meeting_start_time.isoformat() if self.meeting_start_time else "",
            "end_time": datetime.now().isoformat(),
            "history": self.history,
            "summary": summary_text
        }
        
        # Сохраняем в JSON
        json_path = os.path.join(HISTORY_DIR, f"meeting_{self.meeting_id}.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(meeting_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Ошибка сохранения JSON встречи: {e}")
            
        # Сохраняем читаемый текстовый отчет
        txt_path = os.path.join(HISTORY_DIR, f"meeting_{self.meeting_id}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"=== ПРОТОКОЛ ВСТРЕЧИ {self.meeting_id} ===\n")
                f.write(f"Дата начала: {self.meeting_start_time.strftime('%Y-%m-%d %H:%M:%S') if self.meeting_start_time else ''}\n")
                f.write(f"Дата окончания: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                if summary_text:
                    f.write("=== КРАТКИЕ ИТОГИ (SUMMAARY) ===\n")
                    f.write(summary_text)
                    f.write("\n\n")
                    
                f.write("=== ТРАНСКРИПТ СОЗВОНА ===\n")
                for r in self.history:
                    speaker_label = "Вы" if r["speaker"] == "user" else "Коллега"
                    f.write(f"[{r['timestamp']}] {speaker_label}: {r['text']}\n")
        except Exception as e:
            print(f"Ошибка сохранения текстового отчета встречи: {e}")
            
        return json_path
