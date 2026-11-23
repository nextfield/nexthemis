import os
import sys
import json
import time
import queue
import threading
import subprocess
from datetime import datetime
from pathlib import Path

import tkinter as tk
import psutil
import pystray
from PIL import Image, ImageDraw
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

APP_NAME = "nexthemis"
APP_VERSION = "1.1.0"
APP_AUTHOR = "nextfield"
ICON_FILENAME = "Logo_N.png"

# Speicherorte für Konfiguration und technisches Protokoll
DATA_DIR = Path.home() / ".nexthemis"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "nexthemis_alerts.log"

DEFAULT_CONFIG = {
    "bubble_enabled": True,
    "bubble_duration": 6,       # Sekunden
    "bubble_opacity": 0.88,     # 0.50 bis 1.00
    "whitelist_processes": ["nexthron.exe", "nextheia.exe", "nexthemis.exe"],
    "whitelist_ports": []
}

# Überwachte KI-Ports und verdächtige Log-Muster
AI_PORTS = {
    11434: "Lokaler KI-Server (Ollama)",
    1234: "Lokales Sprachmodell (LM Studio)",
    5000: "Lokale KI-Schnittstelle",
    8000: "Lokale KI-API",
    8080: "Lokaler Modell-Dienst"
}

SUSPICIOUS_EXTENSIONS = {".jsonl", ".sqlite", ".sqlite3", ".db", ".log", ".json"}
SUSPICIOUS_KEYWORDS = (
    "system_prompt",
    "tool_calls",
    "embeddings",
    "vector_search",
    "rag_query",
    "api.openai.com",
    "api.anthropic.com",
    "localhost:11434"
)


def resource_path(relative_path: str) -> Path:
    """Findet Ressourcen sowohl im Entwicklungsordner als auch in der PyInstaller-.exe."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return Path(base_path) / relative_path


class ConfigManager:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
            except Exception:
                pass
        self.save()

    def save(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def write_log(category: str, human_msg: str, tech_detail: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{category}] {human_msg} | Detail: {tech_detail}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class AlertBubbleManager:
    """Zeigt rahmenlose, transparente Alarm-Nachrichtenblasen über der Windows-Taskleiste."""

    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root = root
        self.config = config
        self.alert_queue = queue.Queue()
        self.current_window = None
        self.hide_after_id = None
        self._poll_queue()

    def push_alert(self, title: str, message: str):
        self.alert_queue.put((title, message))

    def _poll_queue(self):
        try:
            while not self.alert_queue.empty():
                title, message = self.alert_queue.get_nowait()
                if self.config.data.get("bubble_enabled", True):
                    self._show_bubble(title, message)
        except Exception:
            pass
        self.root.after(250, self._poll_queue)

    def _close_current(self):
        if self.hide_after_id:
            try:
                self.root.after_cancel(self.hide_after_id)
            except Exception:
                pass
            self.hide_after_id = None
        if self.current_window is not None:
            try:
                self.current_window.destroy()
            except Exception:
                pass
            self.current_window = None

    def _show_bubble(self, title: str, message: str):
        self._close_current()

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        opacity = float(self.config.data.get("bubble_opacity", 0.88))
        win.attributes("-alpha", max(0.3, min(1.0, opacity)))

        bg_main = "#111827"
        bg_accent = "#06b6d4"
        fg_title = "#f9fafb"
        fg_body = "#d1d5db"

        win.configure(bg=bg_accent)

        inner = tk.Frame(win, bg=bg_main, padx=16, pady=12)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(inner, bg=bg_main)
        header.pack(fill="x")

        lbl_badge = tk.Label(
            header,
            text=f"{APP_NAME}  v{APP_VERSION}",
            font=("Segoe UI", 9, "bold"),
            fg=bg_accent,
            bg=bg_main
        )
        lbl_badge.pack(side="left")

        btn_close = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 10, "bold"),
            fg="#9ca3af",
            bg=bg_main,
            cursor="hand2"
        )
        btn_close.pack(side="right")
        btn_close.bind("<Button-1>", lambda e: self._close_current())

        lbl_title = tk.Label(
            inner,
            text=title,
            font=("Segoe UI", 10, "bold"),
            fg=fg_title,
            bg=bg_main,
            anchor="w",
            justify="left",
            wraplength=340
        )
        lbl_title.pack(fill="x", pady=(6, 4))

        lbl_msg = tk.Label(
            inner,
            text=message,
            font=("Segoe UI", 9),
            fg=fg_body,
            bg=bg_main,
            anchor="w",
            justify="left",
            wraplength=340
        )
        lbl_msg.pack(fill="x")

        # Klick auf die Blase schließt sie sofort
        for widget in (inner, lbl_title, lbl_msg, lbl_badge):
            widget.bind("<Button-1>", lambda e: self._close_current())

        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()

        x = screen_w - width - 20
        y = screen_h - height - 64
        win.geometry(f"{width}x{height}+{x}+{y}")

        self.current_window = win
        duration_ms = int(self.config.data.get("bubble_duration", 6)) * 1000
        self.hide_after_id = self.root.after(duration_ms, self._close_current)


class AIFileWatcher(FileSystemEventHandler):
    def __init__(self, on_alert_callback):
        super().__init__()
        self.on_alert = on_alert_callback
        self.last_seen = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        self._inspect(Path(event.src_path))

    def on_created(self, event):
        if event.is_directory:
            return
        self._inspect(Path(event.src_path))

    def _inspect(self, path: Path):
        if ".nexthemis" in str(path):
            return
        if path.suffix.lower() not in SUSPICIOUS_EXTENSIONS:
            return

        now = time.time()
        if now - self.last_seen.get(str(path), 0) < 15:
            return
        self.last_seen[str(path)] = now

        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return
            content = path.read_text(encoding="utf-8", errors="ignore")[-4000:]
            lower_content = content.lower()
            matched = [kw for kw in SUSPICIOUS_KEYWORDS if kw in lower_content]
            if matched:
                folder_hint = path.parent.name
                human_msg = (
                    f"Im Ordner „{folder_hint}“ wurden gerade unaufgefordert "
                    f"KI-Such- oder Kommunikationsdaten geschrieben ({path.name})."
                )
                self.on_alert(
                    "DATEI-AKTIVITÄT",
                    "Versteckte KI-Datenaktivität erkannt",
                    human_msg,
                    f"Datei: {path} | Muster: {', '.join(matched)}"
                )
        except Exception:
            pass


class SystemAuditor:
    def __init__(self, config: ConfigManager, bubble_mgr: AlertBubbleManager):
        self.config = config
        self.bubble = bubble_mgr
        self.running = True
        self.cooldowns = {}
        self.observer = None

    def trigger_alert(self, category: str, title: str, human_msg: str, tech_detail: str):
        key = f"{category}:{title}:{human_msg}"
        now = time.time()
        if now - self.cooldowns.get(key, 0) < 30:
            return
        self.cooldowns[key] = now

        write_log(category, human_msg, tech_detail)
        self.bubble.push_alert(title, human_msg)

    def start(self):
        # 1. Dateisystem-Überwachung im AppData-Bereich
        appdata = os.getenv("LOCALAPPDATA")
        if appdata and os.path.isdir(appdata):
            handler = AIFileWatcher(self.trigger_alert)
            self.observer = Observer()
            self.observer.schedule(handler, appdata, recursive=True)
            self.observer.start()

        # 2. Hintergrund-Thread für aktive KI-Netzwerkverbindungen
        t = threading.Thread(target=self._monitor_network_loop, daemon=True)
        t.start()

    def stop(self):
        self.running = False
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass

    def _monitor_network_loop(self):
        while self.running:
            try:
                whitelist_procs = {
                    p.lower() for p in self.config.data.get("whitelist_processes", [])
                }
                whitelist_ports = set(self.config.data.get("whitelist_ports", []))

                for conn in psutil.net_connections(kind="inet"):
                    if not conn.raddr:
                        continue
                    rport = conn.raddr.port
                    if rport in AI_PORTS and rport not in whitelist_ports:
                        proc_name = "Unbekanntes Programm"
                        if conn.pid:
                            try:
                                proc_name = psutil.Process(conn.pid).name()
                            except Exception:
                                pass
                        if proc_name.lower() in whitelist_procs:
                            continue

                        service_desc = AI_PORTS[rport]
                        human_msg = (
                            f"Das Programm „{proc_name}“ greift im Hintergrund "
                            f"unaufgefordert auf einen KI-Dienst zu ({service_desc})."
                        )
                        self.trigger_alert(
                            "KI-ZUGRIFF",
                            "KI-Aktivität im Hintergrund erkannt",
                            human_msg,
                            f"Prozess: {proc_name} (PID {conn.pid}) -> Port {rport}"
                        )
            except Exception:
                pass
            time.sleep(4)


def load_tray_icon() -> Image.Image:
    icon_file = resource_path(ICON_FILENAME)
    try:
        with Image.open(icon_file) as img:
            return img.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    except Exception as e:
        write_log("WARNUNG", f"Tray-Icon konnte nicht geladen werden ({icon_file})", str(e))
        # Fallback-Icon
        img = Image.new("RGBA", (64, 64), (17, 24, 39, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse((6, 6, 58, 58), outline=(6, 182, 212, 255), width=4)
        return img


class NexthemisApp:
    def __init__(self):
        self.config = ConfigManager()
        self.root = tk.Tk()
        self.root.withdraw()  # Hauptfenster bleibt unsichtbar

        self.bubble_mgr = AlertBubbleManager(self.root, self.config)
        self.auditor = SystemAuditor(self.config, self.bubble_mgr)
        self.tray_icon = None

    def open_log_file(self, icon=None, item=None):
        write_log("INFO", "Protokoll durch Benutzer geöffnet.", str(LOG_FILE))
        try:
            os.startfile(str(LOG_FILE))
        except Exception:
            subprocess.Popen(["notepad.exe", str(LOG_FILE)])

    def show_test_bubble(self, icon=None, item=None):
        self.bubble_mgr.push_alert(
            "Test-Alarm: KI-Aktivität erkannt",
            "Beispielmeldung: Ein Hintergrundprogramm greift gerade unaufgefordert auf einen lokalen KI-Dienst zu."
        )

    def show_about(self, icon=None, item=None):
        self.bubble_mgr.push_alert(
            f"{APP_NAME} v{APP_VERSION} – {APP_AUTHOR}",
            "Echtzeit-Systemwächter für unbemerkte KI- und Agent-Aktivitäten unter Windows 11. Open Source auf GitHub (nextfield/nexthemis)."
        )

    def toggle_bubble(self, icon=None, item=None):
        current = self.config.data.get("bubble_enabled", True)
        self.config.data["bubble_enabled"] = not current
        self.config.save()

    def set_duration(self, seconds: int):
        def inner(icon=None, item=None):
            self.config.data["bubble_duration"] = seconds
            self.config.save()
        return inner

    def set_opacity(self, opacity: float):
        def inner(icon=None, item=None):
            self.config.data["bubble_opacity"] = opacity
            self.config.save()
        return inner

    def quit_app(self, icon=None, item=None):
        self.auditor.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def build_menu(self):
        duration_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    f"{sec} Sekunden",
                    self.set_duration(sec),
                    checked=lambda item, s=sec: self.config.data.get("bubble_duration") == s,
                    radio=True
                )
                for sec in (3, 5, 8, 12, 20)
            ]
        )

        opacity_menu = pystray.Menu(
            *[
                pystray.MenuItem(
                    f"{int(op * 100)} %",
                    self.set_opacity(op),
                    checked=lambda item, o=op: abs(self.config.data.get("bubble_opacity", 0.88) - o) < 0.01,
                    radio=True
                )
                for op in (0.50, 0.70, 0.85, 0.95, 1.00)
            ]
        )

        return pystray.Menu(
            pystray.MenuItem(f"{APP_NAME} v{APP_VERSION} ({APP_AUTHOR})", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Alarm-Nachrichtenblase aktiv",
                self.toggle_bubble,
                checked=lambda item: self.config.data.get("bubble_enabled", True)
            ),
            pystray.MenuItem("Anzeigedauer", duration_menu),
            pystray.MenuItem("Transparenz", opacity_menu),
            pystray.MenuItem("Test-Alarm anzeigen", self.show_test_bubble),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Alarm-Protokoll öffnen", self.open_log_file),
            pystray.MenuItem(f"Über {APP_NAME} (v{APP_VERSION})", self.show_about),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self.quit_app)
        )

    def run(self):
        write_log("START", f"{APP_NAME} v{APP_VERSION} gestartet.", "Systemüberwachung aktiv")
        self.auditor.start()

        self.tray_icon = pystray.Icon(
            APP_NAME,
            load_tray_icon(),
            f"{APP_NAME} v{APP_VERSION} – KI-Systemwächter",
            self.build_menu()
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        self.root.mainloop()


if __name__ == "__main__":
    app = NexthemisApp()
    app.run()