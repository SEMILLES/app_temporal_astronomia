# 18C6A — Resolución conceptual local

La revisión léxica guarda primero una resolución conceptual propia del aporte.
Aceptar o rechazar el resto exige esa resolución. El guardado conceptual mantiene
el aporte pendiente y no altera Alternatives, assignments, relaciones, morfología
ni nomenclatura. La aceptación léxica conserva los mecanismos canónicos existentes.

## Esquema y migración

`migrations/020_submission_concept_resolution.py` crea la tabla sin backfill ni
reescritura de datos anteriores. Es idempotente y admite respaldo explícito.
No se ejecutó sobre la base real de trabajo. El esquema de bases nuevas instala
la misma definición mediante `submission_concept_resolution.install()`.

Tabla `submission_concept_resolution`:

| Campo | Restricción / función |
| --- | --- |
| submission_concept_resolution_id | INTEGER PRIMARY KEY AUTOINCREMENT |
| submission_id | NOT NULL, FK submission |
| concept_id | NOT NULL, FK concept |
| resolution_action | CONFIRM_REFERENCE / USE_EXISTING / CREATE_NEW |
| resolution_note | Nota; el servicio la exige ante cambios materiales |
| collaborator_id | FK collaborator, opcional según atribución existente |
| collaborator_name_snapshot | Nombre del colaborador al decidir |
| access_role | NOT NULL, reviewer / master |
| created_at | NOT NULL, CURRENT_TIMESTAMP |
| supersedes_submission_concept_resolution_id | FK a versión previa |
| is_current | NOT NULL, 0 / 1, predeterminado 1 |

Índices: `one_current_submission_concept_resolution` (único parcial por
submission_id, WHERE is_current=1) e
`idx_submission_concept_resolution_submission` (historial por submission_id).
No se duplica occurrence_id. El servicio exige submission ALTERNATIVE pending.

## Servicio y transacciones

`current_resolution()`, `resolution_history()`, `require_concept()` y
`save_resolution()` centralizan lectura, historial, precondición de cierre y
versionado. El guardado conserva versiones previas, crea una referencia conceptual
directa versionada para la occurrence y registra `submission_concept_resolved`
con IDs de resolución, referencia y Concept en activity_event.

El servicio participa en transacciones externas mediante savepoint; en llamadas
independientes usa BEGIN IMMEDIATE. La ruta POST `/aportes/<id>/concepto` exige
Reviewer/Master y token firmado de edición; formularios obsoletos reciben 409.

La comparación de etiquetas utiliza normalize_concept_label. Confirmar una
referencia directa o aceptar una etiqueta propuesta equivalente permite nota
opcional. Cambiar el Concept original o sustituir una resolución por otro Concept
exige nota. Los aportes cerrados no pueden sustituir su resolución por este flujo.

## Separación de estados

Antes: aceptar Alternative podía resolver concept_proposal para todos sus usuarios.
Ahora: cada submission tiene su resolución local; concept_proposal conserva su
estado y resultado global, incluso si está rejected o resolved. La referencia
original de alternative_submission no se modifica, tampoco en aportes legacy.

La occurrence recibe una nueva occurrence_concept_reference, enlazada a la previa.
Un assignment anterior puede seguir bajo Concept A mientras esta revisión resuelve
Concept B. Solo aceptar una Alternative bajo B sustituye el assignment. Rechazar
el resto conserva tanto la resolución B como el assignment previo.

Futuras submissions copian la referencia vigente, pero nacen sin resolución local.
Los aportes históricos cerrados sin resolución muestran su ausencia expresamente;
no se infiere una decisión histórica desde Alternative, assignment o propuesta.
La denominación actual de la Alternative resultante se identifica como tal.

## Aceptación inmediata y límite para 18C6B

El registro conceptual inmediato existente se conserva: force_new_proposal=True
crea una propuesta exclusiva antes de resolverla; no afecta propuestas compartidas.

La aceptación inmediata ALTERNATIVE registra una resolución local dentro de la
misma transacción. Con referencia directa confirma ese Concept y lo informa en
la interfaz. Con propuesta conceptual necesita una decisión explícita; sin ella
revierte todo. El servicio admite USE_EXISTING / CREATE_NEW sin modificar la
propuesta global.

La interfaz de aceptación inmediata para referencias a propuestas se deshabilita
expresamente y remite a revisión normal. Añadir allí selección conceptual, nota y
preview completo queda para 18C6B. No queda una vía léxica que resuelva globalmente
una propuesta compartida.

## Validación

Las pruebas usan SQLite temporales o en memoria. La cobertura nueva comprende
independencia X/Y, versionado, notas, ausencia de cierre sin resolución, rechazo
parcial, propuesta rejected, legacy, inmutabilidad original, permisos, concurrencia,
rollback e idempotencia de migración. Los fixtures de pruebas de etapas léxicas
posteriores guardan ahora la resolución conceptual explícita antes de probar esas
etapas; se conservan sus comprobaciones canónicas.
