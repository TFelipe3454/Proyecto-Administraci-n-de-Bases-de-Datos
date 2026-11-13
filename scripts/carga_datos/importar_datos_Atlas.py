import json
from pymongo import MongoClient

# 1. Conexión a MongoDB (funcionará directo desde tu red local o VPN institucional)
uri = "mongodb://is109502:Urz1RTbguW0EsJP@orion.javeriana.edu.co:27017/is109502_db?authSource=is109502_db"

print("--> Conectando a MongoDB...")
client = MongoClient(uri)

db = client['is109502_db']
coleccion = db['edificaciones_gba']

# 2. Cargar el archivo GeoJSON consolidado
nombre_archivo = 'datos_gba_pdet_colombia_completo.geojson'
print(f"--> Leyendo el archivo '{nombre_archivo}'...")

with open(nombre_archivo, 'r', encoding='utf-8') as f:
    geojson_data = json.load(f)

# 3. Inserción por lotes (batch) de los 135k+ registros
if geojson_data.get("type") == "FeatureCollection":
    features = geojson_data.get("features", [])
    
    if features:
        print(f"--> Iniciando inserción de {len(features)} registros...")
        
        tamano_lote = 5000
        total_insertados = 0
        
        for i in range(0, len(features), tamano_lote):
            lote = features[i:i + tamano_lote]
            resultado = coleccion.insert_many(lote)
            total_insertados += len(resultado.inserted_ids)
            print(f"   -> Progreso: {total_insertados}/{len(features)} registros insertados...")
            
        print(f"¡Éxito! Se insertaron {total_insertados} documentos en 'edificaciones_gba'.")
    else:
        print("El archivo GeoJSON no contiene elementos.")
else:
    print("El archivo no tiene un formato 'FeatureCollection' válido.")

# 4. Crear el índice geoespacial 2dsphere
print("--> Creando índice geoespacial '2dsphere'...")
coleccion.create_index([("geometria", "2dsphere")])
print("¡Índice 2dsphere creado exitosamente!")