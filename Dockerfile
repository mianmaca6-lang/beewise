FROM python:3.11-slim

# Evitar archivos .pyc y buffer de salida
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Crear las carpetas de persistencia para que existan al iniciar
RUN mkdir -p /app/data /app/"Base Datos"/input /app/"Base Datos"/check

# Copiar el resto del código (incluyendo la carpeta Programas)
COPY . /app/

# Exponer el puerto para FastAPI
EXPOSE 8001

# Ejecutar el servidor
# Primero respalda y migra la BD (Alembic); la API se niega a arrancar si el esquema no está en head.
CMD ["sh", "-c", "python -m scripts.preparar_bd && exec uvicorn app.main:app --host 0.0.0.0 --port 8001 --proxy-headers"]