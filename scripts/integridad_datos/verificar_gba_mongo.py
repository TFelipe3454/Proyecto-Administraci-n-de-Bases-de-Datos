import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
NOMBRE_COL = sys.argv[1] if len(sys.argv) > 1 else "edificaciones_gba1"
SALIDA = Path(__file__).resolve().parents[2] / "docs" / "evidencias" / "punto1_gba_verificacion.txt"
SALIDA.parent.mkdir(parents=True, exist_ok=True)
archivo = open(SALIDA, "w", encoding="utf-8")


def log(texto=""):
    print(texto)
    archivo.write(texto + "\n")


client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)
db = client[os.environ["MONGO_DB"]]
col = db[NOMBRE_COL]

log(f"VERIFICACION {NOMBRE_COL} - {datetime.now():%Y-%m-%d %H:%M}")

# 1. Total
total = col.count_documents({})
log(f"\n1. Total de documentos: {total:,}")

# 2. Cobertura de municipios PDET
gba = set(col.distinct("codigo_dane"))
pdet = set(db.municipios_pdet.distinct("codigo_dane"))
log(f"\n2. Municipios con edificios en GBA: {len(gba)} de {len(pdet)} PDET")
log(f"   PDET sin edificios: {sorted(pdet - gba)}")
log(f"   Codigos de GBA que no son PDET: {sorted(gba - pdet)}")

# 3. Nulos o inexistentes (None captura ambos casos)
log("\n3. Nulos o inexistentes por campo:")
for campo in ["codigo_dane", "geometria", "centroide", "area_m2", "lote_id", "altura_m"]:
    log(f"   {campo}: {col.count_documents({campo: None}):,}")

# 4. Tipos de geometría
log("\n4. Tipos de geometria:")
for r in col.aggregate([{"$group": {"_id": {"geometria": "$geometria.type", "centroide": "$centroide.type"},
                                    "n": {"$sum": 1}}}], allowDiskUse=True):
    log(f"   {r['_id']}: {r['n']:,}")

# 5. Rango de coordenadas de los centroides
r = next(col.aggregate([{"$group": {
    "_id": None,
    "lon_min": {"$min": {"$arrayElemAt": ["$centroide.coordinates", 0]}},
    "lon_max": {"$max": {"$arrayElemAt": ["$centroide.coordinates", 0]}},
    "lat_min": {"$min": {"$arrayElemAt": ["$centroide.coordinates", 1]}},
    "lat_max": {"$max": {"$arrayElemAt": ["$centroide.coordinates", 1]}}}}], allowDiskUse=True))
log(f"\n5. Rango de centroides: lon [{r['lon_min']:.4f}, {r['lon_max']:.4f}], "
    f"lat [{r['lat_min']:.4f}, {r['lat_max']:.4f}]")
fuera = col.count_documents({"$or": [
    {"centroide.coordinates.0": {"$lt": -80}}, {"centroide.coordinates.0": {"$gt": -66}},
    {"centroide.coordinates.1": {"$lt": -5}}, {"centroide.coordinates.1": {"$gt": 14}}]})
log(f"   Centroides fuera de la caja de Colombia: {fuera}")

# 6. Estadísticas de área y altura
r = next(col.aggregate([{"$group": {"_id": None, "min": {"$min": "$area_m2"},
                                    "max": {"$max": "$area_m2"}, "prom": {"$avg": "$area_m2"}}}],
                       allowDiskUse=True))
log(f"\n6. area_m2: min {r['min']:.2f}, max {r['max']:.2f}, promedio {r['prom']:.2f}")
log(f"   area_m2 <= 0: {col.count_documents({'area_m2': {'$lte': 0}}):,}")
con_altura = col.count_documents({"altura_m": {"$ne": None}})
log(f"   Documentos con altura_m informada: {con_altura:,} de {total:,}")

# 7. Edificios por municipio
por_mpio = list(col.aggregate([{"$group": {"_id": "$codigo_dane", "n": {"$sum": 1}}},
                               {"$sort": {"n": -1}}], allowDiskUse=True))
log(f"\n7. Edificios por municipio (suma = {sum(x['n'] for x in por_mpio):,})")
log("   Los 5 con mas: " + ", ".join(f"{x['_id']}={x['n']:,}" for x in por_mpio[:5]))
log("   Los 5 con menos: " + ", ".join(f"{x['_id']}={x['n']:,}" for x in por_mpio[-5:]))

# 8. Conciliación con la bitácora de lotes
log("\n8. Bitacora lotes_carga:")
tot_ins = 0
for r in db.lotes_carga.aggregate([{"$match": {"fuente": "globalbuildingatlas"}}, {"$group": {
        "_id": "$estado", "lotes": {"$sum": 1}, "leidos": {"$sum": "$n_leidos"},
        "insertados": {"$sum": "$n_insertados"}, "rechazados": {"$sum": "$n_rechazados"}}}]):
    tot_ins += r["insertados"]
    log(f"   estado={r['_id']}: {r['lotes']} lotes, leídos {r['leidos']:,}, "
        f"insertados {r['insertados']:,}, rechazados {r['rechazados']}")
log(f"   Insertados segun bitacora: {tot_ins:,} | Documentos en la colección: {total:,} | "
    f"{'COINCIDEN' if tot_ins == total else 'NO COINCIDEN'}")
lotes_en_col = col.distinct("lote_id")
lotes_bitacora = set(db.lotes_carga.distinct("_id", {"fuente": "globalbuildingatlas"}))
log(f"   Lotes distintos en la coleccion: {len(lotes_en_col)} | "
    f"lotes de la colección que no estan en la bitacora: {len(set(lotes_en_col) - lotes_bitacora)}")