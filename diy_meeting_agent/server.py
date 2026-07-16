import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from .config import load_settings, save_settings, HISTORY_DIR
from .audio_capture import AudioCapture
from .transcriber import Transcriber
from .tts_engine import TTSEngine
from .agent_logic import MeetingAgent

# Инициализация FastAPI приложения
app = FastAPI(title="DIY Meeting Agent")

# Настройка путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")

# Создаем папки static и templates, если их нет
os.makedirs(os.path.join(static_dir, "css"), exist_ok=True)
os.makedirs(os.path.join(static_dir, "js"), exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

# Монтируем статику и шаблоны
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# Глобальные инстансы компонентов
audio_capture = AudioCapture()

def set_speaking_status(is_speaking):
    audio_capture.is_speaking = is_speaking

tts_engine = TTSEngine(on_speak_status_cb=set_speaking_status)
meeting_agent = MeetingAgent()

# Класс для отслеживания WebSocket клиентов
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()
transcriber = None
meeting_task = None
is_meeting_active = False

main_loop = None

@app.on_event("startup")
async def startup_event():
    global main_loop
    main_loop = asyncio.get_running_loop()
    print("FastAPI event loop captured.")

def on_whisper_ready():
    """Callback от Whisper о готовности модели"""
    global main_loop
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": "whisper_ready"}), 
            main_loop
        )
    else:
        print("Whisper ready, but loop is not initialized yet.")

def on_transcript_update(speaker, text, is_final):
    """Callback от Whisper о распознавании речи"""
    global main_loop
    if main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            handle_transcript_event(speaker, text, is_final), 
            main_loop
        )
    else:
        # Резервный вариант, если loop еще не сохранен
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(
                handle_transcript_event(speaker, text, is_final), 
                loop
            )
        except Exception as e:
            print(f"Ошибка доставки транскрипта: loop не готов. {e}")

async def handle_transcript_event(speaker, text, is_final):
    if is_final:
        # Добавляем в историю встречи
        replica = meeting_agent.add_replica(speaker, text)
        print(f"[FINAL] {speaker}: {text}")
        
        # Отправляем клиентам
        await manager.broadcast({
            "type": "replica",
            "speaker": speaker,
            "text": text,
            "timestamp": replica["timestamp"]
        })
        
        # Проверяем на триггер (wake-word)
        # Реагируем, только если говорит коллега и сработало ключевое слово
        if meeting_agent.check_trigger(text):
            print(f"Trigger matched on: '{text}'! Requesting Ollama...")
            await manager.broadcast({
                "type": "status",
                "message": "Обнаружено обращение! Генерирую ответ..."
            })
            
            # Асинхронно генерируем ответ
            response_text = await meeting_agent.generate_response(text)
            
            # Отправляем ответ клиенту
            await manager.broadcast({
                "type": "suggestion",
                "question": text,
                "answer": response_text
            })
            
            # Если включен авто-ответ (опционально), озвучиваем его
            settings = load_settings()
            if settings.get("auto_answer_enabled", False):
                tts_engine.say(response_text)
                await manager.broadcast({
                    "type": "status",
                    "message": f"Озвучиваю ответ: {response_text}"
                })
    else:
        # Промежуточный транскрипт (черновик)
        await manager.broadcast({
            "type": "draft",
            "speaker": speaker,
            "text": text
        })

# Модели Pydantic для API
class SettingsModel(BaseModel):
    mic_device_id: int = None
    loopback_device_id: int = None
    wake_word: str = "вопрос к залу"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    tts_voice: str = "ru-RU-DmitryNeural"
    auto_answer_enabled: bool = False
    whisper_model: str = "base"

class SayRequest(BaseModel):
    text: str

class ChatMessageModel(BaseModel):
    role: str # user или assistant
    content: str

class ChatRequestModel(BaseModel):
    message: str
    history: list[ChatMessageModel] = []

# Эндпоинты
@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/devices")
def get_devices():
    try:
        return audio_capture.get_devices()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения аудиоустройств: {e}")

@app.get("/api/settings")
def get_current_settings():
    settings = load_settings()
    settings["rag_chunks_count"] = len(meeting_agent.rag.chunks)
    sources = set(chunk.get("source") for chunk in meeting_agent.rag.chunks if chunk.get("source"))
    settings["rag_files_count"] = len(sources)
    return settings

@app.post("/api/settings")
def update_settings(new_settings: SettingsModel):
    current = load_settings()
    for k, v in new_settings.dict().items():
        current[k] = v
    if save_settings(current):
        return {"status": "success", "message": "Настройки сохранены"}
    raise HTTPException(status_code=500, detail="Не удалось сохранить настройки")

@app.post("/api/start")
async def start_meeting():
    global transcriber, is_meeting_active
    if is_meeting_active:
        return {"status": "error", "message": "Встреча уже идет"}
        
    try:
        meeting_agent.start_meeting()
        audio_capture.start()
        
        # Создаем и запускаем транскрибер
        transcriber = Transcriber(on_transcript_update_cb=on_transcript_update, on_ready_cb=on_whisper_ready)
        transcriber.start(audio_capture)
        
        # Запускаем фоновую индексацию базы знаний при старте встречи
        asyncio.create_task(meeting_agent.rag.index_documents())
        
        is_meeting_active = True
        await manager.broadcast({"type": "meeting_status", "active": True})
        return {"status": "success", "message": "Запись встречи начата"}
    except Exception as e:
        is_meeting_active = False
        raise HTTPException(status_code=500, detail=f"Ошибка старта встречи: {e}")

@app.post("/api/knowledge/index")
async def index_knowledge():
    try:
        res = await meeting_agent.rag.index_documents()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка индексации: {e}")

@app.post("/api/chat")
async def chat_with_ai(request: ChatRequestModel):
    try:
        history_list = [{"role": msg.role, "content": msg.content} for msg in request.history]
        response_text = await meeting_agent.generate_chat_response(request.message, history_list)
        return {"status": "success", "response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чата: {e}")
        

@app.post("/api/stop")
async def stop_meeting():
    global transcriber, is_meeting_active
    if not is_meeting_active:
        return {"status": "error", "message": "Нет активной встречи"}
        
    try:
        if transcriber:
            transcriber.stop()
            transcriber = None
            
        audio_capture.stop()
        is_meeting_active = False
        
        # Информируем о начале генерации саммари
        await manager.broadcast({"type": "status", "message": "Генерирую краткие итоги созвона..."})
        
        # Генерируем саммари через Ollama
        summary_text = await meeting_agent.generate_summary()
        
        # Сохраняем протокол локально
        saved_file = meeting_agent.save_meeting(summary_text)
        
        await manager.broadcast({"type": "meeting_status", "active": False})
        
        return {
            "status": "success", 
            "message": "Встреча завершена", 
            "summary": summary_text,
            "saved_file": saved_file,
            "transcript_count": len(meeting_agent.history)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка остановки встречи: {e}")

@app.post("/api/tts")
def speak_text(req: SayRequest):
    try:
        tts_engine.say(req.text)
        return {"status": "success", "message": "Текст передан на озвучку"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка воспроизведения TTS: {e}")

@app.get("/api/history")
def list_history():
    files = []
    if os.path.exists(HISTORY_DIR):
        for f in os.listdir(HISTORY_DIR):
            if f.endswith(".json") and f.startswith("meeting_"):
                files.append(f.replace("meeting_", "").replace(".json", ""))
    return sorted(files, reverse=True)

@app.get("/api/history/{meeting_id}")
def get_history_detail(meeting_id: str):
    json_path = os.path.join(HISTORY_DIR, f"meeting_{meeting_id}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Встреча не найдена")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения данных встречи: {e}")

# WebSocket соединение
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Отправляем текущее состояние клиенту при подключении
        await websocket.send_json({
            "type": "init",
            "is_active": is_meeting_active,
            "history": meeting_agent.history
        })
        while True:
            # Просто удерживаем соединение
            data = await websocket.receive_text()
            # Можно обрабатывать сообщения от фронтенда при необходимости
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    # Запуск сервера
    uvicorn.run(app, host="127.0.0.1", port=8000)
