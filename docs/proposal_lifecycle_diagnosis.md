# Diagnóstico del registro y las propuestas conceptuales

Base inspeccionada: `feature/analyst-work-assignment`, HEAD `7b9b2c3`.

## Fuentes de datos

- `/ocurrencias/nueva` redirige a `/aportes/nuevo`.
- `routes/submissions.py::_context()` alimenta «Concepto existente» con
  `functional_presentation.concept_options()`. Reviewer usa el mismo helper.
- Este helper selecciona toda la tabla `concept`. El `EXISTS` de Alternatives
  vigentes solo genera una anotación: no filtra Concepts. No exige Occurrences,
  clasificaciones ni procedencia legacy. La plantilla tampoco los excluye.
- «Propuesta pendiente» consulta `concept_proposal WHERE status='pending'`.
- `complete_registration()` verifica de nuevo el ID y estado dentro de su
  transacción. Rechaza Concepts inexistentes y propuestas cerradas antes de
  insertar la ocurrencia. Las pruebas HTTP verifican también formularios viejos.
- El análisis léxico consulta Alternatives por `concept_id` y
  `retired_at IS NULL`; no utiliza coincidencias de etiquetas.

En este HEAD no se reproduce la ausencia de Concepts nuevos del selector:
las pruebas cubren CREATE_NEW con y sin Alternatives y un Concept sin evidencia
ni metadata. La discrepancia observada exige comprobar por separado la versión
y base utilizadas en el entorno donde ocurrió; no se accedió a Railway ni a
producción.

## Omisión confirmada y corrección

`save_resolution()` insertaba `submission_concept_resolution` y versionaba
`occurrence_concept_reference`, pero no cerraba `concept_proposal`. Por ello una
propuesta ya revisada seguía pasando tanto el filtro como la validación de
`SELECTED_PENDING`. La aceptación conceptual inmediata ya cerraba su propuesta;
la omisión estaba en la revisión de aportes.

Ahora la primera resolución de una propuesta pending guarda `resolved`,
`resolved_concept_id`, `resolved_at` y `resolution_note` dentro de la misma
transacción, usando `alternative_submission.reference_concept_proposal_id`.
No se usa matching textual para este cambio.

Las decisiones siguen siendo locales a cada aporte. Una resolución posterior
puede escoger otro Concept sin cambiar el cierre histórico compartido. No se
reabren propuestas resolved/rejected ni se alteran referencias de otras
ocurrencias. El versionado existente conserva la referencia original y
`proposal_origin`. Un rechazo léxico posterior no revierte el cierre conceptual.

## Datos históricos: reparación pendiente de autorización

No se inspeccionó una base persistente de PRUEBAS ni se modificaron datos
históricos. No pueden atribuirse IDs concretos a NEW-CONCEPT, NEW-CONCEPT-2 o
TEST-RECHAZO-LEXICO a partir de sus nombres.

Los candidatos inequívocos a inspeccionar son propuestas todavía pending que
tienen decisiones en `submission_concept_resolution`, enlazadas por
`alternative_submission.reference_concept_proposal_id` y `submission_id`.
Debe revisarse el historial completo, incluidas las versiones superseded y las
referencias de ocurrencia. Varias decisiones locales pueden tener destinos
distintos: no debe elegirse un destino mediante etiquetas o un agregado ciego.

Una reparación separada podría cerrar cada ID aprobado usando su primera
resolución histórica documentada, copiando Concept, fecha y nota, sin modificar
aportes ni referencias. Los casos con decisiones divergentes deben presentarse
para revisión explícita antes de aplicar esa política retrospectivamente.
Este cambio no contiene migración, script de reparación ni backfill; tampoco
repara propuestas antiguas al cargar formularios o guardar decisiones idénticas.

## Validación

Se ejecutaron pruebas de registro, ciclo de propuestas, resolución conceptual,
proposal_origin, metadata, Alternatives y decisiones léxicas, excluyendo las
pruebas que ejecutan migraciones: 199 pruebas y 44 subtests aprobados, dos
pruebas de migración excluidas. `git diff --check` no reportó errores.

La ampliación a morfología, revisión léxica atómica y relaciones comparables
presentó nueve fallos preexistentes: uno en `test_alternative_morphology.py`,
siete en `test_atomic_lexical_review.py` y uno en
`test_phase18c6e_comparable_relations.py`. Los mismos nueve fallos se reprodujeron
al cargar en memoria `submission_concept_resolution.py` de `7b9b2c3`, manteniendo
los demás módulos y esas pruebas sin cambios. No se modificaron esos casos ni
su implementación, ajenos al cierre de propuestas.
