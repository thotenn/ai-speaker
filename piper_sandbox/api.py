from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .config import env_bool, env_int, load_env
from .engine import PiperEngine, PiperError
from .models import DEFAULT_MODEL, MODELS


MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024


INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Piper TTS Sandbox</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111827; color: #f9fafb; }
    main { width: min(760px, calc(100vw - 32px)); padding: 28px; border: 1px solid #374151; border-radius: 20px; background: #1f2937; box-shadow: 0 24px 80px #0008; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    p { color: #cbd5e1; }
    textarea, select, button { font: inherit; }
    textarea { width: 100%; min-height: 170px; box-sizing: border-box; resize: vertical; padding: 14px; border-radius: 14px; border: 1px solid #4b5563; background: #0f172a; color: #f9fafb; }
    .row { display: flex; gap: 12px; align-items: center; margin-top: 14px; flex-wrap: wrap; }
    select { padding: 10px 12px; border-radius: 999px; border: 1px solid #4b5563; background: #111827; color: #f9fafb; }
    button { border: 0; border-radius: 999px; padding: 11px 18px; background: #f97316; color: #111827; font-weight: 800; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    #status { margin-left: auto; color: #93c5fd; }
    audio { width: 100%; margin-top: 18px; }
    kbd { border: 1px solid #64748b; border-bottom-width: 3px; padding: 1px 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <main>
    <h1>Piper TTS Sandbox</h1>
    <p>Escribe texto y presiona <kbd>Ctrl</kbd> + <kbd>Enter</kbd> o el boton para generar voz.</p>
    <textarea id="text">Hola, esta es una prueba de voz en espanhol latinoamericano con Piper.</textarea>
    <div class="row">
      <label for="model">Modelo</label>
      <select id="model"></select>
      <button id="speak">Hablar</button>
      <span id="status">Listo</span>
    </div>
    <audio id="audio" controls></audio>
  </main>
  <script>
    const text = document.querySelector('#text');
    const model = document.querySelector('#model');
    const speak = document.querySelector('#speak');
    const status = document.querySelector('#status');
    const audio = document.querySelector('#audio');

    const engineUrl = __ENGINE_URL_JSON__;
    const apiUrl = (path) => `${engineUrl}${path}`;

    async function loadModels() {
      const response = await fetch(apiUrl('/models'));
      const data = await response.json();
      for (const item of data.models) {
        const option = document.createElement('option');
        option.value = item.name;
        option.textContent = `${item.name} (${item.country}, ${item.quality})`;
        model.appendChild(option);
      }
      model.value = data.default;
    }

    async function say() {
      const body = { text: text.value.trim(), model: model.value };
      if (!body.text) return;
      speak.disabled = true;
      status.textContent = 'Generando audio...';
      try {
        const response = await fetch(apiUrl('/speak'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error(await response.text());
        const blob = await response.blob();
        audio.src = URL.createObjectURL(blob);
        await audio.play();
        status.textContent = `Reproduciendo con ${body.model}`;
      } catch (error) {
        status.textContent = 'Error';
        alert(error.message);
      } finally {
        speak.disabled = false;
      }
    }

    speak.addEventListener('click', say);
    text.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        say();
      }
    });
    loadModels();
  </script>
</body>
</html>
"""


class PiperRequestHandler(BaseHTTPRequestHandler):
    engine = PiperEngine()
    service_mode = "both"
    engine_url = ""
    cors_origin = "*"

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/":
            if not self.gui_enabled:
                self._send_error(HTTPStatus.NOT_FOUND, "GUI is disabled")
                return
            engine_url_js = json.dumps(self.engine_url).replace("<", "\\u003c").replace(">", "\\u003e")
            html = INDEX_HTML.replace("__ENGINE_URL_JSON__", engine_url_js)
            self._send_text(html, "text/html; charset=utf-8")
            return

        if path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "mode": self.service_mode,
                    "engine": self.engine_enabled,
                    "gui": self.gui_enabled,
                    "engine_url": self.engine_url,
                }
            )
            return

        if path == "/models":
            if not self.engine_enabled:
                self._send_error(HTTPStatus.NOT_FOUND, "Engine is disabled")
                return
            body = {
                "default": DEFAULT_MODEL,
                "models": [spec.__dict__ for spec in MODELS.values()],
            }
            self._send_json(body)
            return

        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path != "/speak":
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if not self.engine_enabled:
            self._send_error(HTTPStatus.NOT_FOUND, "Engine is disabled")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
            return

        if length > MAX_REQUEST_BODY_BYTES:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Payload too large")
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = str(payload.get("text", ""))
            model = str(payload.get("model", DEFAULT_MODEL))
            wav = self.engine.synthesize_bytes(text, model=model)
        except json.JSONDecodeError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return
        except (KeyError, PiperError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(wav)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    @property
    def engine_enabled(self) -> bool:
        return self.service_mode in {"both", "engine"}

    @property
    def gui_enabled(self) -> bool:
        return self.service_mode in {"both", "gui"}

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _send_json(self, body: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, body: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_text(message, "text/plain; charset=utf-8", status=status)

    def _send_cors_headers(self) -> None:
        if not self.cors_origin:
            return
        self.send_header("Access-Control-Allow-Origin", self.cors_origin)
        self.send_header("Vary", "Origin")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Run the Piper sandbox web GUI and API.")
    parser.add_argument("--host", default=os.environ.get("PIPER_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=env_int("PIPER_PORT", 8000), type=int)
    parser.add_argument("--mode", choices=("both", "engine", "gui"), default=os.environ.get("PIPER_SERVICE_MODE", "both"))
    parser.add_argument("--engine-url", default=os.environ.get("PIPER_ENGINE_URL", ""))
    parser.add_argument("--cors-origin", default=os.environ.get("PIPER_CORS_ORIGIN", "*"))
    parser.add_argument("--gui", dest="enable_gui", action="store_true")
    parser.add_argument("--no-gui", dest="enable_gui", action="store_false")
    parser.set_defaults(enable_gui=None)
    args = parser.parse_args()

    service_mode = args.mode
    if args.enable_gui is not None:
        service_mode = "both" if args.enable_gui else "engine"
    elif "PIPER_ENABLE_GUI" in os.environ:
        service_mode = "both" if env_bool("PIPER_ENABLE_GUI", True) else "engine"

    PiperRequestHandler.service_mode = service_mode
    PiperRequestHandler.engine_url = args.engine_url.rstrip("/")
    PiperRequestHandler.cors_origin = args.cors_origin
    server = ThreadingHTTPServer((args.host, args.port), PiperRequestHandler)
    print(f"Piper Sandbox listening on http://{args.host}:{args.port}")
    print(f"Mode: {service_mode}")
    if service_mode == "gui":
        print(f"Engine URL: {PiperRequestHandler.engine_url or 'same origin'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Piper Sandbox")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
