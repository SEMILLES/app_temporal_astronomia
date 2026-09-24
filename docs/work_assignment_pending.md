# Asignación de trabajo: resumen de pendientes

La pantalla de Reviewer/Master obtiene los pendientes de `concept_diagnostics()`
en `work_assignments.py`. Antes, paginaba todos los Concepts por nombre y mostraba
solo sus diagnósticos, sin resumen global ni filtros por tipo.

## Semántica existente, conservada

| Identificador | Unidad de trabajo | Condición actual | Concept |
| --- | --- | --- | --- |
| `morphology` | Alternative | Activa, sin `alternative_morphology.is_current=1` | `alternative.concept_id` |
| `relations` | Alternative | Activa, etiqueta con sufijo b–z, sin relación vigente en ninguno de sus extremos | `alternative.concept_id` |
| `grammar` | Occurrence | Asignación vigente a Alternative activa, sin `occurrence_grammar.is_current=1` | Concept de la Alternative asignada |
| `assignment` | Occurrence | Concept Reference vigente, sin asignación vigente | `occurrence_concept_reference.concept_id` |

Son cuatro diagnósticos, no cuatro clases nuevas de aportes. Una misma Alternative
puede tener una tarea de morfología y otra de relación; son trabajos diferentes.
Los EXISTS evitan multiplicar Alternatives por evidencias o relaciones. Los índices
únicos de asignación y referencia vigentes impiden duplicar Occurrences. El resumen
cuenta las listas canónicas sin incorporar JOINs nuevos.

`pending_changes()` adjunta aportes MORPHOLOGY/RELATION en revisión. No añade tareas
ni suprime el pendiente lingüístico. `review_proposal()` al aceptar materializa la
morfología o relación vigente. Rechazar resuelve el aporte, pero no completa los
datos faltantes: el diagnóstico puede continuar pendiente. La gramática desaparece
al crear una gramática vigente (`resolve_grammar_submission()` al aceptar); la
asignación desaparece al crear una asignación vigente (`create_or_replace_assignment()`).

No se infiere el Concept desde Source. Las Occurrences sin referencia y sin
asignación no entran en ninguno de estos grupos. Una Occurrence ya asignada tiene
un Concept conocido por su Alternative, incluso si carece de referencia; se
mantiene ese comportamiento de Gramática. No se crea una cola nueva.

## Filtros y presentación

`/administracion/asignaciones?concept_id=123&work_type=morphology` filtra en el
servidor. Tipos admitidos: vacío (todos), `morphology`, `relations`, `grammar`,
`assignment`; las etiquetas son españolas. Parámetros malformados devuelven 400;
un Concept inexistente o fuera del conjunto devuelve una lista vacía.

La búsqueda y los filtros de asignación/analista definen el conjunto inicial. El
selector incluye sus Concepts con pendientes, ordenados por total descendente,
etiqueta ascendente e ID como desempate final. El resumen responde al Concept
elegido y conserva los cuatro conteos generales, independientemente del tipo y de
la página. El detalle aplica Concept y tipo conjuntamente; su contador incluye
todas las páginas. Se conserva el orden canónico dentro de cada Concept.

Asignar, retirar y paginar conservan los filtros. Las asignaciones administrativas
siguen siendo Concept ↔ colaboradores, con historial, CSRF y permisos existentes
de Reviewer/Master. Mi trabajo conserva su alcance personal y su listado de
Concepts asignados, incluidos los que todavía no tienen alternativas: no se
extiende el acceso del Analista a la administración.

## Límites

No hay cambios de esquema ni migraciones. Se reutilizan los diagnósticos completos
en lotes de Concepts y se agregan en memoria antes de paginar. El coste crece con
el trabajo del conjunto consultado; para volúmenes mayores convendría medirlo antes
de optimizar el servicio canónico, sin duplicar sus reglas en otra query.

La pantalla ahora oculta Concepts con cero pendientes, también si conservan
asignaciones administrativas; no borra esas asignaciones ni altera su historial.
