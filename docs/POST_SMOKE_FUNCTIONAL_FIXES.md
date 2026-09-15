# Cierre funcional posterior al smoke test

Rama: `feature/evidence-analysis-media`. Base comparada: `0879ca9`.

## Hallazgos y correcciones

| Hallazgo | Solución |
| --- | --- |
| Morfología incompleta sin indicación útil | Se conserva la validación canónica del servidor. El envío normal y la aceptación inmediata vuelven al formulario con una alerta específica. Si falta la cantidad se indica elegir cantidad o N/A. La validación genérica del navegador deja pasar la morfología al validador canónico. |
| Pérdida de valores al volver desde un error | Se recuperan cantidad, permutación, notas, relaciones y filas dinámicas de componentes, incluidos tipo, posición y destino. El formulario recuperado envía la revisión normal a su URL explícita aunque proceda de aceptación inmediata. |
| Primer concepto elegido implícitamente | El selector compartido de traslado individual/grupo comienza con `— Selecciona un concepto destino —`, valor vacío y `required`. La ruta rechaza destino ausente/vacío; se conserva el rechazo canónico del concepto origen. |
| Retiradas mezcladas con vigentes | Dos secciones: alternativas vigentes y alternativas retiradas. Las retiradas muestran ID, estado y “Última denominación”. No se modifica `working_label`, ocurrencias ni relaciones históricas. Se verificaron los filtros vigentes en clasificación, componentes, relaciones, retiro y fusión. |
| Conceptos sin alternativas vigentes en lista activa | Estado derivado con `EXISTS` sobre alternativas no retiradas. Se muestran en sección separada, sin eliminarlos ni duplicarlos. Siguen disponibles en registro/clasificación, resolución conceptual y traslados, con el sufijo “sin alternativas vigentes”. |
| Terminología visible | Alternativa, Concepto, Ocurrencia, fusión, retiro, división y traslado en las pantallas afectadas; referencias temporales y nombres del historial estructural traducidos al presentar. Los códigos y nombres internos se conservan. |
| Previews ordenados por ID | Copia ordenada por etiqueta actual con orden numérico natural, ID como desempate y etiquetas ausentes al final. Aplicado a las tablas estructurales de fusión, retiro, división y ambos traslados, y a las tablas de nomenclatura/relación de gestión. El cálculo no cambia. |
| Traslado individual poco claro | Muestra origen, destino, alternativa e ID, ocurrencias conservadas, relaciones afectadas y conservación de morfología/media. Comparte tablas de origen/destino con traslado de grupo e indica cuando no hay cambios de nomenclatura. No se modifica `alternative_structural.py`. |
| Confirmación comprimida | Checkbox, etiqueta “Motivo”, textarea y botón en bloques separados. El checkbox envía `confirm=yes`; ya no hay un hidden que confirme independientemente del checkbox. Se mantienen motivo, token y validación del servidor. |

## Archivos

- `functional_presentation.py`: estado conceptual derivado y utilidades de presentación.
- `routes/alternatives.py`, `routes/concepts.py`, `routes/occurrences.py`, `routes/submissions.py`.
- `templates/alternativas.html`, `templates/conceptos.html`, `templates/gestionar_alternativa.html`, `templates/clasificar_ocurrencia.html`.
- `templates/nueva_ocurrencia.html`, `templates/_submission_concept_resolution.html`, `templates/nuevo_conflicto.html`.
- `tests/test_post_smoke_functional.py`: regresiones específicas con bases sintéticas temporales y navegador Edge headless.
- `tests/test_alternative_routes.py`: una expectativa de texto actualizada de “Previsualizar movimiento” a “Previsualizar traslado”.

## Verificación

Las pruebas específicas comparan volcados completos antes/después de errores y previews para comprobar ausencia de escrituras parciales. Las pruebas de navegador comprueban la alerta tras pulsar ambos botones y la recuperación de todos los campos dinámicos; después corrigen el dato y obtienen un preview válido.

Regresión representativa ejecutada con:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_post_smoke_functional tests.test_alternative_routes tests.test_alternative_structural tests.test_alternative_workflow tests.test_alternative_morphology tests.test_alternative_admin tests.test_immediate_acceptance tests.test_immediate_acceptance_routes tests.test_submission_concept_resolution tests.test_submission_lexical_decision tests.test_canonical_nomenclature tests.test_backend_hardening
```

Las suites estructurales, de rutas y de endurecimiento incluyen traslado de componentes/grupos.

Resultado: **233 pruebas aprobadas**, incluidas **11 pruebas nuevas** (con subcasos para ambos caminos de envío y las cinco operaciones estructurales). `git diff --check` sin errores.

### Fallos previos comprobados

La ejecución ampliada de 253 pruebas incluyó además `tests.test_submission_lexical_ui` y `tests.test_phase18c6d_analysis` y reportó **19 fallos y 1 error**. Se ejecutaron esas mismas dos suites (31 pruebas) sobre un `git archive HEAD` extraído en un directorio temporal: reprodujeron **los mismos 19 fallos y 1 error**.

- Los POST de revisión de fixtures antiguos carecen de las precondiciones vigentes y reciben 409; varias expectativas esperan 302 o 400.
- Dos subcasos de confirmación de relaciones inválidas también esperan 400 y reciben 409.
- Una prueba importa `calculate_nomenclature_preview` desde `alternative_workflow`, donde ya no existe ese símbolo.

No se alteraron estas suites ni las garantías de concurrencia para ocultar sus fallos. Queda pendiente actualizar esos fixtures y la instrumentación de la prueba al contrato actual, fuera del cierre de hallazgos del smoke test.

## Cierre final post-smoke — ajustes de presentación

- Las alternativas retiradas se conservan separadas, dentro de un `details` cerrado por defecto con contador. Se mantienen ID, estado, última denominación e información histórica; se omite el bloque cuando está vacío.
- Las tablas de fusión, retiro, división, traslado individual y traslado de grupo muestran `Actual | Después | Estado`, con la convención existente: `= Sin cambio`, `↻ Cambia`, `↻ Cambia de grupo`, `+ Nueva`. El orden usa la etiqueta actual; las nuevas se ordenan por la propuesta. Solo se ordena una copia de las filas: el cálculo no cambia.
- Clasificar ocurrencia distingue el concepto de referencia de la clasificación derivada del assignment vigente, con etiqueta completa e ID. Sin assignment muestra `Sin clasificación vigente`. Las alternativas del concepto de referencia se presentan como opciones para una nueva decisión. No se sincronizan referencias ni se alteran asignaciones.
- Los errores de relaciones indican el ID histórico y distinguen destino retirado, inexistente o de otro concepto. El mismo diagnóstico aparece en revisión y en el bloqueo del POST; las condiciones de validación permanecen intactas.
- Se reemplazó evidencia por ocurrencia en el enlace de registro, borradores y confirmación inmediata. Se conservaron los identificadores internos y los comentarios históricos.
- Los conceptos sin alternativas vigentes siguen separados y disponibles como destino; sus pruebas existentes continúan pasando.

### Archivos de este cierre

`functional_presentation.py`, `alternative_workflow.py`, `routes/alternatives.py`, `routes/submissions.py`; plantillas `alternativas.html`, `gestionar_alternativa.html`, `clasificar_ocurrencia.html`, `ocurrencias.html`, `borradores.html`, `confirmar_aceptacion_inmediata.html`; `tests/test_post_smoke_functional.py`, `tests/test_alternative_routes.py` y este documento.

### Pruebas del cierre

Se añadieron cinco pruebas: referencia distinta de clasificación actual y ausencia de assignment; terminología y ausencia de sección vacía; orden de alternativas nuevas sin mutación; relación histórica retirada bloqueada en pantalla y POST sin escrituras; mensajes para destino inexistente o de otro concepto. Se ampliaron dos pruebas para comprobar el plegado/contador y los cuatro estados en las cinco operaciones. Se actualizaron dos expectativas del encabezado del comparador en `test_alternative_routes.py`.

Resultados, usando exclusivamente bases sintéticas temporales:

- `tests.test_post_smoke_functional`: **16/16**, incluidas las pruebas de navegador Edge headless.
- Suites focalizadas: **165/165** (`test_alternative_routes`, `test_alternative_workflow`, `test_occurrence_submission_decoupling`, `test_submission_concept_resolution`, `test_submission_lexical_decision`, `test_alternative_structural`, `test_canonical_nomenclature`).
- Regresión representativa: **66/66** (`test_alternative_morphology`, `test_alternative_admin`, `test_immediate_acceptance`, `test_immediate_acceptance_routes`, `test_backend_hardening`).
- Adicionalmente, `test_occurrence_grammar_routes`: **23 aprobadas y 1 fallo preexistente**. `test_review_lists_alternative_and_reject_does_not_modify_canonical` envía una revisión sin las precondiciones actuales y espera 302, pero recibe 409. Se reprodujo exactamente en una copia temporal de `git archive HEAD`; no se debilitó la validación ni se modificó esa suite.

En total: **247 pruebas aprobadas en las tandas limpias**, más las 23 aprobadas de gramática. El único fallo pendiente es el fixture preexistente descrito. `git diff --check` sin errores. Los dos archivos históricos excluidos se verificaron mediante SHA-256, sin cambios.

No se ejecutaron migraciones ni operaciones sobre bases reales, Railway, staging, commits o push. No quedan hallazgos de presentación solicitados pendientes; los cambios metodológicos siguientes permanecen fuera de alcance.

## Pendientes metodológicos y de alcance

- Alternativas activas con cero ocurrencias, retiro/suspensión automática y retirada de su etiqueta.
- Traslado parcial de un componente o ruptura de relaciones para trasladar una sola alternativa.
- Búsqueda y orden cronológico opcional de fuentes.
- Diseño visual: CSS general, tipografía, layout, responsive, colores y catálogo visual.

Sin migraciones, cambios en bases reales, Railway, staging, commits ni push. Se conservaron intactos los dos archivos excluidos por la solicitud: `docs/PHASE18C6G_A_CROSS_CONCEPT_STOP.md` y `tests/test_nomenclature_cross_concept_audit.py`. El preflight real de 270 PASS / 0 FAIL es el resultado aportado por el usuario; no se repitió sobre las bases reales.
