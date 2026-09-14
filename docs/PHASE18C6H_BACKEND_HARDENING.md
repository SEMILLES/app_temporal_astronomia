# Fase 18C6H — Hardening final del backend

## Alcance y estado inicial

Rama `feature/evidence-analysis-media`, base `08eeaa7`. El estado inicial
contenía exclusivamente los dos archivos históricos sin seguimiento indicados
en la solicitud. Se preservan. No se ejecutan migraciones ni se accede a bases
reales, Railway o producción. Sin cambios de CSS, commit, add ni push.

## Hallazgos y protección implementada

- La revisión ordinaria recalculaba la decisión sin comprobar el estado de la
  página abierta. Ahora el formulario existente lleva `lexical_preview_token`.
  La página, sus previews y el fingerprint se leen en una única transacción de
  lectura. Usar existente, crear nueva y rechazar comprueban el token antes de
  cualquier escritura, bajo la transacción de cierre.
- La aceptación inmediata no vinculaba su confirmación con el preview.
  Ahora firma el fingerprint **y** la propuesta/decisión normalizadas. El token
  se calcula antes de simular, dentro de `BEGIN IMMEDIATE`; la simulación siempre
  hace rollback. Confirmar vuelve a comprobarlo bajo el bloqueo de escritura,
  antes de crear el aporte, la resolución conceptual o cualquier evento.
- Se reutilizan SHA-256, JSON canónico y el firmante existente de
  `edit_concurrency`: secreto configurado/fallback del proceso y caducidad de
  una hora. No se persisten tokens ni estados temporales en la base.
- Token ausente, manipulado, reutilizado tras un cierre o con estado distinto:
  HTTP 409, sin writes, con el mensaje:
  “El estado cambió desde la vista previa. Vuelve a revisar la decisión antes
  de confirmarla.” No se fusionan decisiones concurrentes.

## Estado cubierto

`lexical_preconditions.py` incluye la submission y su estado, resolución local
vigente, propuestas de relaciones/morfología/componentes y submissions destino
de las relaciones; occurrence, referencia y assignment vigentes; endpoints,
conceptos origen/destino, alternativas activas, working labels, relaciones y
evidencia temporal necesaria para recalcular nomenclatura. Para aceptación
inmediata incluye también aportes léxicos pendientes sobre la occurrence.

`alternative_preconditions.relevant_state` se comparte con las operaciones
estructurales. Se excluyen renumber events históricos, assignments/relaciones
no vigentes, nombres visuales, glosses, timestamps de auditoría y atribución.
Se conserva `alternative.created_at`: A.3 lo usa para desempatar el orden de
registro; por tanto sí afecta semánticamente a la nomenclatura. Las notas de
morfología que se aceptan o retiran forman parte del análisis protegido.

El alcance ordinario cubre las opciones ya disponibles en el formulario del
concepto resuelto, incluidas las dos resoluciones posibles de relaciones.
La selección/manualización sigue en ese único formulario y se valida al cerrar.
No se permite cambiar el concepto mediante un POST de cierre: hay que guardar
la resolución conceptual y volver a abrir su preview. La aceptación inmediata
sí liga la selección exacta del preview a la confirmación.

## Operaciones estructurales e invariantes

Retiro, fusión, división, move aislado y component move conservan sus tokens de
especificación firmada y fingerprints comprobados con `BEGIN IMMEDIATE`.
Los previews se ejecutan en una copia SQLite en memoria, incluso con una
conexión original de solo lectura. Los apply revalidan las precondiciones.

- Retiro exige exactamente una resolución por occurrence, también cuando no
  hay occurrences. No deja assignments vigentes en la alternativa retirada.
- Retiro, fusión y división muestran IDs, endpoints y parámetros de todas las
  relaciones retiradas. Fusión muestra además las relaciones creadas por unión;
  se mantienen las políticas `keep_target`/`union`, sin autorrelaciones ni
  duplicados. La confirmación existente incluye estos efectos explícitos.
- La morfología del origen se retira, sin transferencia automática; la del
  destino se conserva. Media y evidencia conservan su política previa y no
  se duplican ni transfieren automáticamente. El historial se preserva.
- División conserva la distribución exacta de assignments y crea IDs nuevos;
  no copia arbitrariamente relaciones ni morfología.
- Move individual sigue exigiendo aislamiento; component move mantiene clausura
  transitiva, identidad, assignments, occurrences y relaciones internas.
- Los cinco apply verifican permisos Reviewer/Master en backend. Analyst queda
  bloqueado; las rutas léxicas también exigen revisión.
- Se rechazan previews estructurales si el componente del origen tiene
  endpoints inexistentes/retirados, parámetros inválidos o relaciones vigentes
  entre conceptos. No se usa el retiro/fusión/división para reparar legacy.
- Una nomenclatura inconclusa bloquea la operación. Se mantiene validación A.3,
  límite de 26 variantes, ausencia de etiquetas duplicadas y recalculado de
  origen/destino. Los renumber events solo se generan si cambia el mapa.
- Una excepción revierte assignments, alternativas, relaciones, nomenclatura,
  conflictos y activity events. La lectura base de conflictos también ocurre
  dentro de la transacción.

## Verificación y fixtures legacy

`tests.test_backend_hardening` añade casos de cambios entre preview y apply
sobre todas las operaciones, pruebas HTTP con cliente crudo y tokens reales,
rechazo de POST alterados, replay, roles, ausencia de escrituras mediante trace,
comparación completa de dumps, historial irrelevante, mapa sin renumber event,
fallo de segunda nomenclatura y efectos de relaciones exactamente anunciados.

Se ejecutan los nueve módulos exigidos y se añaden
`tests.test_backend_hardening`, `tests.test_phase17b1_concurrency` y
`tests.test_immediate_acceptance`.

Resultado: **250 tests aprobados** en la regresión completa. Tras mover también
la lectura de “aceptar alternativa propuesta” dentro de la transacción de cierre,
se repite la cobertura focalizada de hardening y las dos rutas de aceptación
existente. Las pruebas usan exclusivamente SQLite sintético en memoria o en
directorios temporales; su ejecución con archivos temporales requirió salir del
sandbox, que impedía abrirlos.

Fixtures actualizados, sin debilitar las aserciones de negocio:

- `tests/form_client.py`, `test_alternative_routes.review_post` y el cierre de
  `test_submission_concept_resolution` obtienen el token del formulario/preview.
  Los tests nuevos de stale usan cliente crudo y conservan el token antiguo.
- `test_phase17b1_concurrency` parte de etiquetas A.3 canónicas y aísla el origen
  del move individual, conforme a 18C6G. Para conexiones de solo lectura usa
  la alternativa ya aislada. Sustituye el cambio de nombre visual del concepto
  por un cambio real de estructura en el destino; insertar una relación nueva
  prueba stale tras el aislamiento.
- `test_immediate_acceptance` usa contexto Flask para firmar previews y etiqueta
  inicial `1a`. Sus fallos intencionales siguen creando etiquetas duplicadas
  (`1a`), conservando las comprobaciones de conflictos y rollback.

## Limitaciones deliberadas

Sin nuevas políticas de merge, división parcial de componentes, reparación
legacy, eliminación automática de alternativas vacías, deduplicación, cambios
visuales o autenticación individual. La invalidación abarca conceptos completos
afectados porque el orden canónico puede depender de cualquiera de sus miembros.
No abarca cambios en conceptos ajenos a las opciones/propuestas de la revisión.

Las funciones internas de workflow conservan llamadas sin token para simulación
y composición transaccional ya existentes; todas las entradas HTTP de cierre
léxico suministran obligatoriamente la precondición (también si está ausente).
No se habilita aceptación inmediata para Analyst. No se amplía el protocolo
de previews de registro conceptual o gramática, fuera del alcance léxico.
