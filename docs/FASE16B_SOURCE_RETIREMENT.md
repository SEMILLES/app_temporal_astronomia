# Fase 16B — Retiro y migración de fuentes

Implementa exclusivamente el retiro lógico de una Source y el traslado de todas
sus occurrences a un destino vigente, existente o creado dentro de la operación.
Reviewer y Master pueden operarlo; Analyst recibe 404 en GET/POST estructurales.
No incluye divisiones, selecciones parciales, fusión A+B→C ni reactivación.

## Schema y conservación

Migration `019_source_retirement.py` añade `retired_at TEXT` a `source` y
`source_revision`. NULL significa vigente; una fecha indica retiro. Todas las
Sources existentes permanecen vigentes al migrar. No se elimina ninguna Source.
La migration es incremental, verifica el esquema post-018 y es idempotente.

Tres triggers impiden asignar occurrences a Sources retiradas y retirar una
Source que aún tenga occurrences. Los selectores, la edición/protección ordinaria,
el registro de evidencia y la validación de borradores excluyen las retiradas.
Los borradores existentes se conservan; para completarlos tras el retiro hay que
elegir una Source vigente. No se trasladan automáticamente, pues no son occurrences.

Cada occurrence mantiene su ID y obtiene una `occurrence_revision` con su estado
anterior, incluida Source, Details, statuses, notas, flags y año. El traslado solo
cambia `source_id`, el año si se eligió explícitamente eliminarlo, y `updated_at`.
No altera assignments, Alternatives, gramática estructurada ni snapshots publicados.
El catálogo live obtiene la Source destino de la occurrence actual.

## Preview, confirmación y atomicidad

El preview usa una transacción de lectura; no simula escrituras ni crea el destino.
Muestra origen/destino, IDs, tipos, periodos, cantidad de occurrences, conservación
literal de Details y conflictos de años. El nuevo destino obtiene su ID al confirmar.

Un token firmado liga origen, destino propuesto, metadata nueva, rol y huella del
estado completo de ambas Sources y de todas las occurrences origen. Caduca en una
hora. Usa `SECRET_KEY` si está configurada; en caso contrario usa una clave aleatoria
del proceso, por lo que reiniciar invalida previews pendientes y exige repetirlos.

La confirmación exige checkbox explícito y motivo no vacío. Dentro de
`BEGIN IMMEDIATE` se reconstruye el preview y se compara su huella. Si hay cambios
relevantes, se rechaza. Creación/revisión del destino, revisiones y traslado de
occurrences, retiro del origen y activity hacen un único commit; cualquier fallo
revierte la operación completa.

## Periodos y años

Se conserva la semántica existente: cada límite registrado restringe el año; un
límite NULL no lo restringe. Sin límites no hay conflictos de rango.

Ante conflictos se exige ampliar únicamente los extremos conocidos necesarios,
sin reducir el rango, o eliminar solo los años fuera de rango. Las occurrences
dentro del rango conservan sus años. La ampliación versiona la Source destino.
En periodos abiertos se conserva el estado `ongoing`/`unknown` y el extremo NULL.

La edición ordinaria de Source bloquea el guardado de un nuevo periodo que deje
años fuera de rango. Muestra IDs/años afectados y permite volver a editar el
periodo para incluirlos. No elimina años ni introduce submissions.

## Activity e historial

Un evento `source_retired` agrupa origen, destino, motivo, cantidad e IDs de
occurrences, IDs de revisiones, creación del destino, periodo anterior/propuesto y
años eliminados. El mecanismo de activity conserva colaborador, rol y fecha.
Una Source destino nueva genera además el evento habitual `source_created` y queda
protegida contra Analyst. Las revisiones de Source conservan el estado anterior.
La relación A→B vive en el evento, no en columnas genealógicas de Source.

Reviewer/Master acceden a fuentes retiradas y al historial de origen o destino
desde Fuentes. Las fuentes retiradas no ofrecen controles de edición ordinaria.

## Archivos

- Schema: `database.py`, `source_retirement_schema.py`, `migrations/019_source_retirement.py`.
- Operación: `source_structural.py`, `routes/source_retirement.py`.
- Integración: `routes/sources.py`, `routes/occurrences.py`, `routes/submissions.py`,
  `occurrence_registration.py`, `source_period.py`.
- Formulario compartido: `source_forms.py`, `templates/_source_creation_fields.html`.
- UI: `templates/fuentes.html`, `templates/retirar_fuente.html`,
  `templates/fuentes_retiradas.html`, `templates/source_history.html`,
  `templates/source_operation_error.html`, `templates/source_period_conflicts.html`.
- Validación: `tests/test_source_retirement.py`, `tests/smoke_phase16b_source_retirement.py`.
- Cierre: este documento.

## Validación

Los 22 tests nuevos cubren permisos, motivo, destinos, fuentes vacías, preview sin
escrituras, tokens inválidos, confirmación, concurrencia, todos los Details/statuses,
años/rangos abiertos, snapshots, catálogo, guards SQL y rollback con fallo inyectado
tras mover parcialmente occurrences y tras retirarlas.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git)' .
git diff --check
.\.venv\Scripts\python.exe tests/smoke_phase16b_source_retirement.py --output-dir import_inputs/astronomia/phase16b_smoke
```

El smoke usa Edge headless y una copia temporal del baseline post-16A. Crea datos
ficticios independientes de las Sources reales para los cuatro casos: destino
existente, ampliar periodo, eliminar años afectados y destino nuevo. Comprueba
Analyst, selectores e historial, `integrity_check=ok` y cero violaciones FK.
Las capturas quedan en `import_inputs/astronomia/phase16b_smoke`, fuera de Git.

## Bases protegidas

No se aplicó migration 019 a la working real. Se verificaron antes y después:

| Base | SHA256 intacto |
|---|---|
| Working y baseline post-16A | `EF1BFB5A251BC181EEA95F9A614A0C545AEC80F7DB4D88702F8FAE38FE574F32` |
| Candidate | `3F7540A916AEC8DE91B1FEF01DE4A10D375304C915A6C1C675B7568FE2C78158` |
| Prototype | `DCCD19EDEAF96725951F194C66014A2DCE429F18B8DB285FB0CF8B34C1C93292` |
| Write-test | `B13DB525141341CEB7DF784ECF410146C1E203676E14A1F294E46E83B5B74D36` |

Cierre de desarrollo mediante un único commit local. Sin push ni aplicación a
la working real en esta fase de desarrollo.
