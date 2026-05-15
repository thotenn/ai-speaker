# Piper Sandbox

Pequena libreria, API HTTP y GUI web opcional para probar voces TTS con Piper. El modelo por defecto es `es_MX-ald-medium`, una voz en espanhol latinoamericano disponible en `rhasspy/piper-voices`.

La primera generacion descarga automaticamente el modelo en `PIPER_MODELS_DIR`.

## Requisitos

- Python 3.11 o 3.12 recomendado.
- Binario `piper` disponible en `PATH`, o variable `PIPER_BIN=/ruta/a/piper`.
- Navegador web si quieres usar la GUI.

Python 3.13 puede funcionar para la API, pero `piper-tts` puede fallar por dependencias binarias como `onnxruntime`.

## Instalacion Local

Crear entorno virtual:

```bash
python -m venv .venv
source .venv/bin/activate
```

Instalar la libreria sin Piper:

```bash
pip install -e .
```

Instalar la libreria intentando incluir el ejecutable `piper` desde PyPI:

```bash
pip install -e '.[tts]'
```

Verificar que `piper` existe:

```bash
piper --help
```

Si no se instala con `pip`, instala el binario oficial de Piper y dejalo en tu `PATH`. Tambien puedes apuntar directamente al binario:

```bash
export PIPER_BIN=/ruta/a/piper
```

## Configuracion

Copia el ejemplo de entorno:

```bash
cp .env.example .env
```

Variables disponibles:

```env
PIPER_HOST=127.0.0.1
PIPER_PORT=8000
PIPER_ENABLE_GUI=true
PIPER_MODELS_DIR=models/piper
# PIPER_BIN=/usr/local/bin/piper
```

Para desactivar la GUI y dejar solo la API:

```env
PIPER_ENABLE_GUI=false
```

Tambien puedes usar flags:

```bash
python -m piper_sandbox.api --no-gui
python -m piper_sandbox.api --gui
```

## Ejecutar

```bash
python -m piper_sandbox.api
```

Con la configuracion por defecto abre:

```text
http://127.0.0.1:8000
```

La GUI web permite escribir texto, elegir modelo y reproducir el audio generado. Presiona `Ctrl+Enter` o el boton `Hablar`.

## Endpoints

### `GET /health`

Health check para servidores y Docker.

Respuesta:

```json
{
  "status": "ok",
  "gui": true
}
```

### `GET /models`

Lista los modelos disponibles en esta app.

Ejemplo:

```bash
curl http://127.0.0.1:8000/models
```

### `POST /speak`

Genera audio WAV. Recibe JSON con `text` y `model`.

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola desde Piper","model":"es_MX-ald-medium"}' \
  --output salida.wav
```

Respuesta exitosa:

```text
Content-Type: audio/wav
```

### `GET /`

Muestra la GUI web si `PIPER_ENABLE_GUI=true`.

Si `PIPER_ENABLE_GUI=false`, responde `404` con `GUI is disabled`.

## Uso Como Libreria

```python
from piper_sandbox import PiperEngine

engine = PiperEngine()
engine.synthesize_to_file(
    "Hola, esta es una prueba.",
    "salida.wav",
    model="es_MX-ald-medium",
)
```

## Wyoming Piper

La API incluida genera WAV usando el binario `piper`. Tambien hay un wrapper pequeno para arrancar `wyoming-piper` si lo instalas aparte.

```python
from piper_sandbox import WyomingPiperService

service = WyomingPiperService(voice="es_MX-ald-medium")
service.start()
```

Equivalente aproximado por terminal:

```bash
wyoming-piper \
  --uri tcp://127.0.0.1:10200 \
  --voice es_MX-ald-medium \
  --data-dir models/wyoming-piper \
  --download-dir models/wyoming-piper
```

## Docker

Construir y ejecutar con Docker Compose:

```bash
docker compose up --build
```

Abrir:

```text
http://127.0.0.1:8000
```

Desactivar GUI en Docker:

```bash
PIPER_ENABLE_GUI=false docker compose up --build
```

La imagen instala el extra `.[tts]`, que intenta instalar `piper-tts`. Los modelos se guardan en el volumen `piper-models`.

## Coolify Con GitHub Apps

Pasos recomendados:

1. Sube este proyecto a un repositorio GitHub.
2. En Coolify, crea un nuevo recurso desde GitHub Apps.
3. Selecciona el repositorio.
4. Elige despliegue con `Docker Compose`.
5. Usa el archivo `docker-compose.yml` incluido.
6. Configura variables de entorno si quieres cambiar comportamiento.

Variables utiles para Coolify:

```env
PIPER_ENABLE_GUI=true
```

Coolify normalmente provee el dominio y proxy externo. El contenedor escucha en `0.0.0.0:8000`.

Si lo quieres solo como API publica, usa:

```env
PIPER_ENABLE_GUI=false
```

## Modelos Incluidos

- `es_MX-ald-medium`: espanhol Mexico, recomendado para prueba latinoamericana.
- `es_ES-carlfm-x_low`: espanhol Espanha, alternativa liviana.
