let ws = null;
let currentSettings = {};
let activeHistoryMeetingId = null;

// Инициализация при загрузке страницы
document.addEventListener("DOMContentLoaded", async () => {
    // Инициализируем иконки Lucide
    lucide.createIcons();
    
    // Загружаем настройки, затем список аудиоустройств и историю
    try {
        await loadSettings();
        await loadDevices();
        await loadHistory();
    } catch (err) {
        console.error("Ошибка инициализации приложения:", err);
    }
    
    // Подключаемся к WebSocket
    connectWebSocket();
});

// Переключение вкладок (Tabs)
function switchTab(tabId) {
    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.remove("active");
    });
    document.querySelectorAll(".nav-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    
    document.getElementById(`tab-${tabId}`).classList.add("active");
    document.getElementById(`tab-btn-${tabId}`).classList.add("active");
    
    if (tabId === 'history') {
        loadHistory();
    }
}

// WebSocket соединение
function connectWebSocket() {
    const loc = window.location;
    const wsUrl = (loc.protocol === "https:" ? "wss:" : "ws:") + "//" + loc.host + "/ws";
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
        updateSystemStatus("active", "Подключено к серверу");
    };
    
    ws.onclose = () => {
        updateSystemStatus("error", "Соединение потеряно. Переподключение...");
        setTimeout(connectWebSocket, 3000);
    };
    
    ws.onerror = (err) => {
        console.error("WS error: ", err);
    };
    
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleWSMessage(msg);
    };
}

// Обработка сообщений из WebSocket
function handleWSMessage(msg) {
    switch (msg.type) {
        case "init":
            // Восстановление состояния при перезагрузке страницы
            toggleMeetingUI(msg.is_active);
            if (msg.history && msg.history.length > 0) {
                const container = document.getElementById("transcript-container");
                container.innerHTML = ""; // очищаем empty state
                msg.history.forEach(replica => {
                    appendSpeechBubble(replica.speaker, replica.text, replica.timestamp, true);
                });
                scrollToBottom(container);
            }
            break;
        case "whisper_ready":
            // Убираем спиннер загрузки Whisper и показываем состояние "Слушаю..."
            const container = document.getElementById("transcript-container");
            if (container.querySelector(".empty-state")) {
                container.innerHTML = `
                    <div class="empty-state">
                        <i data-lucide="mic"></i>
                        <p>Слушаю встречу... Говорите или воспроизведите звук.</p>
                    </div>
                `;
                lucide.createIcons();
            }
            updateSystemStatus("active", "Слушаю созвон...");
            break;
            
        case "replica":
            // Удаляем черновик (draft) перед добавлением финальной реплики
            removeDraftBubble(msg.speaker);
            appendSpeechBubble(msg.speaker, msg.text, msg.timestamp, true);
            break;
            
        case "draft":
            // Промежуточное распознавание
            updateDraftBubble(msg.speaker, msg.text);
            break;
            
        case "suggestion":
            // Получена подсказка с ответом
            showSuggestion(msg.question, msg.answer);
            break;
            
        case "status":
            // Системный статус/уведомление
            updateSystemStatus("active", msg.message);
            break;
            
        case "meeting_status":
            toggleMeetingUI(msg.active);
            break;
    }
}

// Обновление панели системного статуса в сайдбаре
function updateSystemStatus(state, message) {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    
    dot.className = "status-indicator";
    if (state === "active") {
        dot.classList.add("active");
    } else if (state === "error") {
        dot.classList.add("error");
    }
    
    text.textContent = message;
}

// Добавление текстового пузыря (Speech Bubble) в транскрипт
function appendSpeechBubble(speaker, text, timestamp, isFinal) {
    const container = document.getElementById("transcript-container");
    
    // Если это первый элемент, удаляем empty state
    const emptyState = container.querySelector(".empty-state");
    if (emptyState) {
        container.innerHTML = "";
    }
    
    const bubble = document.createElement("div");
    bubble.className = `speech-bubble ${speaker}`;
    if (!isFinal) {
        bubble.classList.add("draft");
        bubble.id = `draft-${speaker}`;
    }
    
    const meta = document.createElement("div");
    meta.className = "bubble-meta";
    const nameSpan = document.createElement("span");
    nameSpan.textContent = speaker === "user" ? "Вы" : "Коллега";
    const timeSpan = document.createElement("span");
    timeSpan.textContent = timestamp || new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
    
    meta.appendChild(nameSpan);
    meta.appendChild(timeSpan);
    
    const body = document.createElement("div");
    body.className = "bubble-text";
    body.textContent = text;
    
    bubble.appendChild(meta);
    bubble.appendChild(body);
    container.appendChild(bubble);
    
    scrollToBottom(container);
}

// Обновление промежуточного драфта речи
function updateDraftBubble(speaker, text) {
    const draftId = `draft-${speaker}`;
    let draftElement = document.getElementById(draftId);
    
    if (!draftElement) {
        appendSpeechBubble(speaker, text, null, false);
    } else {
        draftElement.querySelector(".bubble-text").textContent = text;
        const container = document.getElementById("transcript-container");
        scrollToBottom(container);
    }
}

// Удаление драфта речи после финализации реплики
function removeDraftBubble(speaker) {
    const draftElement = document.getElementById(`draft-${speaker}`);
    if (draftElement) {
        draftElement.remove();
    }
}

function scrollToBottom(element) {
    element.scrollTop = element.scrollHeight;
}

// Переключение UI (кнопки старта/стопа и индикатор)
function toggleMeetingUI(isActive) {
    const btnStart = document.getElementById("btn-start");
    const btnStop = document.getElementById("btn-stop");
    const badge = document.getElementById("recording-badge");
    
    if (isActive) {
        btnStart.style.display = "none";
        btnStop.style.display = "inline-flex";
        badge.style.display = "flex";
        updateSystemStatus("active", "Идет запись созвона...");
    } else {
        btnStart.style.display = "inline-flex";
        btnStop.style.display = "none";
        badge.style.display = "none";
        updateSystemStatus("active", "Готов к работе");
    }
}

// API: Старт созвона
async function startMeeting() {
    try {
        const res = await fetch("/api/start", { method: "POST" });
        const data = await res.json();
        if (data.status === "success") {
            // Очищаем итоги созвона с предыдущей встречи
            document.getElementById("summary-container").innerHTML = `
                <div class="empty-state">
                    <i data-lucide="file-text"></i>
                    <p>Итоги созвона будут сгенерированы автоматически при нажатии «Завершить встречу».</p>
                </div>
            `;
            // Очищаем и скрываем подсказки
            clearSuggestion();
            
            document.getElementById("transcript-container").innerHTML = `
                <div class="empty-state">
                    <i data-lucide="loader" class="animate-spin"></i>
                    <p>Аудиозахват инициализирован. Запуск Whisper...</p>
                </div>
            `;
            lucide.createIcons();
            toggleMeetingUI(true);
        } else {
            alert("Ошибка: " + data.message);
        }
    } catch (err) {
        console.error(err);
        alert("Не удалось запустить встречу. Проверьте соединение с сервером.");
    }
}

// API: Стоп созвона
async function stopMeeting() {
    updateSystemStatus("active", "Завершение созвона...");
    try {
        const res = await fetch("/api/stop", { method: "POST" });
        const data = await res.json();
        
        if (data.status === "success") {
            toggleMeetingUI(false);
            
            // Выводим Саммари
            const summaryContainer = document.getElementById("summary-container");
            summaryContainer.innerHTML = formatMarkdown(data.summary);
            
            updateSystemStatus("active", `Встреча завершена. Протокол сохранен.`);
        } else {
            alert("Ошибка: " + data.message);
        }
    } catch (err) {
        console.error(err);
        alert("Ошибка при остановке встречи.");
    }
}

// Рендеринг ИИ подсказки
function showSuggestion(question, answer) {
    const card = document.getElementById("suggestion-card");
    const empty = document.getElementById("suggestion-empty");
    const active = document.getElementById("suggestion-active");
    
    card.classList.add("active");
    empty.style.display = "none";
    active.style.display = "flex";
    
    document.getElementById("txt-detected-question").textContent = `"${question}"`;
    document.getElementById("txt-generated-answer").value = answer;
}

// Отклонить подсказку
function clearSuggestion() {
    const card = document.getElementById("suggestion-card");
    const empty = document.getElementById("suggestion-empty");
    const active = document.getElementById("suggestion-active");
    
    card.classList.remove("active");
    empty.style.display = "block";
    active.style.display = "none";
}

// API: Воспроизвести подсказку голосом
async function speakSuggestion() {
    const text = document.getElementById("txt-generated-answer").value;
    if (!text.strip) {
        // Простой хелпер trim
        if (!text.trim()) return;
    }
    
    updateSystemStatus("active", "Озвучиваю ответ...");
    try {
        const res = await fetch("/api/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
        });
        const data = await res.json();
        if (data.status === "success") {
            updateSystemStatus("active", "Ответ озвучен успешно");
        } else {
            alert("Ошибка озвучки: " + data.message);
        }
    } catch (err) {
        console.error(err);
    }
}

// API: Загрузка списка устройств
async function loadDevices() {
    try {
        const res = await fetch("/api/devices");
        const devices = await res.json();
        
        const selectMic = document.getElementById("select-mic");
        const selectLoopback = document.getElementById("select-loopback");
        
        selectMic.innerHTML = "";
        selectLoopback.innerHTML = "";
        
        // Микрофоны
        if (devices.mics.length === 0) {
            selectMic.innerHTML = '<option value="null">Микрофоны не найдены</option>';
        } else {
            devices.mics.forEach(dev => {
                const opt = document.createElement("option");
                opt.value = dev.id;
                opt.textContent = `${dev.name} (${dev.rate} Гц)`;
                if (dev.id === currentSettings.mic_device_id) {
                    opt.selected = true;
                }
                selectMic.appendChild(opt);
            });
        }
        
        // Системный захват
        if (devices.loopbacks.length === 0) {
            selectLoopback.innerHTML = '<option value="null">WASAPI Loopback не найден</option>';
        } else {
            devices.loopbacks.forEach(dev => {
                const opt = document.createElement("option");
                opt.value = dev.id;
                opt.textContent = `${dev.name} (Loopback, ${dev.rate} Гц)`;
                if (dev.id === currentSettings.loopback_device_id) {
                    opt.selected = true;
                }
                selectLoopback.appendChild(opt);
            });
        }
    } catch (err) {
        console.error("Failed to load audio devices:", err);
    }
}

// API: Загрузка текущих настроек
async function loadSettings() {
    try {
        const res = await fetch("/api/settings");
        currentSettings = await res.json();
        
        // Заполняем форму
        document.getElementById("txt-wake-word").value = currentSettings.wake_word;
        document.getElementById("lbl-wake-word").textContent = `"${currentSettings.wake_word}"`;
        document.getElementById("chk-auto-answer").checked = currentSettings.auto_answer_enabled || false;
        document.getElementById("txt-ollama-url").value = currentSettings.ollama_url;
        document.getElementById("txt-ollama-model").value = currentSettings.ollama_model;
        
        const whisperSelect = document.getElementById("select-whisper-model");
        if (whisperSelect) {
            whisperSelect.value = currentSettings.whisper_model || "base";
        }
        
        const voiceSelect = document.getElementById("select-tts-voice");
        for (let i = 0; i < voiceSelect.options.length; i++) {
            if (voiceSelect.options[i].value === currentSettings.tts_voice) {
                voiceSelect.selectedIndex = i;
                break;
            }
        }
        // Отображение статуса базы знаний RAG
        updateRagStatus(currentSettings.rag_chunks_count, currentSettings.rag_files_count);
    } catch (err) {
        console.error("Failed to load settings:", err);
    }
}

// Отображение статуса RAG базы знаний
function updateRagStatus(chunksCount, filesCount) {
    const statusText = document.getElementById("rag-status-text");
    if (statusText) {
        if (chunksCount && chunksCount > 0) {
            statusText.innerHTML = `База знаний активна: проиндексировано <b>${filesCount}</b> файлов (<b>${chunksCount}</b> абзацев).`;
        } else {
            statusText.textContent = "База знаний не проиндексирована или пуста.";
        }
    }
}

// Запуск переиндексации базы знаний RAG
async function reindexKnowledgeBase() {
    const btn = document.getElementById("btn-reindex-rag");
    const spinner = document.getElementById("rag-index-spinner");
    const statusText = document.getElementById("rag-status-text");
    
    if (btn) btn.disabled = true;
    if (spinner) spinner.style.display = "inline-block";
    if (statusText) statusText.textContent = "Выполняется сканирование файлов и генерация эмбеддингов. Пожалуйста, подождите...";
    
    try {
        const res = await fetch("/api/knowledge/index", { method: "POST" });
        const data = await res.json();
        
        if (data.status === "success") {
            updateRagStatus(data.chunks, data.files);
            alert("Индексация успешно завершена! Обработано файлов: " + data.files + ", абзацев: " + data.chunks);
        } else {
            if (statusText) statusText.textContent = "Ошибка индексации: " + data.message;
            alert("Ошибка индексации: " + data.message);
        }
    } catch (err) {
        console.error(err);
        if (statusText) statusText.textContent = "Не удалось связаться с сервером для индексации.";
        alert("Не удалось запустить индексацию базы знаний.");
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.style.display = "none";
    }
}

// API: Сохранение настроек
async function saveSettingsForm(event) {
    event.preventDefault();
    
    const micVal = document.getElementById("select-mic").value;
    const loopbackVal = document.getElementById("select-loopback").value;
    
    const newSettings = {
        mic_device_id: micVal !== "null" ? parseInt(micVal) : null,
        loopback_device_id: loopbackVal !== "null" ? parseInt(loopbackVal) : null,
        wake_word: document.getElementById("txt-wake-word").value,
        auto_answer_enabled: document.getElementById("chk-auto-answer").checked,
        ollama_url: document.getElementById("txt-ollama-url").value,
        ollama_model: document.getElementById("txt-ollama-model").value,
        tts_voice: document.getElementById("select-tts-voice").value,
        whisper_model: document.getElementById("select-whisper-model").value
    };
    
    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(newSettings)
        });
        const data = await res.json();
        if (data.status === "success") {
            alert("Настройки успешно сохранены!");
            loadSettings(); // Перегружаем
        } else {
            alert("Ошибка сохранения: " + data.message);
        }
    } catch (err) {
        console.error(err);
        alert("Не удалось отправить настройки на сервер.");
    }
}

// API: Загрузка списка прошедших встреч
async function loadHistory() {
    try {
        const res = await fetch("/api/history");
        const list = await res.json();
        
        const container = document.getElementById("history-list-container");
        container.innerHTML = "";
        
        if (list.length === 0) {
            container.innerHTML = '<p class="empty-state">Нет сохраненных встреч.</p>';
            return;
        }
        
        list.forEach(meetingId => {
            const item = document.createElement("button");
            item.className = "history-item";
            if (meetingId === activeHistoryMeetingId) {
                item.classList.add("active");
            }
            
            // Форматируем дату из ID (ГГГГММДД_ЧЧММСС)
            let dateStr = meetingId;
            try {
                const parts = meetingId.split("_");
                const y = parts[0].substring(0, 4);
                const m = parts[0].substring(4, 6);
                const d = parts[0].substring(6, 8);
                const hh = parts[1].substring(0, 2);
                const mm = parts[1].substring(2, 4);
                const ss = parts[1].substring(4, 6);
                dateStr = `${d}.${m}.${y} в ${hh}:${mm}:${ss}`;
            } catch (e) {}
            
            const title = document.createElement("div");
            title.className = "history-item-title";
            title.textContent = `Встреча от ${dateStr}`;
            
            const meta = document.createElement("div");
            meta.className = "history-item-meta";
            meta.textContent = `ID: ${meetingId}`;
            
            item.appendChild(title);
            item.appendChild(meta);
            
            item.onclick = () => viewHistoryDetail(meetingId, item);
            container.appendChild(item);
        });
    } catch (err) {
        console.error(err);
    }
}

// API: Загрузка деталей архивной встречи
async function viewHistoryDetail(meetingId, element) {
    activeHistoryMeetingId = meetingId;
    
    // Подсвечиваем элемент
    document.querySelectorAll(".history-item").forEach(item => {
        item.classList.remove("active");
    });
    if (element) {
        element.classList.add("active");
    }
    
    const container = document.getElementById("history-detail-container");
    container.innerHTML = '<div class="empty-state"><i data-lucide="loader" class="animate-spin"></i><p>Загрузка деталей...</p></div>';
    lucide.createIcons();
    
    try {
        const res = await fetch(`/api/history/${meetingId}`);
        if (res.status !== 200) {
            container.innerHTML = '<p class="empty-state text-danger">Не удалось загрузить данные встречи.</p>';
            return;
        }
        
        const data = await res.json();
        
        // Разблокируем экспорт
        document.getElementById("btn-export-txt").style.display = "inline-flex";
        
        container.innerHTML = `
            <div class="history-detail-scroll">
                <div class="detail-section">
                    <h4><i data-lucide="scroll-text"></i> Краткие итоги созвона</h4>
                    <div class="summary-box">${formatMarkdown(data.summary || "Итоги встречи не были сгенерированы.")}</div>
                </div>
                <div class="detail-section">
                    <h4><i data-lucide="message-square"></i> Лог транскрипта</h4>
                    <div class="history-transcript-list" id="history-transcript-list">
                        <!-- Реплики вставим ниже -->
                    </div>
                </div>
            </div>
        `;
        
        const listContainer = document.getElementById("history-transcript-list");
        if (data.history && data.history.length > 0) {
            data.history.forEach(replica => {
                const bubble = document.createElement("div");
                bubble.className = `speech-bubble ${replica.speaker}`;
                
                const meta = document.createElement("div");
                meta.className = "bubble-meta";
                meta.innerHTML = `<span>${replica.speaker === 'user' ? 'Вы' : 'Коллега'}</span><span>${replica.timestamp}</span>`;
                
                const body = document.createElement("div");
                body.className = "bubble-text";
                body.textContent = replica.text;
                
                bubble.appendChild(meta);
                bubble.appendChild(body);
                listContainer.appendChild(bubble);
            });
        } else {
            listContainer.innerHTML = '<p class="empty-state">Реплик не найдено.</p>';
        }
        
        lucide.createIcons();
        
    } catch (err) {
        console.error(err);
        container.innerHTML = '<p class="empty-state text-danger">Ошибка загрузки деталей.</p>';
    }
}

// Экспорт активной встречи в TXT
async function exportActiveMeeting() {
    if (!activeHistoryMeetingId) return;
    
    try {
        const res = await fetch(`/api/history/${activeHistoryMeetingId}`);
        const data = await res.json();
        
        let text = `=== ПРОТОКОЛ ВСТРЕЧИ ${data.meeting_id} ===\n`;
        text += `Начало: ${data.start_time}\n`;
        text += `Конец: ${data.end_time}\n\n`;
        text += `=== ИТОГИ ВСТРЕЧИ ===\n${data.summary || "Нет саммари"}\n\n`;
        text += `=== ТРАНСКРИПТ ===\n`;
        
        data.history.forEach(r => {
            text += `[${r.timestamp}] ${r.speaker === 'user' ? 'Вы' : 'Коллега'}: ${r.text}\n`;
        });
        
        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `meeting_${data.meeting_id}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } catch (err) {
        alert("Не удалось экспортировать файл: " + err);
    }
}

// Утилита для рендеринга простого markdown в HTML
function formatMarkdown(text) {
    if (!text) return "";
    
    // Простая замена заголовков
    let html = text
        .replace(/^### (.*$)/gim, '<h4>$1</h4>')
        .replace(/^## (.*$)/gim, '<h4>$1</h4>')
        .replace(/^# (.*$)/gim, '<h4>$1</h4>');
        
    // Замена списков
    html = html.replace(/^\s*\n\*/gm, '<ul>');
    html = html.replace(/^\*\s(.*)/gim, '<li>$1</li>');
    html = html.replace(/^\s*-\s(.*)/gim, '<li>$1</li>');
    html = html.replace(/^\d+\.\s(.*)/gim, '<li>$1</li>');
    
    // Замена переносов строк
    html = html.replace(/\n/g, '<br>');
    
    return html;
}

// === ЧАТ С ИИ ===
let chatSessionHistory = [];

async function sendChatMessage(event) {
    event.preventDefault();
    
    const input = document.getElementById("txt-chat-message");
    const container = document.getElementById("chat-messages-container");
    const text = input.value.trim();
    if (!text) return;
    
    // Очищаем поле ввода
    input.value = "";
    
    // Убираем приветствие, если это первое сообщение
    const welcome = container.querySelector(".chat-welcome-msg");
    if (welcome) {
        welcome.remove();
    }
    
    // Добавляем реплику пользователя
    appendChatBubble("user", text);
    chatSessionHistory.push({ role: "user", content: text });
    
    // Показываем индикатор печати ИИ
    const loader = showChatLoader();
    scrollToBottom(container);
    
    try {
        const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: text,
                history: chatSessionHistory
            })
        });
        
        const data = await res.json();
        
        // Убираем лоадер
        loader.remove();
        
        if (data.status === "success" && data.response) {
            appendChatBubble("assistant", data.response);
            chatSessionHistory.push({ role: "assistant", content: data.response });
        } else {
            appendChatBubble("assistant", "Ошибка: не удалось сгенерировать ответ. Попробуйте еще раз.");
        }
    } catch (err) {
        console.error(err);
        loader.remove();
        appendChatBubble("assistant", "Не удалось подключиться к серверу. Убедитесь, что бэкенд и Ollama запущены.");
    }
    
    scrollToBottom(container);
}

function appendChatBubble(role, text) {
    const container = document.getElementById("chat-messages-container");
    const bubble = document.createElement("div");
    
    // Используем готовые стили спич бабблов, но переопределяем выравнивание
    bubble.className = `speech-bubble ${role === 'user' ? 'user' : 'colleague'}`;
    bubble.style.maxWidth = "75%";
    bubble.style.alignSelf = role === 'user' ? 'flex-end' : 'flex-start';
    
    const meta = document.createElement("div");
    meta.className = "bubble-meta";
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    meta.innerHTML = `<span>${role === 'user' ? 'Вы' : 'Ассистент 🦙'}</span><span>${now}</span>`;
    
    const body = document.createElement("div");
    body.className = "bubble-text";
    
    if (role === 'assistant') {
        body.innerHTML = formatMarkdown(text);
    } else {
        body.textContent = text;
    }
    
    bubble.appendChild(meta);
    bubble.appendChild(body);
    container.appendChild(bubble);
}

function showChatLoader() {
    const container = document.getElementById("chat-messages-container");
    const loader = document.createElement("div");
    loader.className = "chat-bubble-loader";
    loader.innerHTML = `<span></span><span></span><span></span>`;
    container.appendChild(loader);
    return loader;
}

