
import geopandas as gpd
import pandas as pd
import json
from shapely.geometry import mapping

# Rutas de archivos
archivo_mgn = r"../mgn/MGN_ADM_MPIO_GRAFICO.geojson"
archivo_excel = r"../mgn/MunicipiosPDET.xlsx"
archivo_salida = r"../mgn/municipios_pdet.geojson"

# 1. Leer datos del DANE
mgn = gpd.read_file(archivo_mgn)

mgn["mpio_cdpmp"] = (
    mgn["mpio_cdpmp"].astype(str).str.zfill(5)
)

# 2. Leer Excel
pdet = pd.read_excel(archivo_excel)

# Limpiar nombres de columnas
pdet.columns = pdet.columns.str.strip()

# 3. Normalizar códigos DANE
pdet["Código DANE Municipio"] = (
    pdet["Código DANE Municipio"]
    .astype(str)
    .str.replace(r"\.0$", "", regex=True)
    .str.strip()
    .str.zfill(5)
)

# 4. Verificar cantidad de municipios
print("Filas en Excel:", len(pdet))
print(
    "Municipios PDET únicos:",
    pdet["Código DANE Municipio"].nunique()
)

# 5. Cruzar Excel con geometrías del DANE
resultado = mgn.merge(
    pdet,
    left_on="mpio_cdpmp",
    right_on="Código DANE Municipio",
    how="inner"
)

# 6. Verificar integración
print("\n--- RESULTADOS ---")
print("Municipios encontrados:", len(resultado))
print(
    "Códigos únicos:",
    resultado["mpio_cdpmp"].nunique()
)

codigos_excel = set(pdet["Código DANE Municipio"])
codigos_dane = set(resultado["mpio_cdpmp"])

faltantes = codigos_excel - codigos_dane

print("Municipios sin geometría:", len(faltantes))

if faltantes:
    print("Códigos faltantes:", sorted(faltantes))

# 7. Validar integridad antes de exportar
if (
    resultado["mpio_cdpmp"].nunique() != 170
    or len(resultado) != 170
    or faltantes
):
    raise ValueError(
        "La integración no coincide con los 170 municipios PDET."
    )

# 8. Preparar estructura para MongoDB
resultado = resultado.rename(columns={
    "mpio_cdpmp": "codigo_dane",
    "mpio_cnmbr": "nombre",
    "dpto_ccdgo": "departamento_codigo",
    "dpto_cnmbr": "departamento_nombre",
    "Subregión PDET": "subregion_pdet"
})

resultado = resultado[
    [
        "codigo_dane",
        "nombre",
        "departamento_codigo",
        "departamento_nombre",
        "subregion_pdet",
        "geometry"
    ]
]

# 9. Exportar GeoJSON
resultado.to_file(
    archivo_salida,
    driver="GeoJSON"
)

print("\nArchivo generado:", archivo_salida)
print("Integración completada correctamente.")

# Generar documentos individuales para MongoDB
documentos = []

for _, fila in resultado.iterrows():
    documento = {
        "codigo_dane": str(fila["codigo_dane"]),
        "nombre": fila["nombre"],
        "departamento_codigo": str(fila["departamento_codigo"]),
        "departamento_nombre": fila["departamento_nombre"],
        "subregion_pdet": fila["subregion_pdet"],
        "geometria": mapping(fila.geometry)
    }

    documentos.append(documento)

# Guardar como arreglo JSON
archivo_mongodb = r"../mgn/municipios_pdet_mongodb.json"

with open(archivo_mongodb, "w", encoding="utf-8") as archivo:
    json.dump(documentos, archivo, ensure_ascii=False)

print("\nArchivo para MongoDB generado:", archivo_mongodb)
print("Documentos preparados:", len(documentos))
