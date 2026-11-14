import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
SALIDA = Path(__file__).resolve().parents[2] / "docs" / "evidencias" / "punto1_gba_verificacion.txt"
SALIDA.parent.mkdir(parents=True, exist_ok=True)
archivo = open(SALIDA, "w", encoding="utf-8")


def log(texto=""):
    print(texto)
    archivo.write(texto + "\n")


client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)
db = client[os.environ["MONGO_DB"]]
col = db["edificaciones_gba"]

log(f"VERIFICACIÓN edificaciones_gba - {datetime.now():%Y-%m-%d %H:%M}")

# 1. Total
total = col.count_documents({})
log(f"\n1. Total de documentos: {total:,}")
log("   (si la carga sigue en curso, este número crece: repetir el script más tarde)")

# 2. Qué campos existen realmente en los documentos
log("\n2. Documentos que contienen cada campo:")
for campo in ["geometria", "centroide", "geometry", "type", "properties.codigo_dane",
              "properties.lote_id", "properties.area", "properties.centroide"]:
    log(f"   {campo}: {col.count_documents({campo: {'$exists': True}}):,}")

# 3. Nulos o inexistentes
log("\n3. Nulos o inexistentes:")
for campo in ["geometry", "properties.codigo_dane", "properties.area", "properties.centroide"]:
    log(f"   {campo}: {col.count_documents({campo: None}):,}")

# 4. Cobertura de municipios PDET
gba = set(col.distinct("properties.codigo_dane"))
pdet = set(db.municipios_pdet.distinct("codigo_dane"))
log(f"\n4. Municipios con edificios en GBA: {len(gba)} de {len(pdet)} PDET")
log(f"   PDET sin edificios: {sorted(pdet - gba)}")
log(f"   Códigos de GBA que no son PDET: {sorted(gba - pdet)}")

archivo.close()