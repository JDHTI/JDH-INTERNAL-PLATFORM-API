# Condiciones especiales (documentadas, no aplicadas)

La tabla **Condiciones Especiales** del libro «1. MAESTRO APOYOS PROVEEDORES.xlsx» tiene márgenes mínimos
autorizados por proveedor, referencia, cliente, regional, unidad de negocio y canal. Algunas no se
pueden automatizar (aplican a todos los clientes, a toda una regional o dependen de criterios que el
pedido no trae), así que **el [validador de pedidos](validador-pedidos.md) no las aplica por ahora**. Siguen
cargadas en el [maestro de apoyos](apoyos-proveedores.md) como `excepcion_margen` con origen
`Condiciones Especiales`, se pueden consultar y editar allí, y este documento es una copia de lo que había
el 13 de octubre de 2026 (120 filas del libro, cargadas del archivo del 9 de octubre).

Para activarlas basta con dejar de excluir ese origen en
`app/modules/order_validation/infrastructure/agreements.py` (`SPECIAL_CONDITIONS_SOURCE`); las reglas que
el validador ya sabe evaluar (`margen_minimo`) las compararían contra el margen del pedido.

## Resumen

| | Filas |
| --- | --- |
| Total | 120 |
| Aplican a todos los clientes | 89 |
| Aplican a todas las referencias del proveedor | 55 |
| Con inconsistencias (en revisión) | 21 |
| Vencidas | 6 |
| Con margen mínimo negativo | 3 |
| Proveedores comerciales distintos | 20 |

## Detalle

«Todos» o «Todas» significa que el campo no restringe la condición. La columna *Fila* es la fila de la
tabla del libro (`source_key` = `Condiciones Especiales:<fila>#<n>`; una fila con varias regionales o
referencias se expande en varias condiciones).

| Proveedor | Cliente | Referencia | Margen mínimo | Regional | Unidad de negocio | Canal | Vigencia | Estado | Fila |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8 - TEAM FOODS | Todos | Todas | 13.00 % | Todas | RETAIL, MASIVO | Todos | — a — | vigente | 48 |
| 8 - TEAM FOODS | Todos | Todas | 11.50 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-11-18 a 2026-01-31 | vencida | 38 |
| 8 - TEAM FOODS | GIRALDO MARTINEZ MARIA ALEJANDRA (1089599443) | Todas | 13.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-10-05 a — | vigente | 107 |
| 8 - TEAM FOODS | Todos | 21050 - Aceite Fry Basico CF x20Lt |  | ANTIOQUIA | FOOD SERVICE | Todos | — a — | en revisión | 81 |
| 8 - TEAM FOODS | Todos | 21349 - Aceite Fry Oleico x20Lt |  | ANTIOQUIA | FOOD SERVICE | Todos | — a — | en revisión | 82 |
| 8 - TEAM FOODS | Todos | 9951 - Aceite Super Fry x20Lt |  | Todas | FOOD SERVICE | Todos | — a — | en revisión | 83 |
| 8 - TEAM FOODS | Todos | 9951 - Aceite Super Fry x20Lt |  | Todas | FOOD SERVICE | Todos | — a — | en revisión | 80 |
| 70 - BRINSA S.A. | Todos | 21281 - Sal Vital Refisal Saco x25kg | 12.39 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-12-01 a — | en revisión | 85 |
| 70 - BRINSA S.A. | Todos | 4860 - Sal Refisal Inst Saco x 25Kg | 12.39 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-12-01 a — | en revisión | 85 |
| 143 - CRISTAR TABLETOP SAS | Todos | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | — a — | vigente | 66 |
| 143 - CRISTAR TABLETOP SAS | MERA EL DORADO SAS (901009488) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 143 - CRISTAR TABLETOP SAS | MERA MEDELLIN S.A.S (901230769) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 233 - FAMILIA SANCELA | Todos | Todas | 11.77 % | Todas | Todas | MAYORISTAS Y DISTRIBUIDORES | — a — | vigente | 39 |
| 233 - FAMILIA SANCELA | Todos | Todas | 15.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-07-02 a — | vigente | 69 |
| 233 - FAMILIA SANCELA | SALAZAR GOMEZ JAIME (10482266) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 12 |
| 233 - FAMILIA SANCELA | LONDOÑO DIAZ VICTOR EDUARDO (1058845372) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 13 |
| 233 - FAMILIA SANCELA | ORDOÑEZ ARISTIZABAL JULIAN CAMILO (1083865922) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 33 |
| 233 - FAMILIA SANCELA | ORDOÑEZ ARISTIZABAL JULIAN CAMILO (1083865922) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 32 |
| 233 - FAMILIA SANCELA | BUSTAMANTE PEREZ MARIA NIDIA (25231897) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 14 |
| 233 - FAMILIA SANCELA | HERRERA HURTADO ANGIE LORENA (38665113) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 34 |
| 233 - FAMILIA SANCELA | LONDOÑO GOMEZ CARLOS ALBERTO (75003689) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 15 |
| 233 - FAMILIA SANCELA | ALADDIN HOTEL & CASINO S.A.S. (805007280) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 27 |
| 233 - FAMILIA SANCELA | CENTROS RECREACIONALES Y SEDES HABITACIONALES DIFAB (820005201) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 35 |
| 233 - FAMILIA SANCELA | OPERMOT S.A.S. (900180584) | Todas | 13.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 54 |
| 233 - FAMILIA SANCELA | BLUEFIELDS FINANCIAL COLOMBIA (900466196) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 28 |
| 233 - FAMILIA SANCELA | HOTELES DEL OTUN SOCIEDAD POR ACCIONES SIMPLIFICADA (900729995) | Todas | 12.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 42 |
| 233 - FAMILIA SANCELA | QUESERA Y SALSAMENTARIA VALERIA S.A.S (900742714) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 16 |
| 233 - FAMILIA SANCELA | ORGANIZACION INTERNACIONAL JIMENEZ RAMIREZ S.A.S (900763897) | Todas | 13.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 55 |
| 233 - FAMILIA SANCELA | DISTRIBUIDORA ROJAS Y REYES S.A.S. (901119433) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 36 |
| 233 - FAMILIA SANCELA | LA AUTOMATICA EVENTOS S A S (901277677) | Todas | 16.00 % | CENTRO | FOOD SERVICE | Todos | 2026-09-21 a 2026-12-31 | vigente | 102 |
| 233 - FAMILIA SANCELA | LIMPIASEO DISTRIBUCIONES CALI S.A.S. (901285074) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 17 |
| 233 - FAMILIA SANCELA | QUESERA Y SALSAMENTARIA JY S.A.S. (901575277) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 18 |
| 233 - FAMILIA SANCELA | QUESERA CHIMINANGOS S.A.S. (901800321) | Todas | 10.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 19 |
| 233 - FAMILIA SANCELA | QUESERA Y SALSAMENTARIA CAMPOS S.A.S. (901965687) | Todas | 11.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-06-03 a — | vigente | 37 |
| 233 - FAMILIA SANCELA | Todos | 16616 - Serv. Caf Blancax200u 72061-200639 FSC-M |  | COSTA | FOOD SERVICE | Todos | 2026-09-03 a — | en revisión | 99 |
| 233 - FAMILIA SANCELA | Todos | 18736 - Rollo L20 Limpiamax H.S x550mt 3118 | 6.40 % | CENTRO | FOOD SERVICE | Todos | 2026-05-07 a 2026-12-31 | en revisión | 90 |
| 233 - FAMILIA SANCELA | Todos | 21914 - Toalla Scott Blanc Hs 3x2x177mt 9692 | 6.40 % | CENTRO | FOOD SERVICE | Todos | 2026-05-07 a 2026-12-31 | en revisión | 90 |
| 233 - FAMILIA SANCELA | Todos | 22940 - Serv. Normal26x26x100uFSC-M72257-200115 | 6.40 % | CENTRO | FOOD SERVICE | Todos | 2026-05-07 a 2026-12-31 | en revisión | 90 |
| 258 - GRIFFITH COLOMBIA S.A. | Todos | 15319 - Mayonesa Esp Zafran x12kg 685-542-104 | 11.00 % | ORIENTE | Todas | Todos | — a — | vigente | 30 |
| 258 - GRIFFITH COLOMBIA S.A. | Todos | 7602 - Mayonesa Esp Zafran x 3600g 685-542-102 | 11.00 % | ORIENTE | Todas | Todos | — a — | vigente | 30 |
| 295 - INDUSTRIAS ESTRA S.A. | Todos | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | — a — | vigente | 66 |
| 295 - INDUSTRIAS ESTRA S.A. | MERA EL DORADO SAS (901009488) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 295 - INDUSTRIAS ESTRA S.A. | MERA MEDELLIN S.A.S (901230769) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 352 - LATEXPORT S.A. | Todos | 9020 - Guante Vinilo Trans TM C6 x100u | 8.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-11-25 a — | vigente | 11 |
| 352 - LATEXPORT S.A. | Todos | 9021 - Guante Vinilo Transp TL C6 x100u | 8.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-11-25 a — | vigente | 11 |
| 396 - NESTLE DE COLOMBIA S.A. | Todos | Todas | 8.80 % | Todas | Todas | MAYORISTAS Y DISTRIBUIDORES | — a — | vigente | 21 |
| 399 - UNILEVER ANDINA S.A | MONTECITO SAS (901489698) | Todas | 15.00 % | CENTRO | FOOD SERVICE | Todos | 2026-09-29 a 2026-12-30 | vigente | 105 |
| 546 - DICORP S.A. | Todos | Todas | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-01 a — | vigente | 61 |
| 546 - DICORP S.A. | Todos | Todas | 10.00 % | Todas | FOOD SERVICE | Todos | 2026-03-04 a — | vigente | 25 |
| 546 - DICORP S.A. | Todos | Todas | 8.00 % | COSTA | FOOD SERVICE | Todos | 2025-01-01 a — | vigente | 10 |
| 546 - DICORP S.A. | Todos | 16552 - Arroz Castellano Jazmin x 500g | 10.00 % | CENTRO | FOOD SERVICE | Todos | 2026-03-14 a — | vigente | 26 |
| 546 - DICORP S.A. | Todos | 18905 - Arroz Diana Premium x1kg | 15.00 % | CENTRO | FOOD SERVICE | Todos | 2026-03-15 a — | vigente | 62 |
| 546 - DICORP S.A. | Todos | 18905 - Arroz Diana Premium x1kg | 13.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-13 a — | vigente | 51 |
| 546 - DICORP S.A. | Todos | 18984 - Arroz Castellano Sushi x500g | 10.00 % | CENTRO | FOOD SERVICE | Todos | 2026-03-13 a — | vigente | 24 |
| 546 - DICORP S.A. | Todos | 25136 - Arroz Diana Vitamor x5kg | 14.50 % | CENTRO | FOOD SERVICE | Todos | 2026-03-17 a — | vigente | 58 |
| 546 - DICORP S.A. | Todos | 8749 - Arroz Diana x 1Kg | 15.00 % | CENTRO | FOOD SERVICE | Todos | 2026-03-16 a — | vigente | 63 |
| 546 - DICORP S.A. | Todos | 8750 - Arroz Diana xLb | 15.00 % | CENTRO | FOOD SERVICE | Todos | 2026-03-12 a — | vigente | 59 |
| 546 - DICORP S.A. | Todos | 8750 - Arroz Diana xLb | 13.00 % | OCCIDENTE | FOOD SERVICE | Todos | 2026-09-07 a 2026-12-31 | vigente | 98 |
| 584 - TRIPL3CORONA | Todos | Todas | 12.00 % | Todas | FOOD SERVICE | Todos | — a — | vigente | 41 |
| 584 - TRIPL3CORONA | Todos | Todas | 5.00 % | Todas | Todas | Todos | — a — | en revisión | 88 |
| 584 - TRIPL3CORONA | Todos | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | — a — | vigente | 66 |
| 584 - TRIPL3CORONA | MERA EL DORADO SAS (901009488) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 584 - TRIPL3CORONA | MERA MEDELLIN S.A.S (901230769) | Todas | 15.00 % | Todas | FOOD SERVICE | Todos | 2026-05-28 a — | vigente | 64 |
| 584 - TRIPL3CORONA | Todos | 1062 - Copa Margarita 9Oz 5444AL12 | 12.97 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 44 |
| 584 - TRIPL3CORONA | Todos | 10881 - Copa Agua Brunello 5469AL24 | 12.99 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 47 |
| 584 - TRIPL3CORONA | Todos | 11327 - Jarro Galerie Av Liso 0343AL48 | 13.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 49 |
| 584 - TRIPL3CORONA | Todos | 11340 - Jarra Venecia Atenas Av Liso 0620AL12 | 12.99 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 46 |
| 584 - TRIPL3CORONA | Todos | 11775 - Plato Te 16.3cm Artisan BeigPA1605900324 | 16.60 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 77 |
| 584 - TRIPL3CORONA | Todos | 11776 - Pocillo Te 260c Artisan BeigPA1605904424 | 16.43 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 72 |
| 584 - TRIPL3CORONA | Todos | 12225 - Plato Cafe 13.2cm Artis BeigPA1605900124 | 16.60 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 78 |
| 584 - TRIPL3CORONA | Todos | 12226 - Pocillo Cafe 94c ArtisanPA1605904224 | 16.60 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 75 |
| 584 - TRIPL3CORONA | Todos | 13998 - Pocillo 162ccCapu Art Beige PA1605804324 | 16.59 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 74 |
| 584 - TRIPL3CORONA | Todos | 16461 - Salero Rect 5.2c ArtisaBeig PA1605709812 | 16.60 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 76 |
| 584 - TRIPL3CORONA | Todos | 16463 - Azucarera 10x7cm Artis Beig PA1605909112 | 16.50 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 73 |
| 584 - TRIPL3CORONA | Todos | 20967 - Copa Cervecero Praga 5471AL | 13.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 50 |
| 584 - TRIPL3CORONA | Todos | 5076 - Copa Gran Vino Premier 4740AL24 | 12.98 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 45 |
| 584 - TRIPL3CORONA | Todos | 5542 - Copa Shot Prisma 1.5oz 0287AL72 | 13.54 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 56 |
| 584 - TRIPL3CORONA | Todos | 9171 - Vaso Av Liso Prisma 0269AL48 | 16.32 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-10 a — | vigente | 71 |
| 856 - COTRADING | Todos | 10373 - Maiz Tierno MeToo Vacumx 340g Dre 285g | 8.53 % | CENTRO | FOOD SERVICE | Todos | 2026-06-25 a 2026-08-31 | vencida | 20 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | Todas | 19.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-09-07 a — | vigente | 96 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | Todas | 14.40 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-04-01 a 2026-06-30 | vencida | 57 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | 15376 - Cidra De Rosas Juniper x207ml | 11.00 % | COSTA | FOOD SERVICE | Todos | — a — | vigente | 31 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | 20992 - Sugg Syrup Lyche Vidriox750ml | 9.80 % | ANTIOQUIA | FOOD SERVICE | Todos | — a 2026-12-31 | vigente | 23 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | 24460 - Sugg Syrup Sandia x750ml | 9.80 % | ANTIOQUIA | FOOD SERVICE | Todos | — a 2026-12-31 | vigente | 23 |
| 930 - JUNIPER BLENDS INTELLIGENCE | Todos | 25751 - Sugg Syrup Premium Pepino x750ml | 9.80 % | ANTIOQUIA | FOOD SERVICE | Todos | — a 2026-12-31 | vigente | 23 |
| 952 - LISTO EL POLLO | Todos | Todas | 12.00 % | Todas | Todas | Todos | — a — | vigente | 103 |
| 1002 - MCCAIN | Todos | 24537 - Papa delgada 9x9 x 2kg 5300- 5308 SQ | 12.00 % | ANTIOQUIA | FOOD SERVICE | Todos | — a — | vigente | 40 |
| 1014 - KARIBBEAN LIQUOR BLACK RIVER S.A.S. | COPETE LOPEZ LUIS ESTEBAN (8323437) | 3391 - Mayonesa Kari ExtraHeavy Premium x3.79Lt | 13.00 % | URABA | FOOD SERVICE | Todos | 2026-09-18 a 2026-12-18 | vigente | 100 |
| 1047 - C.I IBLU S.A.S | Todos | 12988 - Aceite Oliva Extravirgen Abril Px5Lt | -8.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-05-28 a — | vigente | 7 |
| 1062 - MONIN GOURMET FLAVORINGS | Todos | Todas | 15.00 % | CENTRO | Todas | MAYORISTAS Y DISTRIBUIDORES | — a — | vigente | 65 |
| 1126 - ACO UNILEVER | Todos | Todas | 13.00 % | CENTRO | FOOD SERVICE | Todos | 2025-11-24 a 2026-03-31 | vencida | 53 |
| 1126 - ACO UNILEVER | Todos | Todas | 13.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-08-13 a — | vigente | 95 |
| 1239 - EXPOMAR | Todos | 28532 - Camaron Crudo PyD 51/60 IQF 30% Bx2kg | 10.57 % | ANTIOQUIA, CENTRO | FOOD SERVICE | Todos | 2026-07-01 a — | vigente | 29 |
| 1239 - EXPOMAR | Todos | 28535 - Camaron CrudPYD IQF TailOn21/25 10%Bx2kg | 18.24 % | ANTIOQUIA, CENTRO | FOOD SERVICE | Todos | 2026-07-01 a — | vigente | 79 |
| (sin proveedor) | Todos | Todas | 7.00 % | Todas | FOOD SERVICE | Todos | 2025-08-29 a — | en revisión | 91 |
| (sin proveedor) | Todos | Todas | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | — a — | vigente | 60 |
| (sin proveedor) | Todos | Todas | 13.00 % | CENTRO | FOOD SERVICE | Todos | 2026-04-09 a — | vigente | 52 |
| (sin proveedor) | Todos | Todas | 16.00 % | Todas | Todas | MAYORISTAS Y DISTRIBUIDORES | — a — | vigente | 70 |
| (sin proveedor) | Todos | Todas | 13.00 % | ORIENTE | FOOD SERVICE | Todos | 2026-04-28 a — | en revisión | 86 |
| (sin proveedor) | Todos | Todas |  | ANTIOQUIA | FOOD SERVICE | Todos | — a — | vigente | 92 |
| (sin proveedor) | Todos | Todas | 6.00 % | ANTIOQUIA | FOOD SERVICE | Todos | — a 2026-12-31 | vigente | 8 |
| (sin proveedor) | INVERSIONES TATIANA GA S A S (900954696) | Todas |  | OCCIDENTE | Todas | MAYORISTAS Y DISTRIBUIDORES | 2026-03-31 a — | vigente | 104 |
| (sin proveedor) | Todos | 11672 - Lychee San Valentin 567g dren 255g | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-06-22 a — | en revisión | 87 |
| (sin proveedor) | Todos | 11672 - Lychee San Valentin 567g dren 255g | -9.84 % | ANTIOQUIA | FOOD SERVICE | Todos | — a — | vigente | 93 |
| (sin proveedor) | Todos | 11710 - Cobertura Palenque 70% x 2.5kg | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-06-22 a — | en revisión | 87 |
| (sin proveedor) | Todos | 12990 - Aceite Oliva Orujo Abril Px1Lt | 9.46 % | Todas | FOOD SERVICE | Todos | 2026-04-13 a — | vigente | 22 |
| (sin proveedor) | Todos | 12990 - Aceite Oliva Orujo Abril Px1Lt | 5.98 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-05-28 a — | en revisión | 89 |
| (sin proveedor) | Todos | 13453 - Aceite Oliva Orujo Abril Px5Lt | 5.98 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-05-28 a — | en revisión | 89 |
| (sin proveedor) | Todos | 13453 - Aceite Oliva Orujo Abril Px5Lt | 6.12 % | Todas | FOOD SERVICE | Todos | 2026-04-13 a — | vigente | 9 |
| (sin proveedor) | Todos | 13471 - Arroz Arborio Colavita x 1000g | 16.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-10-01 a 2026-12-30 | vigente | 101 |
| (sin proveedor) | Todos | 17157 - Jarabe Con MielZafran120ux8g 686-109-701 | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-06-30 a — | vigente | 68 |
| (sin proveedor) | Todos | 18721 - Wypall X80 Azul Power Pockets x30u 3107 | 11.00 % | COSTA | FOOD SERVICE | Todos | 2026-10-05 a 2026-12-31 | vigente | 106 |
| (sin proveedor) | Todos | 2148 - Arroz Roa Fortiplus x500g | 12.25 % | ANTIOQUIA | FOOD SERVICE | Todos | 2025-03-20 a — | vigente | 43 |
| (sin proveedor) | Todos | 21916 - Toalla Scott Nt Hs 6x100mt 0057 | 14.10 % | ORIENTE | FOOD SERVICE | Todos | 2025-03-03 a 2026-03-03 | en revisión | 84 |
| (sin proveedor) | Todos | 21922 - Toalla Scott Blan Z Hs x150u 8392 | 14.10 % | ORIENTE | FOOD SERVICE | Todos | 2025-03-03 a 2026-03-03 | en revisión | 84 |
| (sin proveedor) | Todos | 21993 - Leche De Coco Tropical Farm Lata x400ml | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-06-22 a — | en revisión | 87 |
| (sin proveedor) | AMELIER CAF? S.A.S (901705282) | 28067 - Mantequilla Colacteos x500g | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-09-07 a 2026-12-30 | vigente | 97 |
| (sin proveedor) | Todos | 28531 - Camaron Crudo PyD 71/90 IQF 30% Bx2kg | -3.65 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-09-01 a — | vigente | 94 |
| (sin proveedor) | Todos | 3391 - Mayonesa Kari ExtraHeavy Premium x3.79Lt | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-02-12 a — | vigente | 67 |
| (sin proveedor) | Todos | 7113 - Cocoa En Polvo Natural 16% Luker x1kg | 15.00 % | ANTIOQUIA | FOOD SERVICE | Todos | 2026-06-22 a — | en revisión | 87 |

## Qué falta para aplicarlas

1. Decidir cuáles aplican a todos los clientes y cuáles a un cliente puntual: hoy 89 de 120 no traen cliente.
2. Definir qué gana cuando una condición especial, un apoyo y el margen del producto cubren la misma línea.
3. Revisar las 21 que están en revisión y las 3 con margen mínimo negativo.
4. Confirmar la semántica de regional (Barranquilla, Urabá) y la de «Masivo» frente a «Retail».
