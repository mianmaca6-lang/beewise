#!/usr/bin/env python3
"""Transcribe audios (faster-whisper) y lee imágenes/capturas (OCR con Tesseract).
Todo corre local, sin API key.

Flujo por defecto:
    1. Toma los audios e imágenes de la carpeta `input` (junto a este script).
    2. Transcribe los audios y extrae el texto de las imágenes.
    3. Guarda cada .txt en la carpeta `transcripciones`.
    4. Mueve el archivo original a la carpeta `check`.

Instalación (audios):
    pip install faster-whisper "av<19"

Instalación (imágenes):
    sudo apt install tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng
    pip install pytesseract pillow

Uso:
    python transcribir.py                      # procesa todo lo que hay en input/
    python transcribir.py -m medium --timestamps
    python transcribir.py un_audio.mp3         # procesa solo ese archivo
    python transcribir.py captura.png          # idem, para una imagen
"""

import argparse
import shutil
import sys
from functools import partial
from pathlib import Path

BASE = Path(__file__).resolve().parent
CARPETA_INPUT = BASE / "input"
CARPETA_CHECK = BASE / "check"
CARPETA_TXT = BASE / "transcripciones"

EXT_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
             ".wma", ".opus", ".mp4", ".webm"}
EXT_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
EXTENSIONES = EXT_AUDIO | EXT_IMAGEN

# Código de idioma del usuario -> código de Tesseract
LANG_OCR = {"es": "spa", "en": "eng", "auto": "spa+eng"}

# Motores ya cargados (o el error que dieron), para cargarlos una sola vez.
_MOTORES: dict = {}


def parse_args():
    p = argparse.ArgumentParser(
        description="Transcribe audios (faster-whisper) y lee imágenes (OCR).")
    p.add_argument("ruta", nargs="?", default=None,
                   help="(Opcional) un solo archivo, audio o imagen. "
                        "Si no se da, se procesa la carpeta input/")
    p.add_argument("-m", "--modelo", default="small",
                   help="Solo audio. tiny | base | small | medium | large-v3 (default: small)")
    p.add_argument("-l", "--idioma", default="es",
                   help="Idioma para audio e imágenes: es, en o 'auto' (default: es)")
    p.add_argument("--timestamps", action="store_true",
                   help="Solo audio. Agrega [mm:ss] al inicio de cada segmento")
    p.add_argument("--dispositivo", choices=["auto", "cpu", "cuda"], default="auto",
                   help="Solo audio. Dispositivo de cómputo (default: auto)")
    return p.parse_args()


def es_imagen(ruta: Path) -> bool:
    return ruta.suffix.lower() in EXT_IMAGEN


def listar_archivos(args) -> list[Path]:
    if args.ruta:
        ruta = Path(args.ruta).expanduser()
        if not ruta.is_file():
            raise FileNotFoundError(f"No existe el archivo: {ruta}")
        return [ruta]
    return sorted(
        p for p in CARPETA_INPUT.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONES
    )


# ----------------------------- Audio (whisper) -----------------------------

def cargar_modelo(nombre: str, dispositivo: str):
    from faster_whisper import WhisperModel

    if dispositivo == "cpu":
        return WhisperModel(nombre, device="cpu", compute_type="int8")
    if dispositivo == "cuda":
        return WhisperModel(nombre, device="cuda", compute_type="float16")

    # auto: intenta GPU y si falla usa CPU
    try:
        return WhisperModel(nombre, device="cuda", compute_type="float16")
    except Exception:
        return WhisperModel(nombre, device="cpu", compute_type="int8")


def obtener_whisper(args):
    """Carga el modelo la primera vez que hay un audio. Si falla, recuerda el error."""
    if "whisper" not in _MOTORES:
        try:
            _MOTORES["whisper"] = cargar_modelo(args.modelo, args.dispositivo)
        except ImportError:
            _MOTORES["whisper"] = RuntimeError(
                'Falta faster-whisper. Instálalo con: pip install faster-whisper "av<19"')
        except Exception as e:
            _MOTORES["whisper"] = RuntimeError(
                f"Error cargando el modelo '{args.modelo}': {e}")
    motor = _MOTORES["whisper"]
    if isinstance(motor, Exception):
        raise motor
    return motor


def formatear_tiempo(segundos: float) -> str:
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcribir(modelo, ruta: Path, idioma: str, timestamps: bool):
    """Generador: entrega una línea de texto por segmento de audio."""
    segmentos, info = modelo.transcribe(
        str(ruta),
        language=None if idioma == "auto" else idioma,
        beam_size=5,
        vad_filter=True,
    )
    print(f"  Idioma: {info.language} | Duración: {formatear_tiempo(info.duration)}",
          file=sys.stderr)

    for seg in segmentos:
        texto = seg.text.strip()
        if not texto:
            continue
        if info.duration:
            avance = min(seg.end / info.duration, 1.0) * 100
            print(f"\r  Progreso: {avance:5.1f}%", end="", file=sys.stderr, flush=True)
        yield f"[{formatear_tiempo(seg.start)}] {texto}" if timestamps else texto

    print(file=sys.stderr)


# ----------------------------- Imágenes (OCR) ------------------------------

def idioma_ocr(idioma: str) -> str:
    return LANG_OCR.get(idioma, idioma)


def verificar_ocr(lang: str) -> None:
    """Comprueba (una sola vez por idioma) que Tesseract y su idioma estén instalados."""
    clave = ("ocr", lang)
    if clave not in _MOTORES:
        try:
            import pytesseract
            from PIL import Image  # noqa: F401
        except ImportError:
            _MOTORES[clave] = RuntimeError(
                "Faltan pytesseract/Pillow. Instálalos con: pip install pytesseract pillow")
        else:
            try:
                pytesseract.get_tesseract_version()
            except pytesseract.TesseractNotFoundError:
                _MOTORES[clave] = RuntimeError(
                    "Falta el programa tesseract. Instálalo con: "
                    "sudo apt install tesseract-ocr tesseract-ocr-spa")
            else:
                instalados = set(pytesseract.get_languages(config=""))
                faltan = [x for x in lang.split("+") if x not in instalados]
                if faltan:
                    paquetes = " ".join(f"tesseract-ocr-{x}" for x in faltan)
                    _MOTORES[clave] = RuntimeError(
                        f"Falta el idioma de Tesseract: {', '.join(faltan)}. "
                        f"Instálalo con: sudo apt install {paquetes}")
                else:
                    _MOTORES[clave] = True
    estado = _MOTORES[clave]
    if isinstance(estado, Exception):
        raise estado


def ocr_imagen(ruta: Path, idioma: str):
    """Generador: entrega una línea de texto por cada línea detectada en la imagen."""
    import pytesseract
    from PIL import Image, ImageOps

    img = Image.open(ruta)
    img = ImageOps.exif_transpose(img)

    # Si tiene transparencia, se aplana sobre fondo blanco
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fondo = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(fondo, img)

    img = ImageOps.autocontrast(img.convert("L"))

    # Capturas pequeñas: escalar x2 mejora bastante la lectura
    if min(img.size) < 1000:
        img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)

    texto = pytesseract.image_to_string(img, lang=idioma_ocr(idioma))
    lineas = [ln.rstrip() for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        print("  Aviso: no se detectó texto en la imagen.", file=sys.stderr)
    yield from lineas


# ------------------------------- Proceso -----------------------------------

def nombre_libre(archivo: Path) -> str:
    """Nombre base libre para el .txt (en transcripciones/) y el original (en check/)."""
    base = archivo.stem
    candidato = base
    n = 1
    while ((CARPETA_TXT / f"{candidato}.txt").exists()
           or (CARPETA_CHECK / f"{candidato}{archivo.suffix}").exists()):
        candidato = f"{base}_{n}"
        n += 1
    return candidato


def procesar_archivo(archivo: Path, lineas) -> Path:
    """Escribe el .txt en transcripciones/ y mueve el original a check/.

    `lineas` es una función sin argumentos que devuelve un generador de líneas.
    """
    nombre = nombre_libre(archivo)
    destino_txt = CARPETA_TXT / f"{nombre}.txt"
    destino_original = CARPETA_CHECK / f"{nombre}{archivo.suffix}"

    try:
        with open(destino_txt, "w", encoding="utf-8") as f:
            for linea in lineas():
                f.write(linea + "\n")
                f.flush()
    except BaseException:
        # Falló o se interrumpió: se borra el .txt parcial y el original no se mueve.
        destino_txt.unlink(missing_ok=True)
        raise

    shutil.move(str(archivo), str(destino_original))
    return destino_txt


def main() -> int:
    args = parse_args()

    CARPETA_INPUT.mkdir(exist_ok=True)
    CARPETA_CHECK.mkdir(exist_ok=True)
    CARPETA_TXT.mkdir(exist_ok=True)

    try:
        archivos = listar_archivos(args)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if not archivos:
        print(f"No hay audios ni imágenes en {CARPETA_INPUT}", file=sys.stderr)
        return 0

    ok = 0
    fallidos: list[tuple[str, str]] = []
    fallo_audio = False

    for i, archivo in enumerate(archivos, 1):
        print(f"[{i}/{len(archivos)}] {archivo.name}", file=sys.stderr)
        imagen = es_imagen(archivo)
        try:
            if imagen:
                verificar_ocr(idioma_ocr(args.idioma))
                lineas = partial(ocr_imagen, archivo, args.idioma)
            else:
                modelo = obtener_whisper(args)
                lineas = partial(transcribir, modelo, archivo, args.idioma, args.timestamps)

            txt = procesar_archivo(archivo, lineas)
            print(f"  -> {CARPETA_TXT.name}/{txt.name} | original movido a {CARPETA_CHECK.name}/",
                  file=sys.stderr)
            ok += 1
        except KeyboardInterrupt:
            print("\nInterrumpido. El archivo actual no se movió.", file=sys.stderr)
            fallidos.append((archivo.name, "interrumpido"))
            break
        except Exception as e:
            print(f"\n  Error: {e}", file=sys.stderr)
            fallidos.append((archivo.name, str(e)))
            fallo_audio = fallo_audio or not imagen

    print(f"\nOK: {ok} | Fallidos: {len(fallidos)}", file=sys.stderr)
    for nombre, motivo in fallidos:
        print(f"  - {nombre}: {motivo}", file=sys.stderr)
    if fallo_audio:
        print("Si algún audio es de formato raro, instala ffmpeg e intenta de nuevo.",
              file=sys.stderr)

    return 1 if fallidos else 0


if __name__ == "__main__":
    sys.exit(main())