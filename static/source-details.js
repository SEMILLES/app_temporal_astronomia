(function () {
    const source = document.querySelector('[name=source_id]');
    const gloss = document.querySelector('[name=original_gloss]');
    const fieldset = document.getElementById('source-details');
    if (!source || !fieldset) return;
    const config = {
        MATERIAL_IMPRESO: ['Submaterial / sección', 'Página', true],
        VIDEO_POR_SENA: ['Título / identificador del video', 'Tiempo', false],
        UN_VIDEO_VARIAS_SENAS: ['Título del video', 'Tiempo', true],
        VARIOS_VIDEOS_VARIAS_SENAS: ['Título del video', 'Tiempo', true],
        OTRO: ['Referencia en la fuente', 'Localizador en la fuente', true]
    };
    const fields = [1, 2].map(number => ({
        row: fieldset.querySelector('[data-detail="' + number + '"]'),
        input: fieldset.querySelector('[name=source_detail_' + number + ']'),
        select: fieldset.querySelector('[name=source_detail_' + number + '_status]')
    }));
    const override = fieldset.querySelector('[name=source_detail_2_applicability_override]');
    const control = document.getElementById('time-applicability');
    const checkbox = document.getElementById('time-applicability-checkbox');
    const label = document.getElementById('time-applicability-label');
    const help = document.getElementById('time-applicability-help');
    const cache = new Map();
    let currentSource = source.value;
    let applicable;
    function type() {
        return source.options[source.selectedIndex]?.dataset.sourceType || 'OTRO';
    }
    // Browser representation of source_details.effective_detail_2_applicability.
    function effective(kind) {
        if (kind === 'VIDEO_POR_SENA' && override.value === '1') return true;
        if (kind === 'VARIOS_VIDEOS_VARIAS_SENAS' && override.value === '0') return false;
        if (fields[1].select.value === 'VALUE') return true;
        if (fields[1].select.value === 'NA') return false;
        return (config[kind] || config.OTRO)[2];
    }
    function sync(field, enabled = true) {
        const {input, select} = field;
        input.disabled = !enabled || select.value !== 'VALUE';
        input.required = !input.disabled;
        if (input.disabled) input.value = '';
    }
    function render() {
        const kind = type(), labels = config[kind] || config.OTRO;
        fields.forEach((field, index) => {
            field.row.querySelector('.detail-label').textContent = labels[index];
        });
        const single = kind === 'VIDEO_POR_SENA';
        control.hidden = !single && kind !== 'VARIOS_VIDEOS_VARIAS_SENAS';
        label.textContent = single ? 'Tiempo requerido' : 'Tiempo no aplicable';
        help.textContent = single
            ? 'El video contiene varias señas o alternativas y la ocurrencia necesita una marca temporal. El campo Tiempo queda habilitado y requiere un valor válido.'
            : 'La ocurrencia corresponde a un video dedicado a una sola seña. El campo Tiempo queda deshabilitado y se registra como N/A.';
        checkbox.checked = single ? applicable : !applicable;
        // Effective legacy state is visual only; preserve the explicit override.
        fields[1].row.hidden = single && !applicable;
        fields[1].select.disabled = !control.hidden && (!applicable || single);
        if (!applicable) fields[1].select.value = 'NA';
        else if (single) fields[1].select.value = 'VALUE';
        fields[1].input.pattern = kind.includes('VIDEO') && applicable
            ? '(?:[0-9]{1,2}):[0-5][0-9](?::[0-5][0-9])?' : '.*';
        if (single && fieldset.dataset.prefillVideoId === 'true' &&
            fields[0].select.value === 'VALUE' && !fields[0].input.value && gloss?.value) {
            fields[0].input.value = gloss.value;
        }
        sync(fields[0]);
        sync(fields[1], applicable);
    }
    checkbox.addEventListener('change', () => {
        const single = type() === 'VIDEO_POR_SENA';
        applicable = single ? checkbox.checked : !checkbox.checked;
        override.value = checkbox.checked ? (single ? '1' : '0') : '';
        fields[1].select.value = applicable ? 'VALUE' : 'NA';
        render();
    });
    fields.forEach((field, index) => field.select.addEventListener('change', () => {
        if (index === 1 && control.hidden) applicable = field.select.value !== 'NA';
        if (index === 1 && !control.hidden) {
            applicable = field.select.value !== 'NA';
        }
        render();
    }));
    source.addEventListener('change', () => {
        cache.set(currentSource, {override: override.value,
            fields: fields.map(f => ({status: f.select.value, value: f.input.value}))});
        const saved = cache.get(source.value);
        if (saved) {
            override.value = saved.override;
            saved.fields.forEach((f, i) => {
                fields[i].select.value = f.status;
                fields[i].input.value = f.value;
            });
        } else {
            override.value = '';
            // Preserve entered values. A blank time uses the new source's normal rule.
            if (!fields[1].input.value) fields[1].select.value = type() === 'VIDEO_POR_SENA' ? 'UNKNOWN' : 'VALUE';
        }
        currentSource = source.value;
        applicable = effective(type());
        render();
    });
    if (gloss) gloss.addEventListener('change', render);
    // A new form's default VALUE option is not evidence of a legacy time.
    if (type() === 'VIDEO_POR_SENA' && fieldset.dataset.prefillVideoId === 'true' && !fields[1].input.value &&
        override.value === '') fields[1].select.value = 'UNKNOWN';
    applicable = effective(type());
    render();
    fieldset.closest('form').addEventListener('submit', () => {
        // Disabled selects are omitted from POST; transmit the normalized state too.
        fields[1].select.disabled = false;
    });
})();
