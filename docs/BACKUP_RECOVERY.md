# Fase 18B: backup y recovery

La DB del servidor será la única fuente de verdad del piloto. No editar otra DB
local en paralelo para fusionarla después. Un backup no es una copia de trabajo.

## Backup y verificación

```sh
python sqlite_backup.py backup --source /datos/lesico.db --directory /backups/lesico --reason pre-migration
python sqlite_backup.py verify /backups/lesico/ARCHIVO.db
```

`--source` puede omitirse únicamente si `LESICO_DATABASE_PATH` está configurada
explícitamente. No hay fallback al prototipo. `--directory` siempre es obligatorio.
No se necesita secret key ni importar la aplicación Flask.

Se usa SQLite Backup API con origen abierto en lectura, apta para una DB en uso.
Se verifica la copia con `integrity_check`, `foreign_key_check` y un esquema mínimo
de las ocho tablas principales. No sustituye la validación de startup de 18A.
No se activa WAL. Hay un límite de 60 segundos para la copia: si hay bloqueo o
actividad que impide completarla, el comando falla y la operación debe reintentarse.
La validación también comprueba una instantánea en memoria: SQLite 3.42 del entorno
local omite ciertas violaciones CHECK al verificar un archivo abierto con `mode=ro`.
Esto mantiene el archivo en lectura, pero requiere RAM suficiente para una copia
completa de la DB durante la validación (limitación a revisar si crece el piloto).

Cada archivo tiene nombre UTC, motivo saneado y sufijo único. Su archivo asociado
`ARCHIVO.db.manifest.json` incluye nombre, UTC, SHA256, bytes, motivo, resultados de
integridad/FK, commit del código si está disponible y metadatos de esquema
(`user_version`, `schema_version`, número de tablas). No contiene ruta del origen,
secret key ni tokens. No introducir secretos en el motivo.

Guardar DB y manifest juntos, sin modificarlos. El checksum detecta alteraciones;
no es una firma frente a alguien que pueda reemplazar ambos archivos.
Los comandos devuelven 0 en éxito y un código distinto de cero en fallo.
Verificar no escribe en el backup. El directorio requiere permisos de escritura,
espacio suficiente y soporte de hard links para publicar sin sobrescribir.

Los archivos temporales `.lesico-*.partial` se limpian ante errores e interrupciones
controladas. Una caída abrupta puede dejarlos o dejar un `.db` sin manifest: **no son
backups válidos**. Verificar siempre antes de usar; revisar esos restos manualmente
cuando no haya una operación activa. La estimación de espacio usa el tamaño de la
DB; un crecimiento concurrente o fallo de disco todavía puede hacer fallar la copia.

## Restore

1. Detener escrituras para el cambio de DB activa y seleccionar el backup.
2. Ejecutar `verify`, conservando DB y manifest juntos.
3. Restaurar a una ruta nueva cuyo directorio ya exista:

   ```sh
   python sqlite_backup.py restore /backups/lesico/ARCHIVO.db --destination /datos/lesico-restaurada.db
   ```

4. El comando verifica checksum, integridad/FK y esquema, copia los bytes del
   backup estático y vuelve a comprobar checksum e integridad/FK antes de publicar.
   No existe opción overwrite; un destino existente se rechaza.
5. Comprobar conteos y arranque de la app sobre el restaurado. Solo después cambiar
   `LESICO_DATABASE_PATH` y reiniciar los procesos mediante procedimiento operativo.
   Esta CLI nunca sustituye la DB activa ni realiza recovery automático.

## Operación posterior

RPO del piloto: aproximadamente **1 hora**, condicionado a backups horarios
exitosos y copia externa. Retención acordada: **48 horarios, 30 diarios y 8
semanales**; clasificación, programación, alertas y borrado quedan para 18C.

Una copia solo en el mismo disco/servidor no es backup externo suficiente.
El almacenamiento externo y su transferencia se configurarán al elegir hosting.
No hay scheduler ni integración con proveedores en esta fase.

Toda migration de producción requiere un backup previo verificado. Ejecutarla
una sola vez, con escrituras detenidas o un procedimiento controlado; nunca desde
cada worker. Futuras herramientas pueden importar `create_backup`, `verify_backup`
y `restore_backup` sin arrancar Flask ni modificar migrations históricas.

## Prueba local de recovery (2026-09-05)

`python tests/smoke_phase18b_recovery.py` derivó una DB descartable del baseline
post-17A mediante Backup API, creó/verificó un backup y restauró a otra ruta nueva.
Resultado: concepts 150, alternatives 228, occurrences 253, sources 44;
assignments current 253, occurrence_concept_reference current 253,
morphology current 228 y relations current 19. Integridad `ok`, FK `0`, SHA256
restaurado idéntico al backup; los cinco hashes protegidos permanecieron intactos.
Las DBs de prueba se eliminaron al cerrar el directorio temporal.
