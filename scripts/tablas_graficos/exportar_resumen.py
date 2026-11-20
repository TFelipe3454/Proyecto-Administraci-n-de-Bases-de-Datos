import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
SALIDA = Path(__file__).resolve().parents[2] / "resultados"
SALIDA.mkdir(exist_ok=True)

db = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)[os.environ["MONGO_DB"]]

# 1
if len(sys.argv) > 1:
    corrida = sys.argv[1]
else:
    ultima = db.ejecuciones_analisis.find_one({"estado": "completado"}, sort=[("fecha_ejecucion", -1)])
    corrida = ultima["corrida_id"]
ejecs = list(db.ejecuciones_analisis.find({"corrida_id": corrida}))
ids = [e["_id"] for e in ejecs]
print(f"Corrida: {corrida} | ejecuciones: {len(ids)}")

# 2. Resultados por municipio y fuente
res = pd.DataFrame(list(db.resumen_municipal.find({"ejecucion_id": {"$in": ids}}, {"_id": 0})))
res = res.drop(columns=["ejecucion_id"])

# 3. Datos del municipio 
mpios = pd.DataFrame(list(db.municipios_pdet.find({}, {"_id": 0, "geometria": 0})))
mpios = mpios[["codigo_dane", "nombre", "departamento_nombre", "subregion_pdet"]]
df = res.merge(mpios, on="codigo_dane", how="left")
df["corrida_id"] = corrida
df = df.sort_values(["fuente", "codigo_dane"])
df.to_csv(SALIDA / "resumen_municipal.csv", index=False, encoding="utf-8-sig")
# 4. Parametros
params = [{"fuente": e["fuente"], "area_min": e.get("area_min"), "confianza_min": e.get("confianza_min"),
           "fecha": e["fecha_ejecucion"].isoformat()} for e in ejecs]
(SALIDA / "parametros_corrida.json").write_text(
    json.dumps({"corrida_id": corrida, "ejecuciones": params}, indent=2), encoding="utf-8")

# 5. Comprobaciones
print(f"Filas: {len(df)} | municipios sin nombre: {df['nombre'].isna().sum()}")
print(df.groupby("fuente")[["n_edificaciones", "n_aptas", "area_total_m2", "area_apta_m2"]].sum().round(0))
