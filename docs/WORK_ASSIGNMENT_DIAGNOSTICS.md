# Diagnóstico de trabajo por concepto

Asignación de trabajo y Mi trabajo comparten cuatro pendientes calculados al leer
la página, sin estados persistidos ni escrituras lingüísticas:

- Morfología: alternativa activa sin morfología vigente; bloqueante. No exige
  materializar todos los componentes de una morfología compuesta.
- Relaciones fonológicas: alternativa activa con sufijo b-z sin relación vigente;
  bloqueante. Basta participar en cualquiera de los extremos. No se exige una
  fila inversa; se admiten varias relaciones. El sufijo a no exige relación.
- Gramática: ocurrencia con asignación vigente a una alternativa activa del
  concepto, sin gramática vigente; no bloqueante.
- Asignación a alternativa: ocurrencia con referencia conceptual vigente directa
  al concepto y sin asignación vigente. Es trabajo pendiente, no un error de
  integridad. Una referencia histórica o ausente no basta para incluirla aquí.

Un concepto sin alternativas sigue siendo trabajo válido y asignable. Las filas
históricas no satisfacen pendientes actuales. Se realizan hasta cuatro consultas por
lote de conceptos: alternativas con EXISTS de morfología/relaciones, gramática
faltante, asignación faltante y aportes pendientes. No hay consultas por fila.

Los enlaces de Analyst usan `mode=morphology` o `mode=relation` para abrir solo
esa tarea. “En revisión” se deriva de aportes pendientes y no reduce el conteo
canónico. El backend serializa creación y detección de duplicados: una morfología
pendiente por alternativa, o una relación pendiente por par simétrico/parámetro.
El envío de varias relaciones crea aportes independientes; cada aprobación revisa
la vista previa vigente. Las migraciones no cambian.

## Herramientas y permisos

Reviewer y Master reciben enlaces a `/alternativas/<id>/gestionar#morfologia`
y `#relaciones`. Analyst recibe `/alternativas/<id>/proponer` con formularios de
morfología y relaciones, incluso sin evidencia asociada. Cada propuesta crea un
submission ALTERNATIVE_CHANGE pendiente, sin Occurrence artificial. Aparece en
Aportes y Aportes pendientes, con autor, objeto, propuesta y decisión histórica.

Gramática usa `/ocurrencias/<id>/gramatica`; asignación usa
`/ocurrencias/<id>/clasificar`. Sus POST normales generan Aportes pendientes.
Reviewer/Master conservan sus herramientas de aceptación inmediata y gestión
canónica. Analyst no obtiene permisos canónicos nuevos.

## Aportes de alternativa y migración 027

La migración 027 amplía submission: el objeto es una Occurrence para los tipos
históricos o una Alternative para ALTERNATIVE_CHANGE. El subtipo conserva el
payload normalizado, concepto/etiqueta al proponer, colaborador, rol y decisión.
La reconstrucción SQLite preserva filas, IDs, índices, triggers y AUTOINCREMENT;
usa el ejecutor existente con simulación, respaldo y validación de integridad.
Las bases existentes requieren aplicar 027 explícitamente antes del arranque.

La aprobación reutiliza los servicios canónicos en una transacción: morfología
con supersedes y componentes; relación low/high con nomenclatura e historial.
Si el estado canónico cambió, se rechaza la aprobación y se pide un nuevo aporte.
El rechazo conserva el aporte y no modifica datos canónicos. Se admite agregar
relaciones; el retiro/reemplazo mediante Aporte queda pendiente.

Los pendientes de Occurrence, sus encabezados y las evidencias de los nuevos
formularios muestran fuente con `legacy_source_code` entre paréntesis al inicio,
localizador aplicable y enlace a la evidencia disponible. Se reutilizan las reglas
de source_details, sin consultas adicionales por fila ni marcadores UNKNOWN/NA.

Asignar a una alternativa existente conserva sus pendientes propios. Al aprobar
una alternativa nueva se exige morfología aprobada, incluso para propuestas
heredadas o con nota de revisión. Si la etiqueta final tiene sufijo b-z se exige
una relación vigente. El fallo revierte toda la operación. Se conserva el flujo
actual de propuestas y revisión, sin añadir formularios.

Quedan fuera el flujo por fuentes, ocurrencias sin referencia conceptual para
asignación, trazabilidad, localización, campos semánticos y colecciones.
Las migraciones 025 y 026 permanecen intactas.

## Validación

Las pruebas focalizadas cubren referencias/asignaciones vigentes e históricas,
relaciones en ambos extremos y múltiples relaciones sin duplicado inverso,
morfología parcial, gramática, tres consultas constantes, permisos y enlaces.
Los GET preservan el volcado de la base sintética; los POST de Analyst crean
Aportes sin alterar las tablas canónicas. La regresión de revisión léxica cubre
creación con morfología, rechazo sin escrituras y atomicidad de la revisión.

Resultado de esta revisión: las 26 pruebas focalizadas y las 62 de
`test_alternative_workflow.py` pasan. La selección adicional de interfaz léxica,
gramática y atomicidad conserva 25 fallos (incluidos subtests), reproducidos con
el código y pruebas originales del commit base 5ce0ec3. Corresponden a respuestas
409 en revisión y expectativas sobre alternativas retiradas; no se corrigen en
esta tarea. Los fixtures de creación nuevos incluyen ahora morfología aprobada.
