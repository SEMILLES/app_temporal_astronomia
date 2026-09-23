# Clasificaciones de conceptos nuevos en aportes de Alternative

El analista completa las clasificaciones durante el análisis léxico de una
ocurrencia cuya referencia es una propuesta de concepto aún no resuelta con
`proposal_origin=NEW_PROPOSAL`. El
nombre sigue perteneciendo al paso de referencia conceptual existente.

El editor del analista no aparece para `SELECTED_PENDING`, referencias directas
ni referencias de origen desconocido. El backend no exige metadata en esos casos
y rechaza cualquier metadata enviada manualmente, sin almacenar el aporte.
La clasificación original de aportes ya guardados se conserva. El revisor puede
completar la clasificación obligatoria si su resolución final crea un concepto,
aunque el analista haya seleccionado una propuesta pendiente sin metadata.

Para `USE_EXISTING`, la revisión utiliza un único selector «Concepto de destino».
«Consultar estado de este concepto» consulta ese valor, recarga su token y muestra
su nombre y cantidad de alternativas vigentes. Cambiar el selector invalida el
estado mostrado y deshabilita guardar hasta una nueva consulta. El backend sigue
rechazando tokens obsoletos o que cubran un destino distinto. Se mantienen
`concept_edit_token` y `lexical_preview_token`.

- Campo semántico obligatorio: una o dos categorías activas y distintas.
- Colecciones activas obtenidas del catálogo, con pertenencia múltiple.
- Cada colección seleccionada exige una o dos categorías activas y distintas
  para cada uno de sus sistemas activos, según `classification_system.collection_id`.
- Desmarcar una colección oculta y deshabilita sus campos. El servidor también
  rechaza clasificaciones sin la incorporación correspondiente.
- El formulario conserva los valores enviados cuando falla la validación del analista.

Se reutilizan `parse_form`, `editor_context`, `store_proposal` y `apply_metadata`.
La validación adicional de selección completa se aplica exclusivamente a este
flujo; la administración general de conceptos conserva su comportamiento.
No se añaden tablas, migraciones ni usos de columnas legacy.

## Decisión conceptual independiente

El revisor parte de la propuesta original y puede cambiar categorías o añadir y
quitar colecciones. Las tablas de propuestas originales no se sobrescriben.
La creación del concepto, las pertenencias, las revisiones y
`classification_decision_json` se guardan en la misma transacción. Los fallos
revocan la operación completa, también dentro de una transacción exterior.

`ACCEPT_PROPOSAL` muestra el editor solo cuando su resultado será `CREATE_NEW`.
Si el nombre ya corresponde a un concepto existente, o se selecciona
`USE_EXISTING` / `CONFIRM_REFERENCE`, el editor se oculta y deshabilita. El
servidor rechaza metadata explícita para esos destinos. La aceptación inmediata
conserva la propuesta histórica pero solo pasa clasificaciones a una resolución
que cree un concepto.

Un rechazo léxico posterior conserva el concepto y sus clasificaciones ya
aprobadas. Elegir un concepto existente y rechazar el resto del aporte no aplica
las clasificaciones propuestas para el concepto nuevo. Se mantienen las acciones
de resolución conceptual existentes; esta entrega no añade una acción autónoma
de rechazo conceptual sin concepto de destino.

## Verificación

Corrección de UX sobre `db7cdd3` (editor del analista y destino único del revisor):

```text
python -B -m pytest -q -p no:cacheprovider --tb=short tests/test_concept_decision_ux.py tests/test_new_concept_metadata.py tests/test_new_concept_metadata_browser.py tests/test_concept_reference_origin.py tests/test_concept_classification.py tests/test_submission_concept_resolution.py tests/test_alternative_workflow.py tests/test_alternative_routes.py
```

Resultado: **241 pruebas y 37 subcasos aprobados**, incluidas siete pruebas de
Chromium. Se comprueban el bloqueo de metadata fuera de `NEW_PROPOSAL`, la creación
clasificada por el revisor desde `SELECTED_PENDING`, el destino único y su consulta,
el rechazo de token A/destino B, el guardado de B con su token, cambios concurrentes
y las opciones léxicas según alternativas vigentes. Se ajustaron las referencias
de las fixtures de clasificación para declarar explícitamente `NEW_PROPOSAL`.
Las pruebas usan únicamente bases sintéticas. `git diff --check` pasa.

Archivos de esta corrección:

- `alternative_workflow.py`, `routes/occurrences.py`, `routes/submissions.py`.
- `templates/_submission_concept_resolution.html`.
- `tests/test_concept_decision_ux.py`, `tests/test_new_concept_metadata_browser.py`,
  `tests/test_new_concept_metadata.py`, `tests/test_concept_reference_origin.py`,
  `tests/test_concept_classification.py`, `tests/test_submission_concept_resolution.py`,
  `tests/test_alternative_workflow.py`.
- Este documento.

Verificación histórica de la entrega original de clasificaciones:

Pruebas focalizadas:

```text
python -m pytest -q -p no:cacheprovider tests/test_concept_classification.py tests/test_submission_concept_resolution.py tests/test_new_concept_metadata.py tests/test_new_concept_metadata_browser.py
```

Las pruebas de navegador usan Playwright/Chromium y datos sintéticos servidos
por el cliente de pruebas de Flask. Verifican el POST efectivo al desmarcar una
colección y al cambiar el destino a un concepto existente, incluida la coincidencia
de nombre al aceptar una propuesta. Se omiten si Playwright no está instalado.

También se verificaron `test_alternative_workflow.py`, `test_alternative_routes.py`,
`test_alternative_morphology.py`, `test_phase18c6e_comparable_relations.py`,
`test_immediate_acceptance.py` y `test_immediate_acceptance_routes.py`.

Resultado combinado: **248 pruebas y 50 subcasos aprobados; 6 fallos preexistentes**
(cuatro pruebas y dos subcasos), reproducidos en el HEAD de partida. Las pruebas
focalizadas de clasificaciones y resolución conceptual, incluidas las dos de
Chromium, pasan. `git diff --check` y `node --check static/new-concept-metadata.js`
también pasan.

Se reprodujeron los siguientes fallos preexistentes contra una copia de
HEAD `89228bb`, sin cambios de esta entrega:

- `MorphologyReviewTests.test_review_new_explicit_rejection_does_not_materialize_but_history_remains`.
- `ComparableUITests.test_closed_new_with_rejected_relations_has_no_pending_action`.
- `ImmediateAcceptanceTests.test_changed_proposals_require_note_and_record_assignment_effect`
  (subcasos `EXISTING/new` y `UNSURE/new`).
- `ImmediateAcceptanceTests.test_explicit_morphology_rejection_requires_note_and_is_not_pending`.
- `ImmediateAcceptanceTests.test_new_rejected_relations_and_morphology_do_not_materialize`.

Estos casos esperan crear una Alternative sin morfología aprobada, mientras que
el backend de partida ya lo impide. No se cambia esa regla en este bloque.

En Windows se utilizó el intérprete del entorno virtual existente, sin modificarlo,
y `TEMP` / `TMP` apuntaron a `.test-tmp/concept-metadata` dentro de este worktree.
La ejecución requirió salir de la restricción del sandbox que impide acceder a
los directorios creados por `tempfile`. Todas las bases utilizadas fueron de prueba.

## Archivos de la entrega

- Servicios: `concept_classification.py`, `alternative_workflow.py`,
  `submission_concept_resolution.py`, `immediate_acceptance.py`.
- Rutas: `routes/occurrences.py`, `routes/submissions.py`.
- Interfaz: `templates/clasificar_ocurrencia.html`,
  `templates/_submission_concept_resolution.html`,
  `templates/_new_concept_metadata_fields.html`, `static/new-concept-metadata.js`.
- Pruebas: `tests/test_concept_classification.py`,
  `tests/test_submission_concept_resolution.py`, `tests/test_alternative_workflow.py`,
  `tests/test_immediate_acceptance.py`, `tests/test_new_concept_metadata.py`,
  `tests/test_new_concept_metadata_browser.py`.
- Documentación: `docs/NEW_CONCEPT_METADATA.md`.
