"""
Respaldo / verificación / restauración de la BD SQLite.

Uso (desde la raíz del proyecto):
    python -m scripts.respaldo_bd respaldar [--bd data/colmena.db] [--destino backups] [--etiqueta manual]
    python -m scripts.respaldo_bd verificar backups/colmena_....db
    python -m scripts.respaldo_bd restaurar backups/colmena_....db [--bd data/colmena.db]

Detén la API antes de restaurar. Ver docs/OPERACION.md.
"""
import argparse
import sys

from sqlalchemy.engine import make_url

from app.config import obtener_config
from app.services.respaldo import RespaldoError, respaldar, restaurar, verificar_respaldo


def _ruta_bd_por_defecto() -> str:
    url = make_url(obtener_config().database_url)
    if url.get_backend_name() != "sqlite":
        raise SystemExit("Este script solo soporta SQLite")
    return url.database


def main(argv=None) -> int:
    cfg = obtener_config()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="accion", required=True)

    r = sub.add_parser("respaldar")
    r.add_argument("--bd", default=None)
    r.add_argument("--destino", default=cfg.colmena_backup_dir)
    r.add_argument("--etiqueta", default="manual")

    v = sub.add_parser("verificar")
    v.add_argument("archivo")

    s = sub.add_parser("restaurar")
    s.add_argument("archivo")
    s.add_argument("--bd", default=None)

    a = p.parse_args(argv)
    try:
        if a.accion == "respaldar":
            res = respaldar(a.bd or _ruta_bd_por_defecto(), a.destino, a.etiqueta)
            print(f"Respaldo: {res.archivo}\nManifiesto: {res.manifiesto}\nSHA-256: {res.sha256}\nBytes: {res.bytes}")
        elif a.accion == "verificar":
            datos = verificar_respaldo(a.archivo)
            print(f"OK: {datos['archivo']} ({datos['bytes']} bytes, creado {datos['creado_utc']})")
        elif a.accion == "restaurar":
            previa = restaurar(a.archivo, a.bd or _ruta_bd_por_defecto())
            print("Restaurado." + (f" Copia de la BD anterior: {previa}" if previa else ""))
    except RespaldoError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
