# Uso y contexto y catálogo — 2026-09-18

Base de desarrollo: `e21f06a`. Rama: `feature/usage-context-catalog`.
El worktree principal y sus archivos de reconciliación no se editan.

## Modelo y presentación

La migración **023** crea `alternative_usage_profile`:

- `profile_id INTEGER PRIMARY KEY AUTOINCREMENT`.
- `alternative_id INTEGER NOT NULL UNIQUE`, FK a `alternative`.
- Once campos TEXT opcionales: `frequency_impressionistic`,
  `geographic_zone_general`, `geographic_zone_detail`, `age_group`,
  `socioeconomic_group`, `popular_etymology`, `iconicity_notes`,
  `register_notes`, `semantic_pragmatic_nuance`, `spanish_relations`,
  `additional_notes`.
- `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `updated_at TEXT`.
- Índice único implícito de SQLite sobre `alternative_id`.
- FK con `NO ACTION`, coherente con Alternative/Morphology: se conserva el
  historial ante retiros y se impide eliminar una Alternative referenciada.
- Los CHECK rechazan ausencias artificiales comunes. El importador normaliza
  también whitespace, mayúsculas/minúsculas y valores vacíos a NULL.

No se crean filas para Alternatives sin perfil. Una fila completamente NULL es
válida, pero no genera pestaña. Las nuevas publicaciones pueden congelar perfiles
sustantivos y su diff identifica cambios; las publicaciones ya existentes no se
reescriben ni se enriquecen leyendo datos vivos.

`catalog_context.semantic_fields` prefiere la revisión vigente `semantic-fields`;
si no existe, utiliza los dos campos legacy de Concept. Una revisión explícita
vacía no reactiva datos legacy. No hay backfill ni cambios de clasificación.

El selector permite buscar y seleccionar varios campos (OR entre campos, AND
con los demás filtros). La clasificación de variación reutiliza los componentes
conectados por relaciones explícitas del catálogo vigente. El filtro de video
solo limita Concepts; todas sus Alternatives permanecen accesibles.

Se conserva el reproductor de asociaciones `alternative_media` vigentes con rol
`catalog_video`, MIME `video/youtube` y URL utilizable por el helper existente.
No se deducen enlaces a partir de fuentes ni de datos legacy. La copia reconciliada
y la instantánea remota de pruebas inspeccionadas tienen **cero asociaciones**
en `alternative_media` y `occurrence_media`; por tanto no se puede demostrar
reproducción de un video real de MORADO con esos datos. Los tests usan asociaciones
sintéticas para verificar reproductor, ausencia, retiro y filtro.

## Insumo y scripts

`perfil_uso_contexto_consolidado.xlsx` se copia sin modificar el original.
SHA-256: `40ab388966fec68110cf2383fe2c7b4f93728ba4092b15b22dffd93ee46b7c0f`.
El lector OOXML utiliza la biblioteca estándar de Python; no añade dependencias
al servidor ni acepta fórmulas con resultados potencialmente obsoletos.

Los scripts requieren `--database`. Sin `--apply`, simulan en memoria desde una
conexión de solo lectura. Para aplicar, toman `BEGIN IMMEDIATE`, comprueban
precondiciones, crean backup SQLite consistente mediante la API de backup,
comprueban integridad y conservan el ledger. Cualquier error provoca rollback.
El reporte enumera las operaciones y sus identidades; una segunda ejecución
satisfecha no escribe ni genera otro backup. No hay SQL operativo manual.

Orden, sobre una **copia local** explícita:

```powershell
python migrations/023_alternative_usage_profile.py --database validation/candidate.db --apply --report validation/migration.json
python migration/usage_profile_2026-09-18/post_reconciliation.py --database validation/candidate.db --apply --report validation/pending.json
python migration/usage_profile_2026-09-18/import_profiles.py --database validation/candidate.db --apply --report validation/import.json
python migration/usage_profile_2026-09-18/verify_candidate.py --database validation/candidate.db --baseline validation/before.db --report validation/acceptance.json
```

Repetir los tres primeros comandos debe producir cero cambios. El importador
rechaza diferencias salvo `--allow-update` explícito; rechaza IDs vacíos excepto
las dos excepciones autorizadas. Incluso con ID compara el nombre canónico y
exige una Alternative vigente. Nunca modifica el Excel.

Pendientes BD: seis Occurrences en los cuatro casos documentados. Identidad por
legacy completo + glosa + fuente auditada. Solo se aceptan asignaciones ausentes
en los tres casos a crear, o la asignación final correcta. PREGUNTAR se valida
sin escribir. Los tres localizadores se guardan en `source_detail_2` con estado
`VALUE` y en `source_locator`, conservando `source_detail_1`. La corrección
documentada de `10540-COLOMBIA` reemplaza el valor previo `01:16` por `1:18`.
Un retiro, otra asignación o un localizador no previsto bloquean el script.

Resultado local observado:

| Tabla | Antes | Después |
|---|---:|---:|
| occurrence | 10.708 | 10.708 |
| concept | 1.398 | 1.399 |
| alternative | 1.458 | 1.460 |
| assignment | 2.515 | 2.518 |
| alternative_relation | 321 | 322 |
| alternative_usage_profile | 0 | 262 |
| reconciliation_operation | 23.377 | 23.377 |

IDs resueltos: MAPA-COLOMBIA-1b = **1459**, MÁS/MENOS-QUÉ-1a = **1460**.
Importación: 262 creados, 2.491 campos NULL. Segunda pasada: 262 coincidentes,
cero cambios. `integrity_check=ok`, `foreign_key_check=0`.
`verify_candidate.py` también compara hashes de todas las tablas ajenas al cambio
y comprueba que solo se modificaron las tres Occurrences autorizadas.

## Validaciones

```powershell
python -m unittest discover -s tests -p 'test_usage_context.py' -v
python -m unittest discover -s tests -p 'test_catalog*.py' -v
node tests/test_catalog_filters.js
python tests/browser_usage_context.py
python -m unittest discover -s tests -v
```

El smoke de navegador requiere Playwright y Chromium instalados en el entorno de
desarrollo; no es una dependencia de la aplicación. Prueba selección múltiple,
búsqueda, combinación, limpieza, navegación, perfiles condicionales y conservación
de Alternatives sin video. Guarda capturas de escritorio/móvil y un reporte en
`validation/`, que queda fuera de git.

La auditoría amplia de los datos reconciliados señala 18 controles fallidos y
8 advertencias **idénticos antes y después**: 17 controles de nomenclatura y uno
de períodos de Occurrence (1.079 filas). No se reinterpretaron esos datos. La
comparación queda en `reports/audit-baseline-comparison.json`.

## Railway: exclusivamente pruebas

Proyecto `547dcb35-3521-486e-b706-55c936672bc3`, environment
`dc6a07af-8842-44c8-a072-a0d3e10ae203` (`pruebas`), servicio
`2279c8d8-0013-4794-a25b-cebf3e4fe9b1`.

`railway_pruebas.py` fija estos tres IDs y vuelve a comprobarlos dentro del
contenedor, junto con el nombre del environment y
`LESICO_DATABASE_PATH=/data/lesico_astronomia.db`. No permite destinos alternativos.
Se comprobó autenticación y se descargó una instantánea consistente de pruebas,
idéntica por tabla al insumo local previo. No se accedió a production.

**La aplicación remota solo debe continuar cuando se cumpla el requisito de
validación local de la solicitud.** Comandos preparados, no ejecutados durante
la validación:

```powershell
python migration/usage_profile_2026-09-18/railway_pruebas.py snapshot --output validation/pruebas-before.db
python migration/usage_profile_2026-09-18/railway_pruebas.py stage --output validation/railway-stage.json
python migration/usage_profile_2026-09-18/railway_pruebas.py apply --apply --output validation/railway-apply.json
railway up --project 547dcb35-3521-486e-b706-55c936672bc3 --environment dc6a07af-8842-44c8-a072-a0d3e10ae203 --service 2279c8d8-0013-4794-a25b-cebf3e4fe9b1 --detach --message "Usage profiles and catalog filters"
railway status --project 547dcb35-3521-486e-b706-55c936672bc3 --environment dc6a07af-8842-44c8-a072-a0d3e10ae203 --json
```

`stage` escribe únicamente el paquete de mantenimiento en `/tmp`, validando su
hash. `apply` combina migración, correcciones e importación en **una transacción**,
con backup permanente en `/data`, comprueba idempotencia y guarda un reporte
permanente. No intercambia archivos ni requiere un deployment de rescate; el
bloqueo SQLite serializa escrituras de conexiones concurrentes. El esquema nuevo
es aditivo y la UI anterior puede seguir leyendo durante el cambio.

Después de un futuro despliegue: comprobar estado SUCCESS, logs de Gunicorn y
smokes de MORADO, A-VECES, MAPA-COLOMBIA, EMPANADA, MÁS/MENOS-QUÉ y PREGUNTAR. No
se publica una versión oficial del catálogo ni se modifica production.
