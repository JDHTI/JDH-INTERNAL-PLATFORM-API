---
name: "lista-precios"
description: "Actualizar las listas de precios de JDH (35P, Generales, Mayoristas, Salsamentarias) con el MCP postgres, Maestro Margen y las alzas desde Drive; calcula costo, precio nuevo, variación y el Plano de carga."
---

# Actualizar listas de precios JDH

Para Melis (analista, JDH). Responde en español, breve, sin emojis, máximo una pregunta por respuesta.

## Fuentes

1. **MCP postgres, esquema `consumo`** (solo lectura): `precios_lista_vigentes`, `precios_lista_historico`, `listas_precios`, `cotizaciones_proveedores`, `inventario_fecha` (referencia, bodega, costo_prom_unit_ins, existencia). Filtrar siempre por lista o referencia; no consultar `listas_precios` completa (resultado enorme). Si una consulta devuelve demasiado, se guarda en archivo y se lee con Python.
2. **Maestro Margen.xlsx** (carpeta Listas Precios conectada): márgenes por producto en la hoja `Margen Producto ` (columna Margen Market Apartadó para la 35P; trae excepciones por producto, por ejemplo dispensadoras Sancela al 10 %), márgenes por proveedor como respaldo, e impuestos al consumo por SKU (Nacional y Costa). La columna Estado de esa hoja es el estado del ítem en Siesa (Activo, Inactivo, Bloqueado).
3. **Alzas de precio, desde Drive** (conector Google Drive): archivo Acumulado_Alza_Precios, id `1_gMSBSE094F1wSnteaWl-w2_mXfsLSz6s1Z0lpKwatI`. Se descarga con `download_file_content` exportando como **xlsx** (el export CSV falla por tamaño); el resultado queda en un archivo local en base64. No usar el CSV publicado (viene truncado). Abastecimiento lo actualiza durante el día (el 27/10 agregó las alzas de Nutrimenti, 42 referencias cambiadas, casi todas bajas): cuando Melis avise que hay alzas nuevas, volver a descargarlo y recalcular todas las listas.

### Refresco diario del MCP

- Antes de calcular, confirmar que el MCP ya cargó el día: `select max(foto_at) from consumo.inventario_fecha` debe ser de hoy (carga hacia las 8:20 a. m.) y los precios de `precios_lista_vigentes` deben reflejar los Planos que Melis subió el día anterior (`max(vigente_desde)` y `fecha_actualizacion`). Si sigue la foto de ayer, esperar el MCP y no calcular con ella.
- Columnas de las vistas: `inventario_fecha` (referencia, bodega, costo_prom_unit_ins, existencia, cant_disponible, producto_activo, fecha_ultima_venta); `precios_lista_vigentes` (lista_precio_id, sku, unidad_medida_id, precio_sin_iva, impoconsumo_valor, vigente_desde).
- Si un precio de una lista de precios sale de una consulta con varias listas, filtrar por lista antes de comparar (error del 28/10: sin filtrar, el precio actual de cada lista salía de otra y aparecían cientos de Subió falsos).
- En consultas con `distinct on (referencia) ... order by ... desc`, agregar `nulls last` (si no, los costos nulos quedan primero y la referencia sale sin costo).
- Las consultas grandes se parten en máximo 4 bodegas o unas 4 listas; si no, el MCP responde "connection closed".

## Filtro obligatorio de cotizaciones

**Fuente de la cotización (instrucción de Melis, 28/10/2026): por ahora usar el Cotizaciones.xlsx descargado (exporte de Siesa, Solo vigentes), no la vista del MCP**, mientras TI revisa por qué el MCP trae datos erróneos. Ejemplo: 15379 Rollo Tapete Nomad Gris (3M): el MCP trae costo_bruto y precio_unitario 5.587.154 contra 2.640 en Siesa (misma fecha de activación 2021-10-14, mismo proveedor), un valor evidentemente malo que inflaría el precio. Cuando TI lo corrija, Melis avisará para volver al MCP.

Si se vuelve a leer `cotizaciones_proveedores` del MCP, se usa siempre:

- `es_vigente = true` (solo vigentes).
- `unidad_medida_id IN ('UNID','KL')` (unidad y kilo; en los datos la unidad se escribe UNID).
- `producto_activo = true` (estado del ítem activo).

Así se acerca al reporte "Consulta de cotizaciones" de Siesa (Solo vigentes, ítems activos). La vista trae todo el histórico (102.347 filas, 27.625 vigentes), por eso el filtro no es opcional.

Notas del filtro (comparación del 22/10/2026 contra el Cotizaciones.xlsx de Siesa):
- La U.M. se filtra en estricto UNID y KL. Existen 36 filas vigentes con "unid" en minúscula, todas de 2020; no se incluyen porque Siesa no las muestra como vigentes y casi nunca definen el costo (único caso: la 5480).
- Si no se filtra por U.M., entran filas de caja (C24, C6, etc.) y el costo mayor sale inflado.
- `producto_activo` puede venir en falso en el MCP para productos activos en Siesa (92 referencias el 22/10, varias con stock y ventas del día). Si una referencia con costo en el Cotizaciones.xlsx no aparece en el MCP, revisar este indicador antes de dejarla sin cotización.

```sql
select sku, proveedor, unidad_medida_id, fecha_activacion, costo_bruto, icui_tasa_pct, ibua_valor
from consumo.cotizaciones_proveedores
where es_vigente and producto_activo and unidad_medida_id in ('UNID','KL');
```

Advertencia: el MCP es una foto (carga hacia las 7:44 a. m.). Aun con el filtro, el 22/10/2026 quedaron 92 referencias con costo distinto frente a Siesa, con la misma U.M.: cotizaciones modificadas en el lugar (misma fecha de activación, otro costo; ej. 21050: MCP 178.147 vs Siesa 139.115; el 28/10, 15379) o filas vigentes de más (23207).

Una cotización puede cambiar entre un día y otro (ej. 15531, Ajover: 13.950 el 26/10 y 20.350 con activación 26/10 en el archivo del 27/10). Al refrescar, volver a descargar Cotizaciones.xlsx, Maestro Margen y el Acumulado de alzas, y marcar como NUEVO las variaciones que no estaban en el cálculo anterior (comparar con la Comparación de ayer: hoy Subió o Bajó y ayer Estable).

**Lactalis (proveedor 417) cotiza por sucursal** (regla de Melis, 27/10/2026; ella pidió no olvidarla): la sucursal 003 trae el costo de la regional Centro y la sucursal 001 el de las demás regionales. Para Lactalis, las listas Centro (003, 10U y 10S) usan solo cotizaciones de la sucursal 003 (columna Sucursal del Cotizaciones.xlsx); todas las demás listas (35P, 001, 002, 004, 005, 009, 1, las otras mayoristas y 10X y 10R de Salsamentarias) usan solo la sucursal 001. La lista 1 usa la sucursal 001 (confirmado por Melis el 27/10/2026). No filtrar por sucursal a otros proveedores. Aplicarla en TODO cálculo, también en faltantes y recálculos. Ejemplo: 1081 Crema de Leche Parmalat x1Lt, sucursal 003 = 13.104 (02/02) y sucursal 001 = 18.275 (20/10); tomar el máximo entre ambas sucursales sube mal el precio de Centro y hace que 5 referencias de la 1M salgan "Bajó" en el archivo de Melis aunque el precio ya esté bien. Ojo: el archivo de Melis ignora las cotizaciones nuevas de Lactalis en su Inventario[Costo Máximo] (ej. 1081, 7654, 7687, 8579, 7655 con cotización del 20/10).

## Costo

Costo máximo = el más alto entre:
- Cotización vigente: redondear(costo_bruto) × (1 + tasa ICUI/100) + IBUA, redondeado al entero. Si hay varias filas vigentes, tomar la mayor (con el filtro de sucursal de Lactalis de arriba).
- Inventario: Costo prom. unit. (ins) **de la bodega de la lista** (URA6 para la 35P). No tomar el máximo entre bodegas (salvo la lista 1, ver Generales).
- Costo Final de las alzas (ver abajo).

Redondear hacia arriba. Luego el archivo de la 35P multiplica por **1,004** (factor fijo, verificado en 805 de 805 referencias): costo = costo máximo × 1,004. Las Mayoristas NO llevan ese factor (ver su sección); las Salsamentarias sí.

### Costo Final desde el Acumulado de alzas

Replica el Power Query "Variación de Precios":
0. Las filas sin Fecha de Incremento se descartan: NO usar la Fecha de notificación como sustituto (regla confirmada por Melis el 27/10/2026).
1. Agrupar por Referencia y tomar la Fecha de Incremento más reciente.
2. En esa fecha, costo = Costo nuevo con Impuesto Saludable si existe, si no el costo sin impuesto saludable. Los textos tipo "$ 1.234,00" se convierten a número; los errores valen 0.
3. Si hay varias filas en esa fecha: si el costo mayor es más de 3 veces el menor, tomar el **menor** (costo por unidad o kilo; el mayor es por caja, caso Locería Colombiana, sin columna que los distinga). Si no, tomar el **mayor** (incrementos seguidos del mismo producto o diferencias de centavos).

## Precio

Precio = costo / (1 − margen ideal), redondeado hacia arriba. Ejemplo validado: 1.492 × 1,004 = 1.497,968; con margen 20 % da 1.872,46 y el precio es 1.873.

## Margen por lista

- Generales (1, 001–005, 009): Margen Lista 1 de Maestro Margen (Food Service).
- Mayoristas (1M, 10T, 10U, 11N, 10N, 1DR): Margen Mayorista de Maestro Margen.
- Salsamentarias (10X, 10R, 10S): Margen Salsamentarias.
- 35P Market: Margen Market Apartadó.
- Margen Masivo Food: no se usa y **no se modifica**.

## Variación

Compara el precio nuevo con el precio actual de la lista. Umbral fijo de 1 % en todas las listas: Subió o Bajó si supera ±1 %, si no Estable. Subió y Bajó llevan alerta Revisar.

## Diferencias de la lista Generales (verificado con la 001, 002, 003, 004, 005, 009 y 1)

- Margen: columna Margen Lista 1 de Maestro Margen (hoja `Margen Producto `). Se usó igual en todas las Generales.
- Universo de referencias: las filas de la hoja de la lista en Listas de Precios Generales.xlsx: `1 General ` (7.680 filas, sin columna Bodega), `001 General Antioquia`, `002 General Occidente`, `003 General Centro`, `004 General Costa` (3.675), `005 General Oriente` (2.601, solo BGA1), `009 General Barranquilla` (2.435; BAQ2 en 77 filas). Las referencias que el MCP ya trae en la lista pero no están en esa hoja (ej. 18 en la 002, 85 en la 003 el 27/10) no se evalúan; solo pueden ser candidatas a Faltantes si no tienen precio en el MCP. Al leer las hojas, convertir Referencia a entero (no 11290.0) para que cruce con el MCP. Las hojas cambian de un día a otro: volver a extraerlas del archivo del día.
- Bodegas de inventario por lista (Bodega 1 / Bodega 2, según la hoja Faltantes en Lista del archivo): 001 MDE1 / MDE3; 002 OCC1 / OCC16; 003 BTA9 / BTA8; 004 CTG1 / CTG15; 005 BGA1; 009 BAQ1 / BAQ2; lista 1: ver el punto siguiente. Regla del archivo: la Bodega 2 (MDE3, OCC16, BTA8, CTG15, BAQ2) aplica si la LINEA es CONGELADOS o REFRIGERADOS; si no, la Bodega 1. El archivo trae algunas excepciones puntuales en la columna Bodega (p. ej. MDE4 en la 001); respetar la que traiga la fila.
- **Lista 1 (General)**: no tiene bodega fija; el archivo toma el mayor costo entre las bodegas de su hoja Inventario (MAXIFS). Todo el inventario sale del MCP, nunca de la hoja Inventario del archivo. Bodegas que Melis definió el 27/10/2026 y confirmó mantener el 28/10/2026 (el costo es el más alto entre las bodegas principales y cavas, más las de solicitudes puntuales): MDE1, MDE3, MDE4, OCC1, OCC16, OCC14, BTA9, BTA8, BTA20, CTG1, CTG15, CTG14, BGA1, BGA11, BAQ1, BAQ2, BAQ10. NO van las de Urabá (APO1, APO8) ni las de Valledupar (VUP1, VUP9). Si solo se usaran las principales y cavas cambiarían unas 723 referencias; ella eligió mantener las 17. Consultar el MCP agregado por referencia (distinct on referencia, mayor costo redondeado con `nulls last`, suma de existencia en esas bodegas) para no traer filas por bodega. Ojo: los precios actuales de la lista 1 parecen calculados con más bodegas, por eso con estas 17 salen cientos de bajas (542 el 28/10) y algunas referencias sin costo; avisarlo a Melis al entregar. Faltantes de la lista 1: existencia en esas bodegas.
- El costo de inventario se redondea al entero antes de sacar el mayor (Siesa lo exporta entero); luego se redondea hacia arriba y se multiplica por 1,004.
- Precio = redondeo normal (no hacia arriba) de costo / (1 − margen).
- Referencias cuya descripción empieza por "Prom" y no tienen costo: costo 1.
- Precio actual: precio sin IVA vigente de la lista en el MCP (`precios_lista_vigentes`, `precio_sin_iva`).
- El archivo Listas de Precios Generales puede traer cotizaciones, alzas e inventario más viejos que el MCP, Drive y el Cotizaciones.xlsx del día; por eso no se valida exacto contra su caché. Validar casos puntuales.
- Consultas grandes: las fechas pueden venir como timestamp con zona (`zoneinfo`); al evaluar el archivo guardado pasar `zoneinfo` a eval y convertir a fecha.
- Variación mayor a 50 %: se marca para validar el costo en Siesa y se reporta aparte.
- Referencias sin registro de inventario en la bodega de la lista (ej. Locería Colombiana en la 001 y 003): el costo sale solo de cotización y alza, y las alzas suelen ser del 2 % o más; marcarlas con alerta para validar. No usar el costo de otra bodega sin que Melis lo confirme.

## Faltantes en lista

En cada revisión, además de las variaciones, listar las referencias que están en inventario y no están en la lista, para agregarlas. **No aplica a las listas Mayoristas ni a las Salsamentarias** (ver sus secciones): solo se calculan para 35P y las Generales. Una referencia es faltante solo si cumple las tres condiciones: Estado Activo en Maestro Margen, existencia ≥ 1 en la Bodega 1 o 2 de la lista (la que corresponde por LINEA; si solo tiene existencia en la otra, usar esa; 35P: URA6 con disponible > 0) y NO está en la hoja de la lista del archivo NI tiene precio en esa lista en `precios_lista_vigentes` del MCP. Solo cuenta el inventario de las bodegas de esa lista (si el inventario trae todas las bodegas, filtrarlo: el 28/10 sin filtrar salieron 1.600 falsos faltantes en la 002). El archivo de Melis se queda atrás del MCP: una referencia con precio vigente en el MCP ya está en la lista aunque falte en su hoja, y no se le sugiere como faltante (error del 27/10: se listaron 6 en la 003, 6 en la 002 y 5 en la 001 que ya tenían precio). Para cada una calcular costo máximo, margen, precio sugerido (misma fórmula de la lista) y observación: Revisar costo si el costo máximo es ≤ $1, o sin margen en Maestro. No pasan al Plano sin el OK de Melis.

## Mayoristas (1M, 10T, 10U, 11N, 1DR, 10N) — verificado el 27/10/2026

- Archivo: Listas de Precios Mayoristas.xlsx, hojas `1M Antioquia` (3.931 filas), `10T Occidente` (1.802), `10U Centro` (2.947), `11N Cartagena` (1.936), `1DR Barranquilla` (1.742) y `10N Oriente` (1.249). El universo son las filas de cada hoja; la bodega viene en la columna Bodega (1M MDE1/MDE3, 10T OCC1/OCC16, 10U BTA9/BTA8, 11N CTG1/CTG15, 1DR BAQ1/BAQ2, 10N BGA1).
- Margen: columna Margen Mayorista de Maestro Margen (hoja `Margen Producto `), no Margen Lista 1.
- Costo: igual que Generales (mayor entre inventario de la bodega redondeado al entero, cotización y alza) pero SIN el factor 1,004: el archivo no lo aplica (coincide con su costo en el 99,8 %; con 1,004 coincide casi en ninguno). Precio = redondeo normal de costo / (1 − margen). Umbral 1 %.
- Precio actual: `precios_lista_vigentes` con `trim(lista_precio_id)` en ('1M','10T','10U','11N','1DR','10N'). Impuesto: 11N usa Costa; las demás Nacional.
- **En Mayoristas NO se agregan referencias faltantes**: no calcular ni reportar Faltantes en lista para estas listas (en el Resumen va "No aplica"). El surtido de las mayoristas es limitado a propósito.

## Salsamentarias (10X, 10S, 10R) — verificado el 27/10/2026

- Archivo: Listas de Precios Salsamentarias.xlsx (carpeta Listas de Precios; pesa 27 MB, se trae con device_stage_files), hojas `10X Antioquia` (4.681 filas), `10S Centro` (2.896) y `10R Pereira` (1.856). Misma estructura que Mayoristas (columnas Referencia, Bodega, Costo, Precio). El universo son las filas de cada hoja.
- Bodegas: 10X MDE1 / MDE3, 10S BTA9 / BTA8, 10R OCC1 / OCC16 (la columna Bodega de la fila; Bodega 2 si la LINEA es CONGELADOS o REFRIGERADOS).
- Margen: columna Margen Salsamentarias de Maestro Margen.
- Costo: igual que las otras listas, CON el factor 1,004 (coincide con el costo del archivo). Precio = redondeo HACIA ARRIBA de costo / (1 − margen) (coincide al 100 % con el precio del archivo cuando el costo es igual; el redondeo normal no coincide). Umbral 1 %.
- Lactalis: 10S es Centro (sucursal 003); 10X y 10R usan la sucursal 001.
- Precio actual: `precios_lista_vigentes` con `trim(lista_precio_id)` en ('10X','10S','10R'). Impuesto: Nacional (el archivo trae la hoja Alerta Impuestos con la tabla de licores; todas las salsamentarias son nacionales).
- **En Salsamentarias NO se agregan referencias faltantes** (regla de Melis, 27/10/2026): no calcular ni reportar Faltantes en lista para estas listas (en el Resumen va "No aplica"). Ignorar la hoja Faltantes en Lista de su archivo.

## 35P: descuento de Unilever

Por pedido de Melis (27/10/2026) las referencias de Unilever en la 35P llevan un descuento del 15 % sobre el precio (van en el Plano con precio = precio calculado × 0,85). Como el MCP ya trae ese precio, el cálculo normal las muestra como Subió (cerca de +17,6 %); no es un cambio nuevo: anotarlo en las notas del Excel y no volver a cargarlas.

## Impuesto al consumo

Se toma de Maestro Margen por SKU. Las listas 004 y 11N (Cartagena) usan Costa; todas las demás (incluida 1DR y las Salsamentarias) usan Nacional. Antes de cargar el Plano, comparar con IMPTOLIC de Siesa (Precios por ítem) y corregir diferencias.

## Reglas

- Referencias con costo ≤ $1 (Revisar): no se agregan.
- **Referencias con precio 0 o sin precio calculable: NUNCA van en el Plano** (Siesa rechaza la carga con el error "El precio debe ser mayor que cero", registro tipo 0126; regla de Melis, 28/10/2026). Antes de entregar, verificar que el precio mínimo del Plano sea mayor que cero.
- Filas pintadas en el archivo: ya atendidas, se excluyen.
- Sin margen o sin cotización: quedan pendientes y se reportan.
- Antes de modificar un precio existente con alza, avisar a Felipe.
- Referencias con producto inactivo en el MCP (`producto_activo = false` en `inventario_fecha`): no se entregan para revisión; se listan aparte para confirmar el estado real en Siesa (el indicador del MCP ya falló antes). Las activas sin ventas en años y con 1 a 6 unidades se marcan como posibles descontinuadas.
- **El Plano no se genera hasta que Melis dé el OK**, y solo con las referencias que ella apruebe (ella indica cuáles se excluyen).

### Observaciones de Melis por referencia (válidas para futuras revisiones)

Melis devuelve el Excel de revisión recortado a las filas que van al Plano y con una columna Observación en las que NO se modifican. Esas filas se omiten del Plano y se tienen en cuenta en las revisiones siguientes: si la referencia vuelve a salir como Subió o Bajó por la misma causa, no se le propone el cambio (marcarla como "omitida por observación de Melis" en la revisión). Vigentes al 28/10/2026:
- 11454 Salsa de Tomate Aderezos Bx1000g (listas 001 y 10X), 12780 y 12841 (001): el costo subió por traslado desde otra ciudad; no subir el precio.
- 2237 Macarrón Doria (009) y 24257 Palmito Amapuri (10N): no modificar, el proveedor entregó con diferencia de precio.
- 28087 Crema de Leche Natulac (1M): no modificar, se está evacuando próxima a vencer con ese costo.
- 22858 Mayonesa Frugal (10U y 10N), 23197 Mayonesa Aderezos (11N, 1DR, 10N) y 4375 Mayonesa Aderezos (11N): no modificar, se tiene en cuenta el costo promedio.
- 13314 y 23634 (001): ya las eliminó de la lista.
Si Melis agrega observaciones nuevas, sumarlas a esta lista.

## Entrega de la revisión: un solo archivo

La revisión de todas las listas del día se entrega en UN SOLO Excel (ej. Revision_listas_de_precios_AAAA-MM-DD.xlsx), no un archivo por lista ni archivos sueltos de faltantes o motivos. Hojas: Resumen (por lista: referencias evaluadas, Subió, Bajó, con alerta, variación > 50 %, faltantes reales; "No aplica" en Mayoristas y Salsamentarias), Variaciones (todas las listas, con proveedor, costos, fuente del costo máximo, existencia, última venta y alerta), Con alerta (validar) y Faltantes en lista. No incluir una hoja con todas las referencias (las Estables no se revisan y hace el archivo pesado). Al agregar otra lista el mismo día, regenerar ese mismo archivo con la lista nueva y entregar solo ese.

Formato: en todas las hojas, las celdas que dicen "Subió" van en rojo (fuente roja en negrita y fondo rojo claro); siempre hay una columna "Estado del ítem (Siesa)" (columna Estado de Maestro Margen: Activo, Inactivo, Bloqueado) junto a "Activo en MCP", en rojo si no es Activo; la columna Lista como texto ('001', no 1).

## Pasos

1. Confirmar que el MCP cargó el día (ver Refresco diario) y leer inventario y precios vigentes; la cotización sale del Cotizaciones.xlsx del día (ver Fuente de la cotización).
2. Descargar el Acumulado de alzas desde Drive y calcular el Costo Final.
3. Calcular costo máximo, precio nuevo, variación y alerta por lista.
4. Entregar el Excel único de revisión (ver sección anterior): variaciones con proveedor y qué manda el costo (alza del proveedor con su fecha, cotización vigente más alta, o costo de inventario más alto que la cotización) frente al costo con el que se calculó el precio actual. Marcar las de más de 50 %, las que tienen novedad, las NUEVO frente a ayer y las inactivas. Reportar faltantes (sin margen o sin cotización) e incluir los Faltantes en lista donde apliquen. Aquí se detiene: no generar el Plano.
5. Melis revisa y responde cuáles se modifican, cuáles se excluyen (columna Observación) y cuáles se validan con Felipe.
6. Solo con su OK, generar el Plano de carga (ver sección siguiente), con las referencias aprobadas.

Pasos manuales de Melis: Generic Transfer a .txt, importar en Siesa, avisar a Felipe y enviar el correo.

## Plano de carga

Se genera solo después del OK de Melis (ver Reglas). Un solo Plano por jornada, con todas las listas revisadas y aprobadas ese día (por ejemplo 35P, 001, 002, 003, 004, 005, 009 y 1): las filas de cada lista se escriben una debajo de otra en la misma hoja. Si se revisa otra lista después, se agrega al mismo archivo, salvo que Melis ya haya subido ese Plano a Siesa: entonces se hace un Plano NUEVO solo con lo nuevo (ej. Plano Listas de Precios Mayoristas 2026-10-27.xlsx), y el anterior se deja como lo subió.

Partir de una copia de `Plano Listas de Precios.xlsx` (carpeta Listas Precios) y llenar sus dos hojas:

- **Precios** (columnas LISTA DE PRECIOS, REFERENCIA, AAAAMMDD, UNIDAD_MEDIDA, PRECIO DE LISTA): solo las referencias aprobadas por Melis entre las que superan el umbral de 1 % (Subió o Bajó), más las faltantes en lista que ella apruebe agregar. Las Estables no se modifican, aunque el precio calculado difiera en pesos, salvo que Melis las pida expresamente. Si Melis devuelve el Excel de revisión recortado: van las filas que quedan sin observación (con precio nuevo mayor que 0 y costo > $1) y las faltantes que dejó. Si Melis pega una lista de referencias para el Plano (con la lista a la izquierda de cada referencia): no incluir las que ya tienen en el MCP el mismo precio calculado (no cambian nada) y decirle cuántas fueron; no incluir las de costo ≤ $1 (avisar); avisar las que superen 50 % de variación; las que no estén en la hoja ni en faltantes se calculan con la misma fórmula. Lista = código de la lista (ej. 35P, 001, 002); referencia como número; fecha como número entero AAAAMMDD (la fecha que indique Melis; si no la dijo, usar la fecha actual en hora de Bogotá, `TZ=America/Bogota date +%Y%m%d`, y decirlo en la respuesta); unidad UNID, o `KL  ` con espacios hasta 4 caracteres; precio entero mayor que cero.
- **Impuestos en valor** (… LLAVE IMPUESTO, VALOR IMPUESTO): solo referencias de licor/vino del plano, con el valor de Nacional o Costa de la hoja Impuestos de Maestro Margen. Llave usada antes: 0011 para vino (grupo 0503) y 0024 para ginebra/licor (grupo 0509); confirmar la llave con Melis. Si no hay licores, dejar la hoja vacía (borrar también las filas de ejemplo de la plantilla).

Reglas del archivo:
- Fuente Calibri Light tamaño 10 en todas las celdas de las dos hojas (pedido de Melis el 27/10/2026); aplicarlo al final sin alterar el formato `0` de la columna C.
- Antes de entregar, revisar impuestos: la hoja Impuestos de Listas de Precios Generales.xlsx (Sku, Nacional, Costa; 12 productos), IMPTOLIC de Maestro Margen y el impoconsumo del MCP; si ninguna referencia del Plano aparece, la hoja Impuestos en valor queda vacía y se le avisa a Melis. Si Melis pregunta "¿impuestos bien?", repetir esa comprobación para el Plano entregado.
- Borrar las filas de datos de la plantilla antes de escribir (no dejar filas de listas anteriores), en las dos hojas.
- Ningún precio en 0 (ver Reglas): comprobar `min(precio) > 0` y que no haya duplicados lista + referencia.
- Fecha AAAAMMDD (error repetido dos veces, se ve como 573721020): la plantilla tiene el formato de fecha `yyyymmd` en la columna C, a nivel de columna y en el encabezado, en las dos hojas. Poner formato General en las celdas no basta. En openpyxl, en cada hoja: `ws.column_dimensions['C'].number_format = '0'` y `cell.number_format = '0'` en cada fila de datos de la columna C, con el valor como entero (20261027, no texto ni fecha). Guardar y verificar releyendo el archivo: el valor de C2 debe ser un entero y su formato `0`, y el styles.xml de la columna no debe asignar `yyyymmd` a las filas de datos. No entregar el Plano sin esta comprobación.

## Control de validación

Cotejar 3 a 5 referencias contra la hoja 35P Market de Lista de Precios Market.xlsx antes de entregar.

## En la plataforma interna (cotizador)

El módulo Cotizador (`app/modules/quotations`, documentado en `docs/cotizador.md`) aplica estas reglas a la lista de cada cliente. Están centralizadas en `app/modules/quotations/domain/pricing.py`: si cambia una regla de esta skill (bodegas por lista, factor 1,004, redondeo, Lactalis, umbral de cotizaciones), se cambia allí y en `docs/cotizador.md`.

- Costo: el mayor entre la cotización vigente de `consumo.cotizaciones_proveedores` (UNID/KL, ítem activo, Lactalis por sucursal) y `consumo.inventario_fecha` de la bodega de la lista; hacia arriba y × 1,004 salvo Mayoristas. El DWH ya cuadra con Siesa en cotizaciones desde el 28/10/2026 (verificado por el agente del DWH el 03/11/2026); las cotizaciones mayores que 3 veces la mediana del último costo se descartan y se muestran para revisar en Siesa.
- Las alzas del Acumulado (Drive) no están en el DWH: el cotizador no las incluye.
- Impuesto de licores: el de la lista en Siesa; si no, el del Maestro Margen por regional (Costa para el CO 004).
- Las referencias inactivas se muestran para reemplazarlas y nunca salen en el Plano; los precios van en pesos enteros y mayores que cero.
- Flujo: el asesor confirma la lista, BI la valida y solo entonces se generan el Excel y el plano. La lista 1 no se actualiza: el cliente recibe una lista nueva (registro 0112) que se asigna a mano en Siesa.