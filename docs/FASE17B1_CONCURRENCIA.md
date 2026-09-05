# Fase 17B1 — Concurrencia y garantías de edición

Sin migración. Se reutilizan filas canónicas, revisiones, actividad e identificadores
`is_current` existentes. Publication preview/confirm no cambia.

## Auditoría de escrituras

| Escritura humana | Estado usado como precondición | Validación y atribución |
| --- | --- | --- |
| Edición de occurrence | Fila y último `occurrence_revision_id` | Dentro de `BEGIN IMMEDIATE`; conserva revisiones, añade `changed_by` y `occurrence_updated` con actor/rol |
| Edición ordinaria de Source | Fila y último `source_revision_id` | Dentro de la transacción existente y después de comprobar permisos/protección; conserva actividad y añade `changed_by` |
| Rename de Concept | Fila y último evento `concept_renamed` | Dentro de la transacción existente; ID estable y actividad conservada; detecta también rename A→B→A |
| Gramática, aceptación inmediata | Fila `occurrence_grammar` current, incluido su ID | La marca del formulario viaja por preview y confirmación; se comprueba antes de crear submission dentro de ambas transacciones |
| Morfología administrativa | Alternative activa/contexto, versión current y componentes | Comprueba antes de reemplazar; conserva supersedes, historial y actor |
| Alta/retiro de relación administrativa | Estado del concepto y especificación de relación | Token firmado; relee participantes, relaciones y nomenclatura dentro del bloqueo |
| Nomenclatura administrativa | Estado del concepto, etiquetas, relaciones y evidencia temporal | Comprueba antes de aplicar etiquetas del formulario; conserva renumber y actividad |
| Reemplazo/retiro de video canónico | Asociación `alternative_media` current, incluido su ID | Comprueba dentro de la transacción; conserva historial, actor y rol |

Las rutas antiguas de edición libre de Alternative ya responden 404. El alta de
video comprueba que no exista current; crear registros y enviar propuestas nuevas
no reemplaza un estado canónico abierto en un formulario. Las decisiones de revisión
de propuestas pendientes conservan su workflow existente; esta subfase protege los
formularios de edición directa indicados arriba. Protección/toggle de Source,
operaciones estructurales de Source, autenticación y publicación no se rediseñan.

## Marcas de edición

Los GET obtienen formulario y precondición en una misma transacción de lectura.
`edit_concurrency.py` genera SHA-256 determinista y un token con propósito, tipo e
ID del registro. La firma reutiliza el serializer de Source, con la secret key
configurada y su fallback de proceso existente; vigencia de una hora. No hay una
tabla global de versiones ni dependencia exclusiva de timestamps de un segundo.

Los POST requieren la marca y la comparan después de adquirir `BEGIN IMMEDIATE`,
antes de escribir revisiones o actividad. Una marca ausente, inválida, expirada,
de otro registro u obsoleta produce HTTP 409:

> Este registro cambió desde que lo abriste. Recarga para revisar los cambios antes de guardar.

No hay fusión ni reenvío automático. El rechazo de formularios simples devuelve
el mensaje; la gestión administrativa vuelve a mostrar el estado actual. No se
aplican valores enviados con una marca obsoleta. Los servicios internos de
morfología/gramática/video conservan su API para workflows programáticos; las
rutas humanas siempre suministran la precondición, incluso si falta en el POST.

## Alternative: retire, merge, split y move

Los cuatro previews serializan un snapshot SQLite a una base aislada en memoria.
Las simulaciones y sus rollbacks ocurren allí: funcionan con la conexión original
en `query_only` y no incrementan sus escrituras. La evaluación de conflictos se
hace después de simular también la nomenclatura final, para evitar falsos bloqueos
por etiquetas intermedias.

La huella incluye:

- Alternative origen, conceptos origen/destino y Alternatives activas de esos
  conceptos: IDs, etiquetas, fechas de registro y estado.
- Assignments current de dichos conceptos; occurrence, referencia conceptual y
  datos temporales/de fuente que utiliza la nomenclatura.
- Relaciones current incidentes y sus identificadores/versiones.
- Morfología current y componentes del origen; también del destino en merge.
- Últimos eventos de renumeración de los conceptos afectados.

Los hermanos del concepto son relevantes porque su evidencia y relaciones pueden
cambiar la numeración final. Se excluyen notas de provenance ajenas al resultado,
videos, actividad ajena y otros conceptos. IDs virtuales de split son provisionales;
las decisiones del usuario se expresan por número de partición.

El token firmado liga propósito, Alternative origen, huella y especificación:
resoluciones de retire, destino/modo de merge, distribución/cantidad de split o
concepto destino de move. El comentario se introduce al confirmar y no altera
la operación. La ruta verifica firma y especificación antes de invocar el servicio.
Los cuatro servicios exigen `expected_fingerprint`, adquieren `BEGIN IMMEDIATE`,
releen y comparan la huella, revalidan la operación y sólo entonces escriben.
Excepciones revierten toda la transacción, incluidas revisiones, renumeración,
conflictos y actividad.

Un preview obsoleto produce HTTP 409:

> Los datos cambiaron desde la previsualización. Revise nuevamente antes de confirmar.

Analyst sigue sin acceso. Actor/rol proceden de la infraestructura actual
«Trabajando como», sin nueva autenticación.

## Validación

`tests/test_phase17b1_concurrency.py` usa clientes HTTP sin completar tokens
automáticamente. Cubre diez lectores, primer escritor, rechazo sin escrituras,
tokens manipulados/ausentes/de otro registro, current de gramática/morfología/video,
relación vigente y obsoleta, nomenclatura obsoleta, las cuatro operaciones
estructurales, cambios relevantes e irrelevantes, lectura bajo bloqueo, preview
con conexión de sólo lectura, rollback tardío y nuevo preview válido.

`tests/form_client.py` adapta las pruebas anteriores que enviaban POST sin abrir
un formulario: obtiene una marca fresca mediante GET. No altera los clientes de
las nuevas pruebas de concurrencia ni reemplaza tokens suministrados explícitamente.

Smoke reproducible: `python -m tests.smoke_phase17b1_concurrency`. Copia el baseline
post-17A a un directorio temporal, trabaja con occurrence 1 y Alternative 1 reales
de esa copia, y la elimina al terminar. Resultado: occurrence 302/409,
Alternative 409/302, sin escrituras parciales, `integrity_check=ok`,
`foreign_key_check=0`.

Validación final: suite completa con `python -m unittest discover -s tests -q`,
compileall y `git diff --check`. Resultados finales en el cierre del trabajo.

## Bases protegidas

Hashes SHA-256 esperados antes y después:

| Base | SHA-256 |
| --- | --- |
| Working real | `25AFFFEE43476569CFA894C5CAD8DDC0A517DD4A988FE9B97E956A34E32FF8C0` |
| Baseline post-17A | `25AFFFEE43476569CFA894C5CAD8DDC0A517DD4A988FE9B97E956A34E32FF8C0` |
| Candidate | `3F7540A916AEC8DE91B1FEF01DE4A10D375304C915A6C1C675B7568FE2C78158` |
| Prototype | `DCCD19EDEAF96725951F194C66014A2DCE429F18B8DB285FB0CF8B34C1C93292` |
| Write-test | `B13DB525141341CEB7DF784ECF410146C1E203676E14A1F294E46E83B5B74D36` |

Working real modificada: NO. Push: NO. Un único commit local al completar validación.

## Archivos cambiados

- `alternative_admin.py`
- `alternative_preconditions.py`
- `alternative_structural.py`
- `alternative_video_service.py`
- `docs/FASE17B1_CONCURRENCIA.md`
- `edit_concurrency.py`
- `immediate_acceptance.py`
- `routes/alternatives.py`
- `routes/concepts.py`
- `routes/occurrences.py`
- `routes/sources.py`
- `templates/editar_concepto.html`
- `templates/editar_fuente.html`
- `templates/editar_ocurrencia.html`
- `templates/gestionar_alternativa.html`
- `templates/gestionar_video_alternativa.html`
- `templates/gramatica_ocurrencia.html`
- `tests/form_client.py`
- `tests/smoke_phase17b1_concurrency.py`
- `tests/test_alternative_admin.py`
- `tests/test_alternative_structural.py`
- `tests/test_alternative_video.py`
- `tests/test_immediate_acceptance_routes.py`
- `tests/test_occurrence_legacy_provenance.py`
- `tests/test_phase17a_integrity.py`
- `tests/test_phase17b1_concurrency.py`
- `tests/test_phase6_5_consolidation.py`
- `tests/test_source_metadata_systematization.py`
- `tests/test_source_protection.py`
- `tests/test_source_retirement.py`
