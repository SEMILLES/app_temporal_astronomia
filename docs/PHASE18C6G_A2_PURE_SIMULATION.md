# 18C6G-A.2: estado posterior puro

Infraestructura independiente en `lexical_simulation.py`. No se conecta a
review_as_new, review_as_existing, immediate acceptance ni rutas administrativas.
No aplica labels ni interpreta decisiones manuales/adjusted.

## API

`LexicalOperation` indica occurrence, assignment_id vigente esperado (None exige
ausencia de assignment), destino, concepto destino, alternativas virtuales,
relaciones autorizadas nuevas, IDs de relaciones canónicas a retirar y cambios
explícitos de vigencia. Destino None significa conservar el assignment; un rechazo
sin efectos canónicos se representa sin destino ni otros cambios.

`load_lexical_state(connection, operation)` solo ejecuta SELECT. Deriva el alcance
desde el assignment canónico, destino, nuevas alternativas, endpoints de relaciones
añadidas/retiradas y cambios de vigencia. Carga las alternativas de esos conceptos,
sus assignments actuales, evidencia de occurrence/source y relaciones vigentes.
No consulta occurrence_concept_reference ni copia submissions, medios o historial.

`LexicalState`, `ConceptState`, `Alternative`, `Occurrence`, `Assignment`, `Relation`
y los modelos de intención son dataclasses congeladas con colecciones tuple.
`build_effective_state(state, operation)` devuelve otro snapshot sin conexión SQL.
Mover O de A a B cambia únicamente el assignment de O, conservando toda la evidencia
restante de B. La evidencia de A se recalcula desde los assignments posteriores.
Una A vacía conserva vigencia y relaciones, con referencia temporal desconocida.

`derive_affected_concepts` obtiene roles desde los efectos explícitos: origen,
destino, creación, relación y vigencia. Un traslado cross-concept calcula ambos
conceptos completos. Un rechazo sin efectos y una reasignación al mismo destino
sin otros cambios no generan conceptos afectados.

`calculate_concept_nomenclature(ConceptState)` reutiliza temporal_reference y el
algoritmo automático extraído a `calculate_nomenclature_rows`: componentes, orden
de grupos y variantes, _temporal_key y _registration_key. El preview SQL existente
también reutiliza este núcleo; conserva sus reglas y añade componentes al resultado.

Las referencias virtuales son `new_alternative:N`, con virtual_order entero positivo
y único. No se inventan IDs ni timestamps. Su registration key comienza en 2,
después de las claves existentes (0/1), y ordena por virtual_order. Este orden solo
desempata referencias temporales, tanto entre grupos como entre variantes.
Una materialización futura deberá preservar ese orden explícito.

Las relaciones mantienen identidad y parámetro. Una retirada elimina solo el ID
indicado. La conectividad se calcula desde las relaciones finales y solo entre los
nodos vigentes del concepto; otra relación vigente entre el mismo par conserva la
arista. Rechazar una propuesta no implica retirar una relación canónica.

`simulate_lexical_operation` devuelve affected_concepts (roles, automatic_labels,
preview_rows con referencia temporal, components, conflicts, warnings, conclusive),
assignment_effects, relation_effects y effective_state. Después de cargar el estado,
todo el cálculo ocurre en memoria. No usa transacciones, savepoints ni rollback.

## Verificación y alcance pendiente

Los contratos nuevos están en `tests/test_lexical_simulation.py`, con SQLite en
memoria. Incluyen authorizer que deniega DML, DDL y transacciones; comparación del
dump y total_changes; transformación con conexión cerrada; paridad con el cálculo
existente; traslados, evidencia acumulada, alternativas vacías, claves virtuales,
relaciones paralelas y vigencia explícita.

Se comprueban precondiciones básicas (assignment esperado, destino/concepto,
referencias cargadas y unicidad virtual). La validación fuerte completa de A.3
queda pendiente: conflictos de dominio, límites de variantes, compatibilidad de
relaciones y restricciones manuales. conflicts/warnings/conclusive conservan por
ahora el comportamiento automático existente; no certifican autorización de un
workflow. A.4 deberá integrar materialización, concurrencia y cierres reales.

Los dos archivos de auditoría originales permanecen intactos y sin seguimiento
(opción A). Sus assertions del defecto no son contratos del simulador nuevo.
