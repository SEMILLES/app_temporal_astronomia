# Fase 17A: integridad del workflow

No hay migraciones ni cambios de schema. No se aplica el backfill a la working real.

## Componentes

El formulario identifica cada fila con `component_row_id` y nombres
`component_<id>_<campo>`. Cada grupo de radios tiene nombre independiente.
El parser exige campos completos, identidades únicas y valores únicos por campo.
Las listas de formularios antiguos solo se aceptan si están completas y alineadas;
no se usa `zip` para componentes. La validación morfológica existente comprueba
posiciones y referencias vigentes. Se mantienen las reglas de cantidad 1 y N/A.
Reviewer ve posiciones, IDs, estado dudoso y notas; la aprobación y el versionado
usan las tablas existentes de componentes.

## Concepts

Crear, abrir edición y renombrar requieren Reviewer o Master; Analyst recibe 404
en las rutas de escritura y edición y no ve esos controles. El rename conserva
`concept_id`. Cada creación o cambio efectivo registra `concept_created` o
`concept_renamed` en `activity_event`, en la misma transacción, con ID, valores
anterior/nuevo en JSON, colaborador (si se identifica), snapshot de su nombre,
rol del acceso y fecha real. No se inventa un actor cuando no está identificado.

## Normalización privada

Copiar el baseline post-Fase16B a una ruta descartable y ejecutar:

```powershell
.\.venv\Scripts\python.exe -m normalization.phase17a_concept_references RUTA_COPIA.db
.\.venv\Scripts\python.exe -m normalization.phase17a_concept_references RUTA_COPIA.db --apply
```

El primer comando solo calcula el plan. El segundo crea referencias ausentes
mediante `occurrence -> assignment current -> alternative -> concept_id`, en una
operación atómica. Conserva referencias existentes; rechaza assignments ausentes
o ambiguos y referencias históricas sin versión current. Cada inserción registra
`occurrence_concept_reference_backfilled`, origen `phase17a_import_normalization`,
actor `normalizer`, rol administrativo, IDs utilizados y fecha actual.

La prueba sobre copia privada verifica 253 referencias, coincidencia exacta con
los assignments previos, cero cambios en segunda ejecución y comparación de todas
las otras tablas salvo actividad y secuencias. También inicia una submission de
reanálisis y comprueba `integrity_check` y `foreign_key_check`.

## Hashes protegidos antes y después

Validación final: 27 tests dirigidos existentes y 5 específicos correctos;
suite completa ejecutada una vez, 361 tests correctos. Smoke de tres filas en
Edge headless correcto. `compileall` y `git diff --check` correctos.
En copia: `integrity_check = ok`, `foreign_key_check = []`, 253/253 referencias
creadas y segunda ejecución con 0 cambios. Working real no modificada; sin push.

| Base | SHA-256 |
| --- | --- |
| Working real | `AADAEAABF2F69285C2153179B55E06C827F45698D420EA3703E114FE79252686` |
| Baseline post-Fase16B | `AADAEAABF2F69285C2153179B55E06C827F45698D420EA3703E114FE79252686` |
| Candidate | `3F7540A916AEC8DE91B1FEF01DE4A10D375304C915A6C1C675B7568FE2C78158` |
| Prototype | `DCCD19EDEAF96725951F194C66014A2DCE429F18B8DB285FB0CF8B34C1C93292` |
| Write-test | `B13DB525141341CEB7DF784ECF410146C1E203676E14A1F294E46E83B5B74D36` |
