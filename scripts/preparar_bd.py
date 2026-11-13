"""
Prepara la base de datos: respaldo previo + Alembic (stamp de baseline si la BD
es anterior a Alembic) + upgrade head. Es idempotente: si ya está en head no hace nada.

Uso (desde la raíz del proyecto):
    python -m scripts.preparar_bd
"""
import json
import sys

from app.logging_seguro import configurar_logs
from app.migraciones import EsquemaError, preparar_bd


def main() -> int:
    configurar_logs()
    try:
        informe = preparar_bd()
    except EsquemaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
