/*
Estas consultas se realizan con el fin de entender la naturaleza y principales caracteristicas
    de los datos ya cargados a MongoDB en las cuatro colecciones existentes hasta el momento.
    Con estas consultas se prodecera a realizar el analisis exploratorio de los datos para
    la entrega de la semana 3 del proyecto.
*/

// Consulta 1: Datos generales: total de datos cargados, indices y tamaño de indices

["municipios_pdet","edificaciones_google","edificaciones_gba1","lotes_carga"].forEach(c =>
  print(c, db[c].estimatedDocumentCount()));
printjson(db.edificaciones_google.getIndexes());
printjson(db.edificaciones_google.stats().indexSizes);
printjson(db.edificaciones_gba1.getIndexes());
printjson(db.edificaciones_gba1.stats().indexSizes);

//Consulta 2: Auditoria general de lotes_carga

db.lotes_carga.aggregate([
  { $group: { _id: "$fuente", archivos: { $sum: 1 }, leidos: { $sum: "$n_leidos" },
      insertados: { $sum: "$n_insertados" }, fuera_pdet: { $sum: "$n_fuera_pdet" },
      rechazados: { $sum: "$n_rechazados" } } },
  { $addFields: { pct_en_pdet: { $round: [{ $multiply: [{ $divide: ["$insertados","$leidos"] }, 100] }, 2] } } }
]);
db.lotes_carga.find({}, { _id:0, fuente:1, archivo:1, estado:1, n_leidos:1, n_insertados:1,
  n_fuera_pdet:1, n_rechazados:1, crs_declarado:1, crs_detectado:1 }).sort({ fuente:1, archivo:1 });

db.edificaciones_google.aggregate([
  { $group: { _id: "$lote_id", docs: { $sum: 1 } } },
  { $lookup: { from: "lotes_carga", localField: "_id", foreignField: "_id", as: "l" } },
  { $unwind: "$l" },
  { $project: { _id:0, archivo:"$l.archivo", docs:1, registrado:"$l.n_insertados",
      coincide: { $eq: ["$docs","$l.n_insertados"] } } }
]);

db.edificaciones_gba1.aggregate([
  { $group: { _id: "$lote_id", docs: { $sum: 1 } } },
  { $lookup: { from: "lotes_carga", localField: "_id", foreignField: "_id", as: "l" } },
  { $unwind: "$l" },
  { $project: { _id:0, archivo:"$l.archivo", docs:1, registrado:"$l.n_insertados",
      coincide: { $eq: ["$docs","$l.n_insertados"] } } }
]);

//Consulta 3: Estadisticas principales de ambas fuentes de datos
["edificaciones_google","edificaciones_gba1"].forEach(c => {
  print("=== " + c);
  printjson(db[c].aggregate([
    { $group: { _id: "$codigo_dane", n: { $sum: 1 }, area: { $sum: "$area_m2" } } },
    { $group: { _id: null,
        municipios_con_datos: { $sum: 1 },
        edif_por_municipio_media: { $avg: "$n" },
        edif_por_municipio_mediana: { $median: { input: "$n", method: "approximate" } },
        edif_por_municipio_desv: { $stdDevPop: "$n" },
        edif_por_municipio_min: { $min: "$n" },
        edif_por_municipio_max: { $max: "$n" },
        area_por_municipio_media_m2: { $avg: "$area" } } },
    { $addFields: { coef_variacion: { $round: [{ $divide: ["$edif_por_municipio_desv", "$edif_por_municipio_media"] }, 2] } } }
  ], { allowDiskUse: true }).toArray());
});

//Consulta 4: Percentiles y distribucion por rango de area de ambas fuentes
["edificaciones_google","edificaciones_gba1"].forEach(c => {
  print("=== " + c);
  printjson(db[c].aggregate([{ $group:{ _id:null, percentiles:{ $percentile:{
    input:"$area_m2", p:[0.05,0.25,0.5,0.75,0.95], method:"approximate" } } } }]).toArray());
  printjson(db[c].aggregate([{ $bucket:{ groupBy:"$area_m2",
    boundaries:[0,25,50,100,150,250,500,1000,1e6], default:"fuera_de_rango",
    output:{ edificaciones:{ $sum:1 } } } }]).toArray());
});

//Consulta 5: Top 10 de municipios por numero de edificaciones
//Para Google buildings
db.edificaciones_google.aggregate([
  { $group:{ _id:"$codigo_dane", edificaciones:{ $sum:1 }, area_total_m2:{ $sum:"$area_m2" }, area_media_m2:{ $avg:"$area_m2" } } },
  { $sort:{ edificaciones:-1 } }, { $limit:10 },
  { $lookup:{ from:"municipios_pdet", localField:"_id", foreignField:"codigo_dane", as:"m" } },
  { $project:{ _id:0, codigo_dane:"$_id", municipio:{ $first:"$m.nombre" }, edificaciones:1, area_total_m2:1, area_media_m2:1 } }
]);

//Para Atlas buildings
db.edificaciones_gba1.aggregate([
  { $group:{ _id:"$codigo_dane", edificaciones:{ $sum:1 }, area_total_m2:{ $sum:"$area_m2" }, area_media_m2:{ $avg:"$area_m2" } } },
  { $sort:{ edificaciones:-1 } }, { $limit:10 },
  { $lookup:{ from:"municipios_pdet", localField:"_id", foreignField:"codigo_dane", as:"m" } },
  { $project:{ _id:0, codigo_dane:"$_id", municipio:{ $first:"$m.nombre" }, edificaciones:1, area_total_m2:1, area_media_m2:1 } }
]);

//Consulta 6: Municipios sin edificaciones en ambas fuentes de datos
["edificaciones_google","edificaciones_gba1"].forEach(c => {
  print("Sin edificaciones en " + c + ":");
  printjson(db.municipios_pdet.aggregate([
    { $lookup:{ from:c, localField:"codigo_dane", foreignField:"codigo_dane", pipeline:[{ $limit:1 }, { $project:{ _id:1 } }], as:"e" } },
    { $match:{ e:{ $size:0 } } },
    { $project:{ _id:0, codigo_dane:1, nombre:1, departamento_nombre:1 } }
  ]).toArray());
});

//Consulta 7: Comparacion de edificaciones en ambas fuentes por municipio
db.edificaciones_google.aggregate([
  { $group:{ _id:"$codigo_dane", n:{ $sum:1 }, area:{ $sum:"$area_m2" } } },
  { $project:{ fuente:{ $literal:"google" }, n:1, area:1 } },
  { $unionWith:{ coll:"edificaciones_gba1", pipeline:[
      { $group:{ _id:"$codigo_dane", n:{ $sum:1 }, area:{ $sum:"$area_m2" } } },
      { $project:{ fuente:{ $literal:"gba" }, n:1, area:1 } } ] } },
  { $group:{ _id:"$_id",
      n_google:{ $sum:{ $cond:[{ $eq:["$fuente","google"] },"$n",0] } },
      n_gba:{ $sum:{ $cond:[{ $eq:["$fuente","gba"] },"$n",0] } },
      area_google_m2:{ $sum:{ $cond:[{ $eq:["$fuente","google"] },"$area",0] } },
      area_gba_m2:{ $sum:{ $cond:[{ $eq:["$fuente","gba"] },"$area",0] } } } },
  { $lookup:{ from:"municipios_pdet", localField:"_id", foreignField:"codigo_dane", as:"m" } },
  { $project:{ _id:0, codigo_dane:"$_id", municipio:{ $first:"$m.nombre" }, departamento:{ $first:"$m.departamento_nombre" },
      n_google:1, n_gba:1, area_google_m2:1, area_gba_m2:1,
      razon_gba_sobre_google:{ $round:[{ $divide:["$n_gba",{ $max:["$n_google",1] }] },2] } } },
  { $sort:{ n_google:-1 } }
], { allowDiskUse:true });

//Consulta 8: Resumen de edificaciones por departamento y subregion PDET
const porMunicipio = [
  { $group: { _id: "$codigo_dane", n: { $sum: 1 }, area: { $sum: "$area_m2" } } },
  { $lookup: { from: "municipios_pdet", localField: "_id", foreignField: "codigo_dane", as: "m" } },
  { $unwind: "$m" }
];
["edificaciones_google","edificaciones_gba1"].forEach(c => {
  ["departamento_nombre","subregion_pdet"].forEach(campo => {
    print("=== " + c + " por " + campo);
    printjson(db[c].aggregate([
      ...porMunicipio,
      { $group: { _id: "$m." + campo, municipios: { $sum: 1 }, edificaciones: { $sum: "$n" }, area_total_m2: { $sum: "$area" } } },
      { $addFields: {
          edif_por_municipio: { $round: [{ $divide: ["$edificaciones", "$municipios"] }, 0] },
          area_media_m2: { $round: [{ $divide: ["$area_total_m2", "$edificaciones"] }, 2] } } },
      { $setWindowFields: { output: { total: { $sum: "$edificaciones", window: { documents: ["unbounded", "unbounded"] } } } } },
      { $addFields: { pct_edificaciones: { $round: [{ $multiply: [{ $divide: ["$edificaciones", "$total"] }, 100] }, 2] } } },
      { $project: { total: 0 } },
      { $sort: { edificaciones: -1 } }
    ], { allowDiskUse: true }).toArray());
  });
});

//Consulta 9: Calculo de la correlacion de Pearson, que tanto se parecen google y atlas
const comparacion = [
  { $group: { _id: "$codigo_dane", n: { $sum: 1 }, area: { $sum: "$area_m2" } } },
  { $project: { fuente: { $literal: "google" }, n: 1, area: 1 } },
  { $unionWith: { coll: "edificaciones_gba1", pipeline: [
      { $group: { _id: "$codigo_dane", n: { $sum: 1 }, area: { $sum: "$area_m2" } } },
      { $project: { fuente: { $literal: "gba" }, n: 1, area: 1 } } ] } },
  { $group: { _id: "$_id",
      n_google: { $sum: { $cond: [{ $eq: ["$fuente", "google"] }, "$n", 0] } },
      n_gba: { $sum: { $cond: [{ $eq: ["$fuente", "gba"] }, "$n", 0] } } } }
];
db.edificaciones_google.aggregate([
  ...comparacion,
  { $group: { _id: null, k: { $sum: 1 }, sx: { $sum: "$n_google" }, sy: { $sum: "$n_gba" },
      sxy: { $sum: { $multiply: ["$n_google", "$n_gba"] } },
      sxx: { $sum: { $multiply: ["$n_google", "$n_google"] } },
      syy: { $sum: { $multiply: ["$n_gba", "$n_gba"] } } } },
  { $project: { _id: 0, municipios: "$k", total_google: "$sx", total_gba: "$sy",
      razon_total_gba_sobre_google: { $round: [{ $divide: ["$sy", "$sx"] }, 3] },
      correlacion_pearson: { $round: [{ $divide: [
        { $subtract: [{ $multiply: ["$k", "$sxy"] }, { $multiply: ["$sx", "$sy"] }] },
        { $sqrt: { $multiply: [
          { $subtract: [{ $multiply: ["$k", "$sxx"] }, { $multiply: ["$sx", "$sx"] }] },
          { $subtract: [{ $multiply: ["$k", "$syy"] }, { $multiply: ["$sy", "$sy"] }] } ] } } ] }, 3] } } }
], { allowDiskUse: true });