"""
Firma local de un socio. Uso:
  SOCIO_SECRET=S... [STELLAR_NETWORK=testnet] python scripts/REVISAR_stellar_firmar.py <xdr_base>
Imprime el XDR firmado. El secreto solo vive en memoria.
"""
import os
import sys

from stellar_sdk import Keypair, Network, TransactionEnvelope


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: stellar_firmar.py <xdr_base>", file=sys.stderr)
        return 1
    secreto = os.getenv("SOCIO_SECRET")
    if not secreto:
        print("Falta SOCIO_SECRET", file=sys.stderr)
        return 1
    red = os.getenv("STELLAR_NETWORK", "testnet").strip().lower()
    if red not in ("testnet", "public"):
        print("STELLAR_NETWORK debe ser testnet o public", file=sys.stderr)
        return 1
    passphrase = Network.PUBLIC_NETWORK_PASSPHRASE if red == "public" else Network.TESTNET_NETWORK_PASSPHRASE
    try:
        env = TransactionEnvelope.from_xdr(sys.argv[1], passphrase)
        env.sign(Keypair.from_secret(secreto))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(env.to_xdr())
    return 0


if __name__ == "__main__":
    sys.exit(main())
