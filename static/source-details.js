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
        MESA_DE_TRABAJO: ['Fecha', 'Participantes', true],
        OTRO: ['Referencia en la fuente', 'Localizador en la fuente', true]
    };

    const fields = [1, 2].map(number => {
        const row = fieldset.querySelector('[data-detail="' + number + '"]');
        return {
            row,
            input: fieldset.querySelector('[name=source_detail_' + number + ']'),
            select: fieldset.querySelector('[name=source_detail_' + number + '_status]'),
            statusLabel: row.querySelector('.detail-status')
        };
    });

    const legend = document.getElementById('source-details-legend');
    const participantsHelp = fieldset.querySelector('.mesa-participants-help');
    const meetingActions = document.getElementById('mesa-meeting-actions');
    const newMeeting = document.getElementById('mesa-new-meeting');

    const override = fieldset.querySelector(
        '[name=source_detail_2_applicability_override]'
    );
    const control = document.getElementById('time-applicability');
    const checkbox = document.getElementById('time-applicability-checkbox');
    const label = document.getElementById('time-applicability-label');
    const help = document.getElementById('time-applicability-help');

    const meetingPrefill = fieldset.dataset.meetingPrefill === 'true';
    const cache = new Map();
    let currentSource = source.value;
    let applicable;

    function type() {
        return source.options[source.selectedIndex]?.dataset.sourceType || 'OTRO';
    }

    function meetingKey() {
        return 'lesico:mesa-reunion:' + source.value;
    }

    function normalizeParticipants(value) {
        return value
            .split(',')
            .map(part => part.trim())
            .filter(Boolean)
            .join(', ');
    }

    function readMeeting() {
        if (!meetingPrefill || type() !== 'MESA_DE_TRABAJO' || !source.value) {
            return null;
        }
        try {
            const raw = sessionStorage.getItem(meetingKey());
            return raw ? JSON.parse(raw) : null;
        } catch (_) {
            return null;
        }
    }

    function restoreMeeting() {
        const saved = readMeeting();
        if (!saved) return;

        if (!fields[0].input.value && saved.date) {
            fields[0].input.value = saved.date;
        }
        if (!fields[1].input.value && saved.participants) {
            fields[1].input.value = saved.participants;
        }
    }

    function rememberMeeting() {
        if (!meetingPrefill || type() !== 'MESA_DE_TRABAJO' || !source.value) {
            return;
        }

        fields[1].input.value = normalizeParticipants(fields[1].input.value);

        try {
            sessionStorage.setItem(meetingKey(), JSON.stringify({
                date: fields[0].input.value || '',
                participants: fields[1].input.value || ''
            }));
        } catch (_) {
            // El registro de la ocurrencia no depende de sessionStorage.
        }
    }

    function clearMeeting() {
        if (source.value) {
            try {
                sessionStorage.removeItem(meetingKey());
            } catch (_) {
                // La limpieza visual sigue funcionando.
            }
        }

        fields.forEach(field => {
            field.input.value = '';
            field.select.value = 'UNKNOWN';
        });

        fields[0].input.focus();
    }

    // Browser representation of source_details.effective_detail_2_applicability.
    function effective(kind) {
        if (kind === 'VIDEO_POR_SENA' && override.value === '1') return true;
        if (kind === 'VARIOS_VIDEOS_VARIAS_SENAS' && override.value === '0') {
            return false;
        }
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

    function renderMesa() {
        fieldset.hidden = false;
        legend.textContent = 'Datos de la reunión';
        control.hidden = true;
        override.value = '';

        fields[0].row.hidden = false;
        fields[1].row.hidden = false;

        fields[0].row.querySelector('.detail-label').textContent = 'Fecha';
        fields[1].row.querySelector('.detail-label').textContent = 'Participantes';

        fields[0].input.type = 'date';
        fields[1].input.type = 'text';

        fields.forEach(field => {
            field.statusLabel.hidden = true;
            field.select.disabled = true;
            field.input.disabled = false;
            field.input.required = false;
        });

        fields[0].input.removeAttribute('pattern');
        fields[1].input.removeAttribute('pattern');

        participantsHelp.hidden = false;
        meetingActions.hidden = !meetingPrefill;
    }

    function renderStandard(kind, labels) {
        fieldset.hidden = false;
        legend.textContent = 'Referencia dentro de la fuente';
        participantsHelp.hidden = true;
        meetingActions.hidden = true;

        fields[0].input.type = 'text';
        fields[1].input.type = 'text';

        fields.forEach((field, index) => {
            field.statusLabel.hidden = false;
            field.row.querySelector('.detail-label').textContent = labels[index];
        });

        fields[0].row.hidden = false;
        fields[0].select.disabled = false;

        const single = kind === 'VIDEO_POR_SENA';
        control.hidden = !single && kind !== 'VARIOS_VIDEOS_VARIAS_SENAS';

        label.textContent = single ? 'Tiempo requerido' : 'Tiempo no aplicable';
        help.textContent = single
            ? 'El video contiene varias señas o alternativas y la ocurrencia necesita una marca temporal. El campo Tiempo queda habilitado y requiere un valor válido.'
            : 'La ocurrencia corresponde a un video dedicado a una sola seña. El campo Tiempo queda deshabilitado y se registra como N/A.';

        checkbox.checked = single ? applicable : !applicable;

        fields[1].row.hidden = single && !applicable;
        fields[1].select.disabled = !control.hidden && (!applicable || single);

        if (!applicable) fields[1].select.value = 'NA';
        else if (single) fields[1].select.value = 'VALUE';

        fields[1].input.pattern = kind.includes('VIDEO') && applicable
            ? '(?:[0-9]{1,2}):[0-5][0-9](?::[0-5][0-9])?'
            : '.*';

        if (
            single &&
            fieldset.dataset.prefillVideoId === 'true' &&
            fields[0].select.value === 'VALUE' &&
            !fields[0].input.value &&
            gloss?.value
        ) {
            fields[0].input.value = gloss.value;
        }

        sync(fields[0]);
        sync(fields[1], applicable);
    }

    function render() {
        const kind = type();
        const labels = config[kind] || config.OTRO;

        if (kind === 'MESA_DE_TRABAJO') {
            renderMesa();
            return;
        }

        renderStandard(kind, labels);
    }

    checkbox.addEventListener('change', () => {
        const single = type() === 'VIDEO_POR_SENA';
        applicable = single ? checkbox.checked : !checkbox.checked;
        override.value = checkbox.checked ? (single ? '1' : '0') : '';
        fields[1].select.value = applicable ? 'VALUE' : 'NA';
        render();
    });

    fields.forEach((field, index) => {
        field.select.addEventListener('change', () => {
            if (index === 1) {
                applicable = field.select.value !== 'NA';
            }
            render();
        });
    });

    fields[1].input.addEventListener('blur', () => {
        if (type() === 'MESA_DE_TRABAJO') {
            fields[1].input.value = normalizeParticipants(fields[1].input.value);
        }
    });

    newMeeting.addEventListener('click', clearMeeting);

    source.addEventListener('change', () => {
        cache.set(currentSource, {
            override: override.value,
            fields: fields.map(field => ({
                status: field.select.value,
                value: field.input.value
            }))
        });

        const saved = cache.get(source.value);

        if (saved) {
            override.value = saved.override;
            saved.fields.forEach((field, index) => {
                fields[index].select.value = field.status;
                fields[index].input.value = field.value;
            });
        } else {
            override.value = '';

            if (
                type() !== 'MESA_DE_TRABAJO' &&
                !fields[1].input.value
            ) {
                fields[1].select.value =
                    type() === 'VIDEO_POR_SENA' ? 'UNKNOWN' : 'VALUE';
            }

            if (type() === 'MESA_DE_TRABAJO') {
                fields[0].input.value = '';
                fields[1].input.value = '';
                fields[0].select.value = 'UNKNOWN';
                fields[1].select.value = 'UNKNOWN';
                restoreMeeting();
            }
        }

        currentSource = source.value;
        applicable = effective(type());
        render();
    });

    if (gloss) gloss.addEventListener('change', render);

    if (
        type() === 'VIDEO_POR_SENA' &&
        fieldset.dataset.prefillVideoId === 'true' &&
        !fields[1].input.value &&
        override.value === ''
    ) {
        fields[1].select.value = 'UNKNOWN';
    }

    if (type() === 'MESA_DE_TRABAJO') {
        restoreMeeting();
    }

    applicable = effective(type());
    render();

    fieldset.closest('form').addEventListener('submit', () => {
        if (type() === 'MESA_DE_TRABAJO') {
            fields[1].input.value = normalizeParticipants(fields[1].input.value);

            fields[0].select.value =
                fields[0].input.value ? 'VALUE' : 'UNKNOWN';
            fields[1].select.value =
                fields[1].input.value ? 'VALUE' : 'UNKNOWN';

            rememberMeeting();

            // Los selects están ocultos para MESA, pero enviamos el estado
            // canónico explícitamente al servidor.
            fields.forEach(field => {
                field.select.disabled = false;
                field.input.disabled = false;
            });
        } else {
            // Disabled selects are omitted from POST; transmit normalized state too.
            fields[1].select.disabled = false;
        }
    });
})();
