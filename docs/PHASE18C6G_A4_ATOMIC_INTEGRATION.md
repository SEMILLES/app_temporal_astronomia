# 18C6G-A.4 — Integración atómica

`alternative_workflow.plan_lexical_review` normaliza la operación con la
asignación vigente, destino existente o virtual y relaciones aceptadas.
Usa `simulate_lexical_operation` de A.2/A.3 y valida todos los mapas antes de
materializar alternativas, relaciones, asignaciones o eventos.

`review_as_existing` y `review_as_new` consumen ese plan dentro de su
transacción `BEGIN IMMEDIATE` o savepoint existente. El origen se obtiene de
la asignación, aunque la resolución conceptual independiente ya indique otro
concepto. Una Alternative vacía permanece activa, conserva sus relaciones y
participa con referencia temporal desconocida. USE_EXISTING no modifica
relaciones ni morfología del destino.

Al materializar new se traduce la referencia virtual a su ID persistido.
Se comprueba la aplicabilidad de los mapas sobre la evidencia materializada
antes de aplicar el primer concepto. Se aplican los mapas del plan, sin
sustituirlos por otro cálculo. Una discrepancia o excepción revierte toda la
decisión, incluidos eventos, relaciones y asignaciones. Un savepoint fallido
conserva el trabajo previo de la transacción exterior.

Cada concepto cuyo mapa cambia recibe su propio `renumber_event` y
`renumber_change`, vinculados a la misma submission y con actividad registrada.
No se crea un evento si el mapa no cambia.

El preview ordinario de creación proyecta el mismo plan a las claves de la
plantilla legacy (`new`). Conserva también los mapas de todos los conceptos
afectados; la UI actual solo muestra el destino. La aceptación inmediata usa
los mismos cierres dentro de su preview con rollback y confirmación actuales.
No se añade un segundo algoritmo de nomenclatura.

## Manual y adjusted

Prevalece la decisión metodológica posterior al primer diagnóstico de parada:
todos los mapas pasan por `validate_concept_nomenclature`. El mapa manual legacy
corresponde al destino; el origen se recalcula automáticamente. Se conservan
la justificación y el origen manual; adjusted idéntico al automático conserva
`automatic_assisted`.

Se permiten huecos como `1a, 1c` y reordenar variantes posteriores a `a`, por
ejemplo `1a, 1d, 1b`. Se rechazan inversiones cronológicas de grupos
(`GROUP_CHRONOLOGY_MISMATCH`), números no consecutivos, compartir número entre
componentes desconectados, separar un componente, desplazar `a` de su miembro
canónico y componentes de 27 variantes. No se modifican eventos históricos.

El adaptador SQL de `alternative_nomenclature` usa el mismo validador para
administración y operaciones estructurales. Su proyección temporal conserva
la referencia mínima y el orden de registro del cálculo SQL existente; no
introduce reglas de validación distintas.

## Verificación

| Módulo tests.test_* | Tests | Resultado |
| --- | ---: | --- |
| atomic_lexical_review | 18 | PASS |
| lexical_simulation | 22 | PASS |
| canonical_nomenclature | 16 | PASS |
| alternative_workflow | 61 | PASS |
| immediate_acceptance_routes | 11 | PASS |
| alternative_routes | 17 | PASS |
| submission_lexical_ui | 20 | PASS |
| alternative_admin | 7 | PASS |
| alternative_structural | 5 | PASS |

Cobertura legacy actualizada:

- Administración: inversión cronológica ahora rechazada sin efectos;
  cobertura separada de ajustes manuales válidos, motivo e historial.
- Workflow: fixture manual de rollback usa un mapa canónico y referencia
  virtual; USE_EXISTING conserva relaciones/morfología pero recalcula labels;
  el test de nota por conflicto usa un conflicto inducido independiente de
  labels que ahora son corregidas por el plan.
- Resumen inmediato: muestra el resultado canónico `C-1a` en lugar de `C-1`.

Los tests nuevos reutilizan una copia del fixture de auditoría sin sus
expectativas del defecto. Los dos archivos de auditoría originales permanecen
intactos y fuera de esta batería; sus expectativas obsoletas no son criterios
de aceptación de A.4.

La primera ejecución de tests con archivos temporales falló por permisos del
sandbox; se repitió con el permiso de ejecución correspondiente. Los resultados
de la tabla son los finales, después de actualizar expectativas legacy.

## Límites

No hay migración, reinterpretación histórica, UI nueva, fijaciones parciales,
Recalcular ni token firmado de preview. La resolución conceptual guardada
previamente como acción independiente no se revierte al fallar un cierre.
La confirmación calcula sobre el estado vigente, sin garantía de identidad con
un preview anterior si hubo cambios intermedios. Si el estado materializado
no admite el mapa virtual (incluidos desempates de registro anómalos), se
rechaza y revierte; nunca se sustituye silenciosamente el mapa del plan.

Sin cambios en A.2/A.3, producción o Railway. Sin git add, commit ni push.
