#!/usr/bin/env python3
"""
Conteo y area de techos aptos por municipio PDET.

Para cada fuente (Google Open Buildings y GlobalBuildingAtlas) ejecuta UNA agregacion en MongoDB
que agrupa por codigo_dane y calcula, en un solo recorrido:
  n_edificaciones, area_total_m2      -> todas las edificaciones
  n_aptas, area_apta_m2               -> edificaciones que cumplen los umbrales

El resultado se guarda en `resumen_municipal` (un documento por municipio, fuente y ejecucion,
incluyendo ceros para municipios sin edificaciones) y la ejecucion queda en `ejecuciones_analisis`
con sus parametros.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient, ReplaceOne

FUENTES = {
    "google": {"coleccion": "edificaciones_google", "usa_confianza": True},
    "gba": {"coleccion": "edificaciones_gba1", "usa_confianza": False},
}


def version_codigo():
    """Hash del commit del repositorio (con sufijo -modificado si hay cambios sin confirmar)."""
    try:
        aqui = os.path.dirname(os.path.abspath(__file__))
        h = subprocess.check_output(["git", "-C", aqui, "rev-parse", "HEAD"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        sucio = subprocess.call(["git", "-C", aqui, "diff", "--quiet", "HEAD"],
                                stderr=subprocess.DEVNULL) != 0
        return h + ("-modificado" if sucio else "")
    except Exception:
        return "desconocida"


def construir_pipeline(fuente, area_min, confianza_min):
    """Un solo $group: totales sin filtro y totales de edificaciones aptas."""
    condiciones = [{"$gt": ["$area_m2", area_min]}]
    if FUENTES[fuente]["usa_confianza"]:
        condiciones.append({"$gt": ["$confianza", confianza_min]})
    apta = {"$and": condiciones}
    return [{"$group": {
        "_id": "$codigo_dane",
        "n_edificaciones": {"$sum": 1},
        "area_total_m2": {"$sum": "$area_m2"},
        "n_aptas": {"$sum": {"$cond": [apta, 1, 0]}},
        "area_apta_m2": {"$sum": {"$cond": [apta, "$area_m2", 0]}},
    }}]


def ejecutar_fuente(db, fuente, args, corrida_id, municipios):
    cfg = FUENTES[fuente]
    coleccion = cfg["coleccion"]
    pipeline = construir_pipeline(fuente, args.area_min, args.confianza_min)
    inicio = datetime.now(timezone.utc)
    t0 = time.time()
    print(f"[{fuente}] agregando sobre {coleccion} ...", flush=True)
    por_municipio = {d["_id"]: d for d in db[coleccion].aggregate(pipeline, allowDiskUse=True)}
    duracion = time.time() - t0

    ejec_id = None if args.dry_run else db.ejecuciones_analisis.insert_one({}).inserted_id
    filas, sin_datos = [], []
    for cod in municipios:
        d = por_municipio.get(cod)
        if d is None:
            sin_datos.append(cod)
        filas.append({
            "ejecucion_id": ejec_id, "codigo_dane": cod, "fuente": fuente,
            "n_edificaciones": int(d["n_edificaciones"]) if d else 0,
            "n_aptas": int(d["n_aptas"]) if d else 0,
            "area_total_m2": float(d["area_total_m2"]) if d else 0.0,
            "area_apta_m2": float(d["area_apta_m2"]) if d else 0.0,
        })
    conjunto = set(municipios)
    huerfanos = [c for c in por_municipio if c not in conjunto]

    totales = {
        "n_edificaciones": sum(f["n_edificaciones"] for f in filas),
        "n_aptas": sum(f["n_aptas"] for f in filas),
        "area_total_m2": sum(f["area_total_m2"] for f in filas),
        "area_apta_m2": sum(f["area_apta_m2"] for f in filas),
        "municipios_con_datos": len(municipios) - len(sin_datos),
        "municipios_sin_datos": sorted(sin_datos),
    }

    # Validacion: lo agregado debe coincidir con el total de la coleccion de origen
    total_coleccion = db[coleccion].count_documents({})
    total_agregado = totales["n_edificaciones"] + sum(por_municipio[c]["n_edificaciones"] for c in huerfanos)
    coincide = total_agregado == total_coleccion
    print(f"[{fuente}] validacion: agregado={total_agregado:,} coleccion={total_coleccion:,} "
          f"-> {'OK' if coincide else 'NO COINCIDE'}")
    if huerfanos:
        print(f"[{fuente}] aviso: {len(huerfanos)} codigo_dane fuera de municipios_pdet: {huerfanos[:5]}")

    print(f"[{fuente}] edificaciones={totales['n_edificaciones']:,} aptas={totales['n_aptas']:,} "
          f"area_total={totales['area_total_m2']:,.0f} m2 area_apta={totales['area_apta_m2']:,.0f} m2 "
          f"({duracion:.1f} s)")
    if sin_datos:
        print(f"[{fuente}] municipios sin edificaciones: {sorted(sin_datos)}")

    if args.dry_run:
        return totales

    db.resumen_municipal.bulk_write([
        ReplaceOne({"ejecucion_id": ejec_id, "codigo_dane": f["codigo_dane"], "fuente": fuente}, f, upsert=True)
        for f in filas])
    db.ejecuciones_analisis.replace_one({"_id": ejec_id}, {
        "_id": ejec_id, "corrida_id": corrida_id, "fuente": fuente, "coleccion_origen": coleccion,
        "fecha_ejecucion": inicio, "fecha_fin": datetime.now(timezone.utc),
        "duracion_s": round(duracion, 2),
        "area_min": args.area_min,
        "confianza_min": args.confianza_min if cfg["usa_confianza"] else None,
        "parametros": {"campo_area": "area_m2", "operador_area": ">", "operador_confianza": ">"
                       if cfg["usa_confianza"] else None},
        "pipeline_json": json.dumps(pipeline),
        "version_codigo": version_codigo(),
        "version_python": platform.python_version(),
        "version_mongodb": db.client.server_info().get("version"),
        "validacion_total_coincide": coincide,
        "totales": totales, "estado": "completado" if coincide else "revisar",
    })
    return totales


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI"))
    p.add_argument("--database", default=os.environ.get("MONGO_DB", "is109502_db"))
    p.add_argument("--fuente", choices=["google", "gba", "ambas"], default="ambas")
    p.add_argument("--area-min", type=float, default=60.0, help="area minima en m2, defecto 60")
    p.add_argument("--confianza-min", type=float, default=0.70, help="confianza minima, solo Google")
    p.add_argument("--dry-run", action="store_true", help="calcula e imprime sin escribir en la base")
    args = p.parse_args()
    if not args.mongo_uri:
        sys.exit("Falta --mongo-uri o la variable de entorno MONGO_URI")

    db = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=15000)[args.database]
    municipios = sorted(d["codigo_dane"] for d in db.municipios_pdet.find({}, {"codigo_dane": 1}))
    print(f"Base {args.database}: {len(municipios)} municipios PDET | area > {args.area_min} m2 | "
          f"confianza (Google) > {args.confianza_min}")
    if not args.dry_run:
        db.resumen_municipal.create_index(
            [("ejecucion_id", ASCENDING), ("codigo_dane", ASCENDING), ("fuente", ASCENDING)], unique=True)
        db.resumen_municipal.create_index([("fuente", ASCENDING), ("codigo_dane", ASCENDING)])
        db.ejecuciones_analisis.create_index([("fuente", ASCENDING), ("fecha_ejecucion", ASCENDING)])

    corrida_id = str(uuid.uuid4())
    fuentes = list(FUENTES) if args.fuente == "ambas" else [args.fuente]
    for f in fuentes:
        ejecutar_fuente(db, f, args, corrida_id, municipios)
    print(f"Listo. corrida_id={corrida_id}")


if __name__ == "__main__":
    main()