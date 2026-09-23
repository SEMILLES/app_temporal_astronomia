# Proveniencia conceptual y decisiones de alternativa

La migración `028_concept_reference_origin.py` añade únicamente
`occurrence_concept_reference.proposal_origin`:

- `NEW_PROPOSAL`: el registro propuso un concepto mediante su etiqueta.
- `SELECTED_PENDING`: el registro seleccionó una propuesta pendiente por ID,
  también al completar un borrador con esa selección.
- `NULL`: origen histórico desconocido o referencia directa a un concepto.

No hay backfill. La restricción SQL permite los dos valores explícitos solo
cuando existe `concept_proposal_id`. La proveniencia describe la elección del
usuario, incluso si la búsqueda por etiqueta reutiliza una propuesta pendiente.
Si esa búsqueda resuelve a un concepto existente, la referencia directa queda
con origen `NULL`.

La referencia está versionada: al resolver el concepto, la fila original pasa a
histórica y conserva su proveniencia; la nueva fila apunta al concepto resuelto.
No se añaden columnas al aporte ni a la propuesta conceptual compartida.

Solo `NEW_PROPOSAL` fuerza `NEW` en el formulario del analista y rechaza
`EXISTING`/`UNSURE` en el servicio. Los radios permanecen visibles y deshabilitados;
un campo oculto permite enviar `NEW`, pero el backend valida el origen persistido.
`SELECTED_PENDING` y `NULL` conservan las validaciones y opciones anteriores.

La revisión ofrece «Crear una nueva alternativa en el concepto resuelto».
Asignar a una existente solo se habilita si la consulta del concepto resuelto
devuelve alternativas vigentes. Se reutilizan la validación de pertenencia y
vigencia, la transacción de revisión y el token contra vistas previas obsoletas.
Un destino omitido devuelve un error de validación explícito. Cambiar el concepto
invalida el formulario anterior y la página nueva recalcula los destinos.

## Ejecución de la migración

Las bases existentes requieren la migración antes de usar este cambio. El comando
predeterminado solo simula la operación; `--apply` crea un respaldo y aplica la
columna dentro de una transacción. Es idempotente. Producción sigue bloqueada,
incluso con la variable de autorización activada.

```text
python migrations/028_concept_reference_origin.py --database RUTA_COPIA_LOCAL.db
python migrations/028_concept_reference_origin.py --database RUTA_COPIA_LOCAL.db --apply
```

Para una ejecución deliberada **dentro del entorno Railway PRUEBAS**, 028 exige
simultáneamente:

- `LESICO_MIGRATION_028_ALLOW_PRUEBAS=1` (autorización específica de esta migración).
- `LESICO_ENV=pruebas` (destino declarado).
- `RAILWAY_ENVIRONMENT_NAME=pruebas`.
- `RAILWAY_ENVIRONMENT_ID=dc6a07af-8842-44c8-a072-a0d3e10ae203`, el ID ya autorizado
  por el helper compartido.

El nombre y el ID deben proceder del entorno Railway: no sobrescribirlos para
hacer pasar la comprobación. No se presupone que cualquier Railway sea PRUEBAS.
Con `LESICO_ENV` ya configurado como `pruebas`, los comandos en ese contenedor
(shell POSIX) serían:

```sh
# Simulación: usa la ruta configurada de la base de PRUEBAS.
LESICO_MIGRATION_028_ALLOW_PRUEBAS=1 python migrations/028_concept_reference_origin.py --database "${LESICO_DATABASE_PATH:?Falta la ruta de la base de PRUEBAS}"
# Aplicación deliberada: respaldo automático con nombre único junto a la base.
LESICO_MIGRATION_028_ALLOW_PRUEBAS=1 python migrations/028_concept_reference_origin.py --database "${LESICO_DATABASE_PATH:?Falta la ruta de la base de PRUEBAS}" --apply
```

La autorización se limita a cada comando. Puede usarse `--backup RUTA_NUEVA`
para un destino de respaldo explícito y escribible. Si no se puede crear el
respaldo, la operación se cancela sin aplicar el cambio. Se conservan sin cambios
`safe_database.cli/run`, `--apply`, la transacción y `check_integrity`.

En esta entrega solo se ejecutó sobre bases sintéticas temporales de pruebas.
No se migraron bases de uso real ni se desplegó.

## Verificación

Para el ajuste de seguridad/ejecución de 028:

```text
python -B -m pytest -q -p no:cacheprovider --tb=short tests/test_migration_028_execution.py tests/test_concept_reference_origin.py
```

Resultado: **50 pruebas aprobadas**. Incluye 18 casos nuevos de autorización,
simulación, CLI, backup, integridad e idempotencia, con variables de Railway
simuladas y archivos locales sintéticos. Ninguna prueba contacta Railway.

Verificación de la entrega funcional original `76ff181`:

```text
python -B -m pytest -q -p no:cacheprovider --tb=short tests/test_concept_reference_origin.py tests/test_new_concept_metadata_browser.py tests/test_new_concept_metadata.py tests/test_concept_classification.py tests/test_submission_concept_resolution.py tests/test_submission_lexical_ui.py tests/test_alternative_workflow.py tests/test_alternative_routes.py tests/test_alternative_morphology.py tests/test_submission_lexical_decision.py tests/test_phase18c3_registration_flow.py
```

Resultado: **272 pruebas y 55 subcasos aprobados; 18 fallos preexistentes**
(14 pruebas y 4 subcasos). Las 32 pruebas nuevas de proveniencia/decisiones y las
cuatro pruebas de Chromium de `test_new_concept_metadata_browser.py` pasan,
incluidas las dos nuevas de radios bloqueados y cambio de destinos.

Los mismos 18 fallos se reprodujeron en una copia aislada de HEAD `6d5dc51`:

- En `test_submission_lexical_ui.py`, 13 pruebas y 4 subcasos envían decisiones
  sin `lexical_preview_token` y reciben 409.
- `MorphologyReviewTests.test_review_new_explicit_rejection_does_not_materialize_but_history_remains`
  espera crear una alternativa con morfología rechazada, algo que el backend de
  partida ya impide.

Esas pruebas y reglas históricas no se modifican. Las pruebas nuevas envían los
tokens correspondientes y comprueban explícitamente respuestas 400/409 sin
mutaciones ante destinos imposibles o formularios obsoletos.

Se usó el intérprete existente de `.venv` con `-B`, sin modificar ese entorno.
`TEMP` y `TMP` apuntaron a un directorio temporal dentro del worktree. La ejecución
requirió permisos ampliados por el bloqueo del sandbox sobre los directorios
creados por `tempfile`. `git diff --check` pasó.
