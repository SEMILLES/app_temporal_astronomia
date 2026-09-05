# Fase 18A: arranque de producción

Instalar `requirements.txt` en el entorno Python del servidor. No se añade ni se
elige todavía un servidor WSGI o proveedor de hosting.

Configurar antes de importar la aplicación:

- `LESICO_ENV=production` (el entrypoint `wsgi` lo establece si falta y rechaza development).
- `LESICO_DATABASE_PATH`: archivo SQLite existente en almacenamiento persistente.
  Se recomienda una ruta absoluta propia del servidor; se admite `~` y las rutas
  relativas se resuelven respecto al directorio de trabajo del proceso.
- `LESICO_SECRET_KEY`: secreto fuerte generado externamente una vez, conservado
  entre reinicios y compartido por todos los procesos. No incluirlo en el repo.

El destino para el futuro servidor WSGI es **`wsgi:application`**. Importarlo valida
la configuración y las tablas/columnas del esquema, sin migraciones ni escrituras
en la DB seleccionada. Una configuración ausente, archivo inexistente o esquema
incompatible aborta el arranque; nunca se busca otra DB. La comprobación del esquema
cubre tablas y columnas, no es una auditoría de integridad de datos o constraints.

Las conexiones mantienen `foreign_keys=ON`, espera por bloqueo de 5 segundos y
las transacciones existentes. En producción abren con `mode=rw`, que impide crear
una DB vacía si desaparece el archivo. No se cambia journal mode ni se activa WAL.

No usar `python app.py`, `flask run`, debug, secretos hardcodeados ni una DB en un
filesystem efímero para producción. La aplicación configura `DEBUG=False`;
el lanzador de producción no debe envolverla en un debugger ni sobrescribirlo.
La carpeta de la DB persistente debe permitir el journal habitual de SQLite.

Se conservan las variables de acceso existentes `LESICO_ANALYST_ROUTE`,
`LESICO_REVIEWER_ROUTE` y `LESICO_MASTER_ROUTE`: segmentos distintos, sin `/`
intermedio; si faltan, esas entradas internas no están disponibles. No se añade
autenticación ni se modifica el modelo de acceso en esta fase.

Desarrollo: `LESICO_ENV=development` o ausente al importar `app`; sin ruta explícita
se conserva la preparación del prototipo local. La secret key sigue siendo opcional
(sin ella no hay sesiones firmadas), y `python app.py` conserva debug local.

Pendiente para 18B/18C: elegir hosting y servidor WSGI, almacenamiento y permisos,
backups/restauración, operación HTTPS y despliegue. No se realiza deploy ni se
implementan esas tareas aquí.
