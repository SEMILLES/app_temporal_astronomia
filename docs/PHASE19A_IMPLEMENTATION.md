# Fase 19A — clasificaciones estructuradas de Concept

## Alcance

Implementación de esquema, servicios, UI y pruebas. Solo se han ejecutado tests
con bases sintéticas temporales; no se han migrado bases reales. No convierte
datos legacy ni introduce submissions conceptuales independientes, importaciones
o anotaciones por Alternative.

La migración `022_concept_classification.py` permite un bootstrap explícito y
autorizado, separado del arranque. El arranque con DB explícita sigue validando
sin instalar 19A: se debe ejecutar primero la migración y después arrancar el
runtime nuevo. `REQUIRED_APPLICATION_TABLES` conserva las siete tablas exigidas.
La creación de bases nuevas mediante `crear_esquema` incluye esquema y seeds.

## Bootstrap explícito 022 (corrección pre-staging)

Ejemplo de sintaxis para una futura operación autorizada; no se ejecutó contra
ninguna base real:

```text
python migrations/022_concept_classification.py --database "RUTA/base.sqlite" --apply --backup "RUTA/base.pre19a.sqlite"
```

- `--database` es obligatorio y el archivo debe existir. Sin `--apply` rechaza
  antes de abrir la base. La API requiere `apply=True`; conserva `test_only=True`
  como autorización explícita exclusivamente para archivos temporales de tests.
- `--backup` es opcional; por defecto usa `<nombre>.pre_migration_022<extensión>`.
  Reserva el archivo sin sobrescribirlo y comunica su ruta absoluta.
- Comprueba tablas pre-19A, columnas fundamentales y el contrato completo de
  `submission_concept_resolution` (columnas, restricciones, FK e índices).
  Una instalación parcial/incompatible se rechaza sin intentar repararla.
- Adquiere `BEGIN IMMEDIATE` antes de validar/respaldar. Sin escribir todavía,
  otra conexión de lectura ejecuta `Connection.backup()` sobre el estado
  confirmado, incluido WAL; el bloqueo impide escrituras concurrentes durante
  backup e instalación. Un backup fallido impide todo DDL.
- Instala las siete tablas, índices, triggers, seeds y
  `classification_decision_json` en esa transacción. Valida sus estructuras,
  seeds iniciales exactos, `foreign_key_check`, `integrity_check` y los conteos
  y hashes de contenido de todas las tablas de aplicación preexistentes.
- No transforma legacy, no crea membresías/clasificaciones/propuestas y no
  modifica filas de Concepts, Alternatives, Occurrences o publicaciones.
- Ante un error hace rollback y conserva el backup, informando el motivo.
- Si 19A ya está completa, valida y termina sin cambios ni otro backup. No
  sobrescribe renombres/desactivaciones legítimos de Master: en esta repetición
  verifica las identidades/códigos y ámbitos de seeds, no impone sus nombres
  iniciales sobre los catálogos ya administrados.
- La herramienta solo depende de biblioteca estándar y `classification_schema.py`.
  No importa `database.py`, `app.py`, Flask ni configuración del runtime. Los tests
  la ejecutan con `python -S` desde un paquete temporal que contiene solo esos
  dos archivos y una DB sintética pre-19A.

## Esquema

Siete tablas nuevas:

1. `collection`: identidad/código permanente, nombre, estado activo.
2. `classification_system`: sistema global `semantic-fields` o sistema de una
   Collection; `knowledge-areas` pertenece a `academic-vocabulary`.
3. `classification_category`: categorías identificadas, únicas por código/nombre
   normalizado dentro del sistema, con estado activo.
4. `collection_membership`: períodos de pertenencia con autoría y origen.
5. `concept_classification_revision`: pares ordenados, snapshots de nombre/código,
   revisión anterior, vigencia, autoría y origen. Vinculada a membresía cuando
   el sistema pertenece a una Collection.
6. `submission_classification_proposal`: selección original inmutable por aporte
   y sistema; snapshots y estado base para revisión.
7. `submission_collection_proposal`: acción original de membresía, snapshots y
   estado base.

La única columna añadida a una tabla existente es
`submission_concept_resolution.classification_decision_json`: resultado de la
decisión, con nombres/códigos históricos y referencias a las revisiones. Una vez
registrado no se reescribe. La revisión conceptual conserva sus mecanismos
existentes de `is_current` y `supersedes`.

Se inicializan exactamente 33 Campos Semánticos y 23 Áreas de Conocimiento.
`Otro` es una categoría normal. `N/A` se rechaza; vacío está permitido. Las
semánticas de N/A de otros módulos (p. ej., morfología) no cambian.

## Semántica y transacciones

- Las posiciones 1/2 preservan el orden visual elegido, sin prioridad semántica.
  Seleccionar solo 2 se normaliza a 1. Intercambiar posiciones persiste como una
  nueva revisión con `semantic_changed=0`.
- Ausencia de un sistema en la operación conserva su estado. Un par vacío
  explícito crea una revisión vacía, sin confundirse con omisión.
- Retirar cierra primero todas las clasificaciones de esa membresía y después
  la membresía, en la misma transacción. No modifica las entidades globales.
- Reincorporar crea una nueva membresía sin recuperar Áreas históricas.
- Desactivar una definición impide nuevas asignaciones. Se pueden conservar
  selecciones previas inactivas y retirarlas. No hay borrado físico ni cambio
  de código/ámbito.
- El estado vigente puede mostrar el nombre actual; el historial y las
  propuestas/decisiones muestran sus snapshots originales.

## Flujo de usuario

- Reviewer/Master: `/conceptos` permite crear con selecciones opcionales.
- Analyst: en el paso existente de análisis léxico (`/ocurrencias/<id>/clasificar`)
  propone Campos, membresías y Áreas junto al aporte. La referencia conceptual
  puede ser un Concept o una propuesta nueva del registro de evidencia.
- Reviewer: en el bloque de resolución de Concept del aporte consulta el estado
  del destino y marca qué selecciones aplicar; puede corregirlas o vaciarlas.
  Las propuestas originales siempre siguen visibles. Puede conservar el estado
  vigente sin aplicar la propuesta.
- Guardar la resolución aplica Concept/membresías/clasificaciones conjuntamente.
  La revisión léxica sigue pendiente. Rechazar después la parte léxica no revierte
  la resolución conceptual.
- `/conceptos/<id>/editar`: usa el mismo servicio para clasificaciones y
  membresías. `/conceptos/<id>/clasificaciones` permite consultar historia.
- Solo Master: `/administracion/clasificaciones` crea, renombra y desactiva
  Collections, sistemas y categorías.

Las precondiciones de edición abarcan revisiones, membresías y definiciones.
En `submission_concept`, la historia y las membresías (incluidas las cerradas)
se filtran por los Concepts de referencia, resolución actual, base de propuesta
y destino consultado. Cambiar metadata de un Concept ajeno no invalida el token.
`controlled_catalogs` sigue siendo global, y la lista global previa de etiquetas
de Concepts no se rediseñó.

El destino consultado y el conjunto de IDs relevantes quedan firmados dentro
del token. Al cambiar de destino se obtiene otro token; guardar comprueba tanto
el campo `metadata_target` como el destino efectivo, incluso si no hay cambios
de clasificaciones. `ACCEPT_PROPOSAL` usa la misma resolución de etiqueta para
incluir y proteger un Concept existente que reutilizará. Un destino nuevo se
crea dentro de la transacción y conserva las verificaciones previas de etiqueta.

El aporte compara el estado base de la propuesta; si cambió, exige revisión
explícita con un formulario vigente y el destino mostrado. Los previews de
aceptación léxica inmediata incluyen este estado cuando contienen selecciones.

## Publicación: opción B durante 19A

`catalog_projection` conserva deliberadamente la lectura legacy de
`semantic_fields` y `knowledge_areas`, incluido su orden. No hace fallback ni
doble escritura. Así no se ocultan valores existentes ni reaparecen Áreas legacy
al retirar/reincorporar una membresía estructurada.

**Las selecciones estructuradas nuevas se consultan en la UI de Concept/revisión;
todavía no cambian el catálogo interno/externo basado en esa proyección.**
Esta limitación temporal evita mezclar fuentes de autoridad antes de migrar.

La migración posterior debe cambiar la fuente de esos arrays, preservar el orden
de las posiciones e incorporar sus cambios al diff. No se deben editar snapshots
publicados ni resolver sus nombres desde catálogos vigentes.

Fase 19B: Collections explícitas en snapshot, filtros públicos, historial público
de membresías y publicación independiente. Nada de ello es necesario para
registrar y revisar datos estructurados internamente.

## Verificación

`tests/test_concept_classification.py` cubre seeds, migración temporal, servicio,
formularios, permisos, snapshots de nombres, concurrencia, rollback, membresías,
compatibilidad legacy y detección de corrupción. Solo usa SQLite en memoria o
archivos recién creados en `TemporaryDirectory`.

También se ejecutan regresiones de resolución conceptual, aceptación inmediata,
auditor, publicación y concurrencia. La suite `test_atomic_lexical_review` tiene
siete fallos anteriores a 19A: reproducidos ejecutando el mismo módulo contra
una copia temporal de código de `HEAD`, sin bases existentes. Se refieren a
nomenclatura/retiro de Alternatives, no a estas nuevas clasificaciones.

`test_submission_lexical_ui` también reproduce en `HEAD` sus 17 fallos de
aserción (incluidos subtests): espera 302/400 y recibe 409 por precondiciones
de preview. No se modificaron esos tests ni se relajaron las precondiciones.

Comando de la tanda integrada (162 tests correctos antes de agregar el test
adicional de aceptación inmediata con metadata):

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_concept_classification tests.test_submission_concept_resolution tests.test_catalog_projection tests.test_phase17b1_concurrency tests.test_integrity_audit tests.test_immediate_acceptance tests.test_immediate_acceptance_routes tests.test_catalog_publication tests.test_catalog_publication_migration tests.test_submission_lexical_decision
```

La verificación de cierre vuelve a ejecutar los 30 tests finales de 19A y los
12 tests de selección de base y rutas de catálogo:

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_concept_classification tests.test_database_selection tests.test_internal_catalog_routes tests.test_external_catalog_routes
```

## Archivos

Nuevos:

- `classification_schema.py`
- `concept_classification.py`
- `migrations/022_concept_classification.py`
- `templates/_concept_metadata_fields.html`
- `templates/_concept_metadata_history.html`
- `templates/administrar_clasificaciones.html`
- `templates/clasificaciones_concepto.html`
- `tests/test_concept_classification.py`
- `docs/PHASE19A_IMPLEMENTATION.md`
- `tests/test_phase19a_prestaging.py` (correcciones posteriores A/B)

Modificados:

- `database.py`, `access_control.py`, `integrity_audit.py`
- `alternative_workflow.py`, `submission_concept_resolution.py`
- `edit_concurrency.py`, `lexical_preconditions.py`, `immediate_acceptance.py`
- `catalog_projection.py` (comentario que fija explícitamente la opción B)
- `routes/concepts.py`, `routes/occurrences.py`, `routes/submissions.py`
- `templates/_internal_header.html`, `templates/_submission_concept_resolution.html`
- `templates/clasificar_ocurrencia.html`, `templates/conceptos.html`, `templates/editar_concepto.html`

## Verificación de correcciones pre-staging A/B

24 pruebas adicionales en `tests/test_phase19a_prestaging.py`: autorización,
bootstrap independiente, backup consistente con WAL, idempotencia, rechazo de
esquemas incompatibles/parciales, conservación de filas/snapshots, rollback por
triggers/seeds/integridad inválidos y tokens limitados al Concept/destino.

Se ajustó una prueba de ruta en `tests/test_submission_concept_resolution.py`
para consultar el nuevo destino antes de enviar el token, como exige ahora el
contrato firmado. El resto de la estrategia previa se conserva.

Resultado de la tanda integrada de estas correcciones: **124 tests correctos**.

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_phase19a_prestaging tests.test_concept_classification tests.test_submission_concept_resolution tests.test_phase17b1_concurrency tests.test_immediate_acceptance tests.test_immediate_acceptance_routes tests.test_database_selection
```

No se ejecutó `smoke_phase17b1_concurrency.py`, porque parte de una base real.
No se ejecutó Railway ni se migraron bases reales o de pruebas persistentes.
