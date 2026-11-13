#!/usr/bin/env sh
# Compila, prueba y despliega LibroPersonal en TESTNET.
# NO EJECUTADO todavía (requiere stellar-cli >= 25.2 y autorización para usar Testnet).
#
# Requisitos:
#   - stellar-cli >= 25.2  (soroban-sdk 29 exige `stellar contract build`)
#   - Identidades creadas en el keychain local de stellar-cli (las llaves NO se guardan en el repo):
#       stellar keys generate colmena-admin    --network testnet --fund   # llave fría: rotar escritor
#       stellar keys generate colmena-servicio --network testnet --fund   # cuenta de servicio del backend
#
# Uso (desde contratos/):  sh scripts/desplegar_testnet.sh
set -eu

RED=testnet
ADMIN=colmena-admin
ESCRITOR=colmena-servicio

cargo test -p libro_personal
stellar contract build --package libro_personal
WASM=target/wasm32v1-none/release/libro_personal.wasm
ls -la "$WASM"

ID=$(stellar contract deploy \
  --wasm "$WASM" \
  --source "$ADMIN" \
  --network "$RED" \
  -- \
  --admin "$(stellar keys address $ADMIN)" \
  --escritor "$(stellar keys address $ESCRITOR)")

echo "Contrato LibroPersonal desplegado: $ID"
echo "Agregar a .env:  SOROBAN_CONTRATO_LIBRO_PERSONAL=$ID"

# Prueba de humo on-chain (valores de prueba, sin datos personales):
S=0101010101010101010101010101010101010101010101010101010101010101
H=0202020202020202020202020202020202020202020202020202020202020202
stellar contract invoke --id "$ID" --source "$ESCRITOR" --network "$RED" -- registrar --seudonimo "$S" --hash "$H"
stellar contract invoke --id "$ID" --network "$RED" --send=no -- verificar --seudonimo "$S" --hash "$H"

# Exportar la llave del servicio a un archivo para el backend (permisos restringidos, fuera del repo):
#   mkdir -p ../secrets && stellar keys show colmena-servicio > ../secrets/soroban_servicio.txt && chmod 600 ../secrets/soroban_servicio.txt
