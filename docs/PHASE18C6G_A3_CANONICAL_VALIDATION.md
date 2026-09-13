# 18C6G-A.3 — Validación canónica fuerte

## API y contrato

`lexical_simulation.validate_concept_nomenclature(concept_state, labels)` recibe
el estado posterior efectivo y un mapping completo de refs a labels. No consulta
DB, reloj ni escribe. Devuelve labels normalizadas con strip, conflicts con códigos
estables y refs, warnings, valid, conclusive y applicable. Conclusive indica que
el cálculo es posible; un mapa manual incorrecto puede ser concluyente pero nunca
válido ni aplicable. No se acepta un cálculo externo potencialmente obsoleto.
El simulador reutiliza internamente su propio cálculo para la validación.

La única definición fuerte está en este validador: formato, cobertura de todas
las Alternatives activas (incluidas vacías y virtuales), exclusión de retiradas
y refs ajenas, unicidad por concepto, equivalencia entre componente y número,
numeración consecutiva, cronología de grupos y exactamente una a en el miembro
más antiguo. Letras posteriores pueden tener huecos u otro orden.

Componentes, orden de miembros y grupos proceden del cálculo compartido existente:
occurrence_year, año único de fuente, inicio de rango, desconocidos al final y
registration key técnica. El parser se comparte con la validación productiva.
Warnings quedan diferidos: se devuelve una lista vacía, sin nuevos bloqueantes
para incertidumbre temporal, rangos, desempates o letras b/c/d.

26 miembros generan a…z. Más de 26 producen VARIANT_CAPACITY_EXCEEDED,
mapa automático vacío para todo el concepto, conclusive=false, valid=false y
applicable=false. El cálculo SQL compartido también deja de generar caracteres
fuera de a…z. La validación productiva tiene una guarda explícita de capacidad
antes de cualquier escritura de apply_nomenclature. El resto de sus reglas
históricas se conserva deliberadamente hasta A.4.

## Adaptación productiva pendiente para A.4

No se ha conectado el simulador a escrituras. validate_final_labels es el
adaptador SQL legado, no una segunda definición canónica fuerte. En A.4 deberá
recibir el ConceptState posterior, invocar validate_concept_nomenclature,
convertir conflictos a InvalidNomenclatureError y usar labels normalizadas
únicamente si applicable. No reconstruir otro estado con consultas por concepto.
La aplicación atómica de varios conceptos sigue pendiente.

- alternative_workflow: cierre que llama apply_nomenclature con required_edges;
  review_as_new/review_as_existing y aceptación inmediata conservan su flujo.
- alternative_admin.apply_relation_change y apply_direct_nomenclature pasan por
  apply_nomenclature → validate_final_labels. Quedarán cubiertas por ese adaptador;
  no se añaden reglas canónicas en administración. Sus controles de motivo,
  estado concurrente y conflictos posteriores son independientes de este mapa.
- routes/alternatives.py: acción apply_nomenclature y operaciones administrativas
  deberán consumir la validación del estado posterior en A.4; sin cambios de UI.
- alternative_structural._nomenclature también aplica a través del mismo
  adaptador. Sus previews/escrituras estructurales quedan pendientes de A.4.

La integración fuerte inmediata cambiaría mapas manuales admitidos por el legado
(por ejemplo números con huecos); se difiere explícitamente para evitar ampliar
silenciosamente el alcance productivo. El único endurecimiento productivo de
A.3 es la capacidad máxima, con prueba de rechazo sin escritura.

## Verificación

Tests nuevos: tests/test_canonical_nomenclature.py (16 métodos con subcasos para
las invariantes, formatos, 26/27, incertidumbre, empate, vacías, virtuales y refs
retiradas/ajenas). tests/test_lexical_simulation.py añade validación independiente
cross-concept y capacidad en preview/apply sin escritura; conserva prueba SQL
authorizer que prohíbe escrituras.

Módulos ejecutados en procesos individuales:

| Módulo tests.test_* | Tests | Resultado |
| --- | ---: | --- |
| canonical_nomenclature | 16 | PASS |
| lexical_simulation | 22 | PASS |
| alternative_workflow | 61 | PASS |
| alternative_routes | 17 | PASS |
| immediate_acceptance_routes | 11 | PASS |
| alternative_nomenclature_migration | 2 | PASS |
| alternative_admin | 6 | PASS |
| alternative_structural | 5 | PASS |
| typed_submission_workflow_migration | 11 | PASS |
| submission_lexical_ui | 20 | PASS |
| submission_lexical_decision_migration | 5 | PASS |
| submission_lexical_decision | 15 | PASS |
| submission_concept_resolution | 20 | PASS |
| occurrence_submission_decoupling | 9 | PASS |
| phase17a_integrity | 5 | FAIL: 4 pasan, 1 error |

El error de integridad es el blueprint concepts registrado dos veces en
test_concept_permissions_and_atomic_history. Se reprodujo cargando en memoria
alternative_nomenclature.py de HEAD, sin reemplazar archivos de trabajo.
No hay SKIP en estos módulos. No se ejecuta la auditoría cross-concept pendiente.

Riesgos abiertos: integración fuerte de mapas productivos diferida; error previo
de integridad; warnings no implementados. No hay migración, despliegue ni cambios
en los dos archivos de auditoría sin seguimiento. Sin git add, commit ni push.
