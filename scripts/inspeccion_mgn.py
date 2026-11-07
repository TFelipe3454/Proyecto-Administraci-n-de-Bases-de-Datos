
import geopandas as gpd

archivo = r"../mgn/MGN_ADM_MPIO_GRAFICO.geojson"

gdf = gpd.read_file(archivo)

print("\n--- INFORMACIÓN GENERAL ---")
print("Total de registros:", len(gdf))
print("Sistema de coordenadas:", gdf.crs)
print("Tipos de geometría:")
print(gdf.geometry.geom_type.value_counts())

print("\n--- CAMPOS DISPONIBLES ---")
print(gdf.columns.tolist())

print("\n--- PRIMEROS 5 MUNICIPIOS ---")
print(gdf.drop(columns="geometry").head().to_string())

print("\n--- GEOMETRÍAS NULAS ---")
print(gdf.geometry.isna().sum())

print("\n--- GEOMETRÍAS INVÁLIDAS ---")
print((~gdf.geometry.is_valid).sum())
