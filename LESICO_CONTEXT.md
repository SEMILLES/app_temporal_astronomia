# Contexto arquitectónico de LeSiCo para un repositorio provisional

> **Naturaleza de este documento:** contexto orientativo y no bloqueante para personas y asistentes de desarrollo como Codex.
>
> Este archivo **no es una especificación definitiva**, **no es un `AGENTS.md` normativo** y **no obliga al repositorio provisional a implementar anticipadamente la arquitectura completa de LeSiCo**. Su propósito es ayudar a tomar decisiones compatibles con una integración futura y evitar acoplamientos innecesarios.

## 1. Propósito y relación con LeSiCo

Este repositorio contiene una solución provisional que responde a una necesidad inmediata. Puede adoptar la arquitectura, las tecnologías y el ritmo de desarrollo que resulten convenientes para cumplir ese objetivo.

Sin embargo, debe mantenerse presente este principio:

> **Cuando llegue el momento de la integración, el repositorio provisional se adaptará a LeSiCo; LeSiCo no redefinirá su arquitectura canónica para ajustarse al repositorio provisional.**

Por tanto:

- el modelo local de este repositorio no debe presentarse como el modelo canónico de LeSiCo;
- no es necesario reproducir ahora toda la arquitectura futura;
- sí conviene preservar datos, identidad y trazabilidad suficientes para poder mapear o migrar posteriormente;
- cualquier integración definitiva requerirá una revisión conjunta y un mapeo explícito hacia el esquema vigente de LeSiCo en ese momento.

## 2. Cómo debe usar Codex este archivo

Al desarrollar en este repositorio, Codex debe tratar este documento como **contexto de diseño**, no como una lista de prohibiciones ni como criterios automáticos de aceptación.

En particular:

1. Priorizar la necesidad funcional inmediata del repositorio.
2. Preferir decisiones reversibles cuando existan varias opciones equivalentes.
3. Evitar introducir dependencias innecesarias entre este repositorio y detalles todavía no consolidados de LeSiCo.
4. Preservar los datos originales y la trazabilidad cuando una simplificación local sea necesaria.
5. Señalar en documentación o comentarios las decisiones locales que probablemente requieran mapeo durante la integración.
6. No bloquear una implementación útil solo porque LeSiCo aún no haya cerrado algún aspecto de su diseño.

## 3. Decisiones arquitectónicas consolidadas

### 3.1. Núcleo conceptual

LeSiCo distingue, como mínimo, cuatro entidades conceptuales:

```text
source
  └── occurrence
        └── assignment ──> alternative
                              └── concept
```

#### `source`

Representa la fuente o conjunto documental del que proceden los datos: una colección, corpus, repositorio, canal, publicación u otro origen identificable.

La fuente **no equivale necesariamente al archivo multimedia**. LeSiCo podrá guardar referencias, URLs, metadatos y mecanismos de localización sin almacenar el video, audio o documento original.

#### `occurrence`

Representa un testimonio concreto observado en una fuente. Debe conservar el dato tal como fue recibido o registrado, además de la información necesaria para localizarlo en su contexto original: por ejemplo, glosa original, página, nombre del recurso, posición temporal, URL o identificador externo.

Una ocurrencia y su análisis posterior son cosas distintas. La evidencia de origen no debe sobrescribirse para reflejar una interpretación analítica.

#### `concept`

Representa el concepto lingüístico bajo el cual se organiza el análisis. Un concepto puede volver a entrar en análisis cuando aparecen nuevos datos, incluso si ya fue revisado en un lote anterior.

#### `alternative`

Representa una forma o alternativa lingüística asociada con un concepto. Una alternativa puede reunir ocurrencias procedentes de una o varias fuentes.

Una ocurrencia puede estar todavía sin clasificar. Cuando existe una asignación vigente, se espera que apunte a una sola alternativa; las decisiones anteriores pueden conservarse como historial, no como asignaciones simultáneamente vigentes.

### 3.2. Separación entre procedencia, análisis y workflow

LeSiCo separa tres capas que no deben confundirse:

```text
provenance / evidence   dato recibido y localización en la fuente
analysis                interpretación y clasificación lingüística
workflow                quién hizo qué, cuándo, en qué lote y con qué estado
```

Esta separación permite corregir o ampliar el análisis sin destruir el dato original, y auditar cambios sin convertir los estados operativos en propiedades intrínsecas de la evidencia.

El repositorio provisional puede usar una estructura más sencilla, pero debería evitar mezclar irreversiblemente estas capas.

### 3.3. Identidad estable

Las entidades relevantes deben tener identificadores internos estables que no dependan de etiquetas visibles susceptibles de corrección.

Como orientación mínima, conviene preservar:

- un ID estable por registro;
- el valor original recibido;
- la fuente o procedencia;
- fechas de creación y modificación;
- autor o actor cuando esté disponible;
- historial o evidencia suficiente para reconstruir cambios relevantes.

Los nombres visibles, glosas y códigos publicados no deben utilizarse como única identidad técnica permanente.

### 3.4. Lotes (`batches`)

El análisis se organiza en lotes. Un lote registra el contexto en el que se incorporó, analizó, asignó o revisó determinada evidencia.

Los conceptos pueden participar en ciclos de análisis distintos a lo largo del tiempo. Por ello, el estado de un concepto dentro de un lote no debe confundirse con una propiedad definitiva del concepto.

### 3.5. Sellado y crecimiento posterior

Cuando una alternativa ha sido aprobada, queda **sellada** como unidad estable. La incorporación de una nueva ocurrencia que corresponde a esa alternativa:

- añade evidencia;
- registra una nueva asignación en el lote correspondiente;
- no obliga a reabrir ni redefinir la alternativa;
- no obliga a reanalizar todo el concepto.

Si la nueva evidencia parece representar una forma nueva o cuestiona una clasificación existente, el concepto puede abrir un nuevo ciclo de análisis dentro del lote actual. Las alternativas previamente selladas conservan su identidad y su historia.

Las alternativas no deben borrarse para ocultar cambios históricos. Si excepcionalmente dejan de usarse, se espera una forma auditable de deprecación o sustitución.

### 3.6. PostgreSQL y Supabase como dirección futura

La dirección tecnológica prevista para LeSiCo es PostgreSQL, probablemente administrado inicialmente mediante Supabase. Esto permitirá incorporar autenticación, políticas de acceso, API, auditoría y una interfaz para analistas.

Esta decisión **no obliga** al repositorio provisional a usar Supabase ni PostgreSQL ahora. Sí recomienda:

- mantener datos relacionales exportables;
- no depender exclusivamente de estructuras opacas o difíciles de migrar;
- evitar que archivos multimedia pesados sean el único soporte de la evidencia;
- prever una exportación completa y documentada, idealmente en formatos abiertos como CSV o JSON.

## 4. Convenciones lingüísticas y técnicas

Para artefactos técnicos se adopta como dirección general:

- nombres en inglés;
- minúsculas;
- convención `lowercase_snake_case`;
- IDs y claves explícitas;
- nombres que distingan evidencia, análisis y workflow.

Ejemplos orientativos:

```text
source_id
occurrence_id
concept_id
alternative_id
original_gloss
created_at
created_by
```

La documentación destinada al equipo, los textos de interfaz, las ayudas, validaciones y mensajes para usuarios deben estar en **español**, salvo que exista una razón funcional para usar otra lengua.

Estas convenciones son una dirección para facilitar la futura integración. No justifican por sí solas una reescritura inmediata de código provisional que ya funciona.

## 5. Dirección de compatibilidad recomendada

Sin imponer un esquema concreto, se recomienda que el repositorio provisional pueda responder en el futuro estas preguntas:

- ¿De qué fuente procede cada registro?
- ¿Cuál era el valor original recibido?
- ¿Cómo se localiza el testimonio en la fuente?
- ¿Cuál es la identidad estable del registro?
- ¿Qué usuario o proceso lo creó o modificó y cuándo?
- ¿Qué parte es evidencia original y qué parte es interpretación?
- ¿Puede exportarse el conjunto completo sin depender de la interfaz?
- ¿Qué valores locales requerirán transformación para encajar en LeSiCo?

No es necesario que las tablas locales se llamen como las entidades de LeSiCo. Es suficiente con que exista una correspondencia documentable y que la información relevante no se pierda.

## 6. Asuntos todavía en diseño — no asumir como definitivos

Los siguientes puntos forman parte de la dirección de trabajo, pero **no están cerrados** y no deben convertirse en restricciones rígidas ni reproducirse como esquema canónico en este repositorio.

### 6.1. Códigos originales y códigos visibles

Está en discusión una separación aproximada entre:

```text
alternative_id          identidad técnica inmutable
original_code           código histórico inicialmente asignado
display_code_override   eventual código visible revisado
display_code            valor visible o derivado
code_history            historial de recodificaciones
```

La decisión consolidada es que la identidad real no debe depender del código visible y que los cambios deben ser auditables. Todavía está en diseño el nombre definitivo de los campos, cuándo se permite recodificar y qué workflow autoriza el cambio.

Por tanto, el repositorio provisional no debe asumir que códigos como `1a`, `1b` o `2a` son IDs internos inmutables, ni necesita implementar ahora un sistema de recodificación.

### 6.2. Redes y relaciones entre alternativas

Se prevé representar relaciones fonológicas entre alternativas mediante una estructura semejante a una red o grafo. También se ha identificado la necesidad de distinguir:

- la referencia metodológica usada durante un análisis secuencial, que puede tener dirección;
- la relación fonológica validada entre dos formas, que conceptualmente puede ser simétrica;
- el código histórico o publicado de cada alternativa, que no necesariamente expresa toda la estructura de la red.

Siguen abiertos, entre otros aspectos:

- el esquema definitivo de `alternative_relation`;
- si una relación fonológica validada debe registrar exactamente un parámetro;
- el procedimiento de validación de las aristas;
- el papel de sugerencias automáticas frente a relaciones aprobadas manualmente;
- el tratamiento y presentación de redes complejas;
- las reglas excepcionales de recodificación visible.

El repositorio provisional no necesita implementar estas redes salvo que su propia necesidad funcional lo requiera. Si lo hace, debe considerar su modelo local como provisional y exportable.

### 6.3. Workflow y estados definitivos

Está consolidada la idea general de analista, revisión, lotes, asignaciones propuestas o confirmadas y sellado de alternativas. Siguen pendientes los nombres y transiciones exactas de los estados, las reglas de completitud y las condiciones para guardar, enviar a revisión, cerrar o publicar.

No deben copiarse como definitivos ejemplos preliminares como `draft`, `in_analysis`, `ready_for_review`, `confirmed` o `sealed` sin revisar el modelo vigente durante la integración.

### 6.4. Otros bloques pendientes de modelado

También continúan en diseño:

- componentes de señas compuestas;
- campos semánticos;
- rasgos morfosintácticos;
- análisis sociolingüístico y tipos de evidencia;
- análisis semántico-pragmático;
- reglas de completitud y tratamiento de valores como `FALTA`;
- releases o cortes de publicación hacia LeSiCo-Web;
- snapshots de fuentes abiertas o en crecimiento;
- catálogos técnicos definitivos.

## 7. Qué evitar durante el desarrollo provisional

Como criterio de prudencia, conviene evitar:

- usar glosas, nombres o códigos visibles como única clave primaria;
- sobrescribir el dato original con el resultado del análisis;
- perder la procedencia al consolidar o deduplicar registros;
- borrar decisiones anteriores cuando una clasificación cambia;
- acoplar la lógica central a que los archivos multimedia estén alojados en la misma aplicación;
- presentar el esquema provisional como contrato definitivo de LeSiCo;
- implementar como definitivas decisiones expresamente marcadas como abiertas en este documento.

Estas son recomendaciones de compatibilidad, no motivos para detener el desarrollo si existe una necesidad inmediata. Cuando sea necesario desviarse, basta con que la decisión quede explícita y los datos sigan siendo recuperables.

## 8. Contrato mínimo para una integración futura

Antes de integrar este repositorio con LeSiCo se realizará una revisión específica. Como mínimo, se espera disponer de:

1. una exportación completa de los datos;
2. una descripción de las entidades o tablas locales;
3. un diccionario básico de campos y valores;
4. identificación de claves y relaciones;
5. procedencia y valores originales preservados;
6. explicación de transformaciones o normalizaciones realizadas;
7. inventario de decisiones locales que no correspondan directamente al modelo de LeSiCo;
8. mapeo probado hacia el esquema canónico vigente en ese momento.

La integración podrá adoptar una de varias formas: migrar los datos y retirar la aplicación provisional, adaptar su interfaz para trabajar sobre LeSiCo, o mantener una capa de importación. Esa decisión se tomará más adelante según el estado real de ambos proyectos.

## 9. Resumen operativo para Codex

Al trabajar aquí:

```text
resolver la necesidad actual
        ↓
preservar original + procedencia + IDs estables
        ↓
mantener separables evidencia, análisis y workflow
        ↓
documentar decisiones locales relevantes
        ↓
facilitar exportación y mapeo posterior
        ↓
integrar hacia LeSiCo cuando su esquema esté listo
```

Si una decisión de LeSiCo aparece en este archivo como abierta, Codex puede escoger una solución local razonable para el repositorio provisional, documentándola como tal y evitando presentarla como definitiva para LeSiCo.

