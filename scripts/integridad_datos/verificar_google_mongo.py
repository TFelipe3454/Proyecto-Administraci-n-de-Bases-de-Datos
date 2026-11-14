import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
SALIDA = Path(__file__).resolve().parents[2] / "docs" / "evidencias" / "punto1_google_verificacion.txt"
SALIDA.parent.mkdir(parents=True, exist_ok=True)
archivo = open(SALIDA, "w", encoding="utf-8")


def log(texto=""):
    print(texto)
    archivo.write(texto + "\n")


client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)
db = client[os.environ["MONGO_DB"]]
col = db["edificaciones_google"]

log(f"VERIFICACION edificaciones_google - {datetime.now():%Y-%m-%d %H:%M}")

# 1. Total
total = col.count_documents({})
log(f"\n1. Total de documentos: {total:,}")

# 2. Cobertura de municipios PDET
google = set(col.distinct("codigo_dane"))
pdet = set(db.municipios_pdet.distinct("codigo_dane"))
log(f"\n2. Municipios con edificios en Google: {len(google)} de {len(pdet)} PDET")
log(f"   PDET sin edificios: {sorted(pdet - google)}")
log(f"   Codigos de Google que no son PDET: {sorted(google - pdet)}")

# 3. Nulos o inexistentes (None captura ambos casos)
log("\n3. Nulos o inexistentes por campo:")
for campo in ["codigo_dane", "geometria", "centroide", "area_m2", "area_fuente_m2", "confianza"]:
    log(f"   {campo}: {col.count_documents({campo: None})}")

# 4. Tipos de geometría
log("\n4. Tipos de geometria:")
for r in col.aggregate([{"$group": {"_id": {"geometria": "$geometria.type", "centroide": "$centroide.type"},
                                    "n": {"$sum": 1}}}], allowDiskUse=True):
    log(f"   {r['_id']}: {r['n']:,}")

# 5. Rango de coordenadas de los centroides (toda la colección)
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

# 6. Estadísticas de área y confianza
r = next(col.aggregate([{"$group": {
    "_id": None,
    "area_min": {"$min": "$area_m2"}, "area_max": {"$max": "$area_m2"}, "area_prom": {"$avg": "$area_m2"},
    "conf_min": {"$min": "$confianza"}, "conf_max": {"$max": "$confianza"}, "conf_prom": {"$avg": "$confianza"}}}],
    allowDiskUse=True))
log(f"\n6. area_m2: min {r['area_min']:.2f}, max {r['area_max']:.2f}, promedio {r['area_prom']:.2f}")
log(f"   area_m2 <= 0: {col.count_documents({'area_m2': {'$lte': 0}})}")
log(f"   confianza: min {r['conf_min']:.3f}, max {r['conf_max']:.3f}, promedio {r['conf_prom']:.3f}")

# 7. Edificios por municipio
por_mpio = list(col.aggregate([{"$group": {"_id": "$codigo_dane", "n": {"$sum": 1}}},
                               {"$sort": {"n": -1}}], allowDiskUse=True))
log(f"\n7. Edificios por municipio (suma = {sum(x['n'] for x in por_mpio):,})")
log("   Los 5 con mas: " + ", ".join(f"{x['_id']}={x['n']:,}" for x in por_mpio[:5]))
log("   Los 5 con menos: " + ", ".join(f"{x['_id']}={x['n']:,}" for x in por_mpio[-5:]))

# 8. Conciliación con la bitácora de lotes
log("\n8. Bitacora lotes_carga (fuente google):")
tot_ins = 0
for r in db.lotes_carga.aggregate([{"$match": {"fuente": "google"}}, {"$group": {
        "_id": "$estado", "lotes": {"$sum": 1}, "leidos": {"$sum": "$n_leidos"},
        "insertados": {"$sum": "$n_insertados"}, "rechazados": {"$sum": "$n_rechazados"},
        "fuera_pdet": {"$sum": "$n_fuera_pdet"}}}]):
    tot_ins += r["insertados"]
    log(f"   estado={r['_id']}: {r['lotes']} lotes, leidos {r['leidos']:,}, insertados {r['insertados']:,}, "
        f"rechazados {r['rechazados']}, fuera de PDET {r['fuera_pdet']:,}")
log(f"   Insertados segun bitacora: {tot_ins:,} | Documentos en la colección: {total:,} | "
    f"{'COINCIDEN' if tot_ins == total else 'NO COINCIDEN'}")

# 9. Índices
log("\n9. Índices:")
for ix in col.list_indexes():
    log(f"   {ix['name']}: {dict(ix['key'])}")

archivo.close()