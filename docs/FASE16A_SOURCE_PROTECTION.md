# Fase 16A — Protección y edición de fuentes

Migration `018_source_protection.py` agrega `analyst_protected`, entero obligatorio
con valores 0/1 y default 1, a `source` y `source_revision`. La migration es
incremental e idempotente: protege todas las fuentes existentes y no vuelve a
proteger fuentes desprotegidas al ejecutarla otra vez. El default en revisiones
anteriores es la protección inicial de la migration; las nuevas revisiones guardan
el estado real anterior a cada edición o cambio de protección.

Se conserva la clave DB `analyst_source_creation` y su administración por Master.
La interfaz ahora dice «Permitir a analistas crear y editar fuentes». ON permite
crear fuentes y editar cualquier fuente no protegida, sin depender del creador;
OFF impide ambas operaciones a Analyst. Reviewer/Master no dependen del toggle.
Las fuentes creadas por Analyst empiezan sin protección; las creadas por
Reviewer/Master empiezan protegidas. Añadir occurrences no cambia este estado.

Reviewer/Master pueden proteger directamente y desproteger mediante una página
con advertencia, Cancelar y Desproteger. El POST de desprotección exige
confirmación. Analyst no recibe controles ni etiquetas de protección; GET y POST
no autorizados devuelven 404. La autorización de edición se comprueba dentro de
la transacción de escritura antes de validar los metadatos.

Se conservan `source_created`, `source_updated` y el evento del setting. Se agregan
`source_protected` y `source_unprotected`; los eventos usan el rol efectivo y el
colaborador según el mecanismo existente. Cada edición relevante y cambio de
protección conserva la versión anterior en `source_revision`. No se crean
submissions ni estados nuevos. Cambiar el tipo conserva los Details y sus estados;
el formulario advierte sobre el cambio de semántica cuando hay occurrences.

Archivos de implementación: `database.py`, `routes/sources.py`,
`migrations/018_source_protection.py`, `templates/fuentes.html`,
`templates/editar_fuente.html`, `templates/source_protection.html` y
`templates/trabajo.html`.

Validación reproducible:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q -x '(\.venv|\.git)' .
git diff --check
.\.venv\Scripts\python.exe tests/smoke_phase16a_source_protection.py --output-dir import_inputs/astronomia/phase16a_smoke
```

`test_source_protection.py` cubre la matriz de roles, toggle y protección,
defaults, crafted requests, confirmación, activity, revisiones y conservación de
Details. Se ajustó el contexto Reviewer del test de metadata existente y el texto
del smoke Fase 15. El test privado de normalización Fase 15 ahora reproduce los
Details sin normalizar exclusivamente en su copia temporal, porque la working ya
está normalizada.

El smoke usa Playwright con Edge headless, crea y migra una copia temporal del
baseline y la elimina al terminar. Comprueba 44 fuentes existentes protegidas,
creación/edición Analyst, edición de una fuente creada por Master y desprotegida,
denegación 404, controles Reviewer/Master, cancelación y confirmación, y toggle
OFF/ON. Guarda capturas fuera de Git en `import_inputs/astronomia/phase16a_smoke`.
Playwright es una herramienta local de smoke, no una dependencia del servidor.

Working y baseline post-Fase15 conservaron antes y después el SHA256:
`DE7FE91316F66E74B49529DC8B81168619F59F333344556D663244BC8BE8A14C`.
La migration no se aplicó a la working real ni al baseline. Este cierre corresponde
a un único commit local, sin push; no incluye operaciones estructurales 16B/16C.
