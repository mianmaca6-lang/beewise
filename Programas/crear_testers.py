# Requiere el esquema al día: ejecutar antes `python -m scripts.preparar_bd`.
from app.database import SessionLocal
from app.migraciones import exigir_esquema_actualizado
from app.models import Tester

exigir_esquema_actualizado()
db = SessionLocal()
for i in range(1, 21):
    token_name = f"tester{i:02d}"
    if not db.query(Tester).filter(Tester.token == token_name).first():
        db.add(Tester(token=token_name))
db.commit()
print("🐝 ¡20 Testers creados exitosamente!")
