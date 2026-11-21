"""
nexthemis - Echtzeit-Wächter für KI- & Agent-Aktivitäten unter Windows
Projekt: nextfield (github.com/nextfield/nexthemis)
"""

import os
import re
import time
import threading
import subprocess
from datetime import datetime
from pathlib import Path

import psutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from winotify import Notification, audio
import pystray
from PIL import Image, ImageDraw

# ==============================================================================
# KONFIGURATION & WHITELIST (nextfield / nexthron)
# ==============================================================================
WHITELIST_TERMS = [
    "nexthron",
    "nexthemis",
    "nextfield"
]

# Keywords, die auf KI-Logs, Embeddings, Suchen oder Prompt-Kommunikation hinweisen
AI_LOG_PATTERN = re.compile(
    r'("role"\s*:\s*"(system|assistant|tool)"|"tool_calls"|system_prompt|'
    r'embeddings|vector_search|similarity_search|semantic_search|rag_query|'
    r'api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|'
    r'localhost:11434|127\.0\.0\.1:11434)',
    re.IGNORECASE
)

# Verdächtige Ordner- oder Prozess-Signaturen
AI_SIGNATURES = [
    "ollama", "langchain", "autogen", "crewai", "openai", "anthropic",
    "huggingface", "lm-studio", "cursor", "copilot", "continue",
    "chromadb", "qdrant", "milvus", "mcp", "llama-server"
]

# Bekannte KI-Ports (lokal & remote)
MONITORED_PORTS = {11434, 1234, 5000, 8000, 8080}

LOG_DIR = Path.home() / ".nexthemis"
LOG_DIR.mkdir(exist_ok=True)
ALERT_LOG_FILE = LOG_DIR / "nexthemis_alerts.log"

# Cooldown-Speicher, damit nicht dieselbe Datei im Sekundentakt Alarm schlägt
RECENT_ALERTS = {}
ALERT_COOLDOWN_SEC = 30


# ==============================================================================
# ALARM- & BENACHRICHTIGUNGSSYSTEM
# ==============================================================================
def trigger_alert(category: str, title: str, detail: str):
    now = time.time()
    alert_key = f"{category}:{detail}"

    if alert_key in RECENT_ALERTS and (now - RECENT_ALERTS[alert_key]) < ALERT_COOLDOWN_SEC:
        return
    RECENT_ALERTS[alert_key] = now

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{category}] {title} -> {detail}\n"

    with open(ALERT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

    # Native Windows 11 Desktop-Benachrichtigung
    toast = Notification(
        app_id="nexthemis | nextfield",
        title=f"nexthemis: {title}",
        msg=f"[{category}]\n{detail[:180]}",
        duration="short"
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()


def is_whitelisted(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in WHITELIST_TERMS)


# ==============================================================================
# 1. ECHTZEIT-DATEISYSTEM-WÄCHTER (LOGS & KI-SUCHEN)
# ==============================================================================
class AILogWatchdogHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            self._inspect_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._inspect_file(event.src_path)

    def _inspect_file(self, file_path: str):
        if is_whitelisted(file_path):
            return

        lower_path = file_path.lower()

        # Prüfen, ob ein neuer KI-/Vektor-Speicher angelegt wurde
        if any(sig in lower_path for sig in AI_SIGNATURES):
            if lower_path.endswith((".sqlite", ".sqlite3", ".db", ".jsonl", ".log", ".json")):
                trigger_alert(
                    "DATEI-ARTEFAKT",
                    "KI-Speicher oder Log erstellt",
                    file_path
                )
                return

        # Inhalt von Log- und JSON-Dateien auf KI-Kommunikation / Such-Queries prüfen
        if lower_path.endswith((".log", ".json", ".jsonl", ".txt")):
            try:
                if os.path.exists(file_path) and os.path.getsize(file_path) < 10 * 1024 * 1024:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        # Nur die letzten 16 KB der Datei lesen (für Echtzeit-Performance)
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - 16384))
                        tail = f.read()

                        match = AI_LOG_PATTERN.search(tail)
                        if match:
                            trigger_alert(
                                "KI-LOG / SUCHE",
                                f"KI-Aktivität erkannt ({match.group(0)})",
                                file_path
                            )
            except OSError:
                pass


# ==============================================================================
# 2. HINTERGRUND-PROZESS- & NETZWERK-MONITOR
# ==============================================================================
def monitor_processes_and_network(stop_event: threading.Event):
    known_pids = set(psutil.pids())

    while not stop_event.is_set():
        current_pids = set()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']
                current_pids.add(pid)
                name = proc.info['name'] or ""
                cmd = " ".join(proc.info['cmdline'] or [])

                if is_whitelisted(name) or is_whitelisted(cmd):
                    continue

                # Neue verdächtige KI-Prozesse erkennen
                if pid not in known_pids:
                    combined = f"{name} {cmd}".lower()
                    for sig in AI_SIGNATURES:
                        if sig in combined:
                            trigger_alert(
                                "PROZESS-START",
                                f"Neuer KI-Prozess: {name}",
                                f"PID {pid} | {cmd[:140]}"
                            )
                            break

                # Aktive Netzwerkverbindungen auf KI-Ports prüfen
                for conn in proc.net_connections(kind='inet'):
                    if conn.raddr and conn.raddr.port in MONITORED_PORTS:
                        trigger_alert(
                            "KI-ANFRAGE / SUCHE",
                            f"{name} nutzt KI-Schnittstelle",
                            f"Ziel: {conn.raddr.ip}:{conn.raddr.port} (PID {pid})"
                        )
                        break

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        known_pids = current_pids
        stop_event.wait(3.0)


# ==============================================================================
# 3. SYSTEM-TRAY ICON & STEUERUNG
# ==============================================================================
def create_tray_icon_image():
    # Minimalistisches Waage-/Schild-Symbol in Dunkelblau/Cyan
    img = Image.new("RGB", (64, 64), color=(18, 24, 38))
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, 56, 56], outline=(0, 210, 255), width=4)
    draw.line([32, 16, 32, 48], fill=(0, 210, 255), width=4)
    draw.line([18, 26, 46, 26], fill=(0, 210, 255), width=4)
    return img


def open_alert_log(icon, item):
    if not ALERT_LOG_FILE.exists():
        ALERT_LOG_FILE.write_text("nexthemis Überwachungsprotokoll gestartet.\n", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(ALERT_LOG_FILE)])


def quit_app(icon, item, stop_event, observer):
    stop_event.set()
    observer.stop()
    icon.stop()


def main():
    stop_event = threading.Event()
    observer = Observer()
    handler = AILogWatchdogHandler()

    # Relevante Benutzer- und System-Log-Pfade überwachen
    watch_paths = [
        os.environ.get("APPDATA", ""),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("TEMP", "")
    ]

    for path in watch_paths:
        if path and os.path.exists(path):
            observer.schedule(handler, path, recursive=True)

    observer.start()

    net_thread = threading.Thread(
        target=monitor_processes_and_network,
        args=(stop_event,),
        daemon=True
    )
    net_thread.start()

    # Start-Bestätigung
    trigger_alert(
        "SYSTEM-START",
        "nexthemis Wächter aktiv",
        "Überwacht Dateisystem, KI-Logs und lokale Ports im Hintergrund."
    )

    menu = pystray.Menu(
        pystray.MenuItem("nexthemis (aktiv)", None, enabled=False),
        pystray.MenuItem("Alarm-Protokoll öffnen", open_alert_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", lambda icon, item: quit_app(icon, item, stop_event, observer))
    )

    tray_icon = pystray.Icon("nexthemis", create_tray_icon_image(), "nexthemis | KI-Systemwächter", menu)
    tray_icon.run()


if __name__ == "__main__":
    main()