/* Same component template and row identities as classification; the existing
 * alternative has its own preload and successful-control lifecycle. */
(() => {
    const form = document.getElementById('management-morphology');
    if (!form) return;
    const count = form.elements.component_count;
    const permutation = document.getElementById('permutation-field');
    const registration = document.getElementById('component-registration-field');
    const controls = document.getElementById('component-controls');
    const list = document.getElementById('components');
    const add = document.getElementById('add-component');
    const remove = document.getElementById('remove-last-component');

    function componentType(row, active) {
        const type = row.querySelector('[data-component-type]:checked')?.value;
        const select = row.querySelector('select');
        const note = row.querySelector('textarea');
        row.querySelector('.existing-component').hidden = type !== 'existing';
        row.querySelector('.unapproved-component').hidden = false;
        if (type === 'unapproved') select.value = '';
        select.required = active && type === 'existing';
        note.required = active && type === 'unapproved';
        row.querySelectorAll('[data-component-type]').forEach(input => input.required = active);
        row.querySelector('input[type=number]').required = active;
    }

    function addComponent(saved = {}) {
        const row = document.getElementById('component-template').content.firstElementChild.cloneNode(true);
        const index = list.children.length;
        row.querySelector('legend').textContent = 'Componente ' + (index + 1);
        row.querySelectorAll('[name=component_type_INDEX]').forEach(input => {
            input.name = 'component_' + index + '_type';
            input.dataset.componentType = '';
            input.checked = input.value === saved.type;
        });
        ['position', 'alternative_id', 'note'].forEach(field => {
            const input = row.querySelector('[name=component_' + field + ']');
            input.name = 'component_' + index + '_' + field;
            input.value = saved[field] ?? (field === 'position' ? String(index + 1) : '');
        });
        // A retired historical target stays visible, but must be replaced before
        // saving: the server continues to require a current alternative.
        const select = row.querySelector('select');
        if (saved.alternative_id && !select.value) {
            select.add(new Option('Alternativa no vigente · ID ' + saved.alternative_id,
                                  saved.alternative_id, true, true));
        }
        for (const [name, value] of [['component_row_id', index], ['component_' + index + '_label', saved.label || '']]) {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = name; input.value = value;
            row.appendChild(input);
        }
        if (saved.label) {
            const label = document.createElement('p');
            label.textContent = 'Etiqueta registrada: ' + saved.label;
            row.appendChild(label);
        }
        list.appendChild(row);
        row.addEventListener('change', toggle);
    }

    function toggle() {
        const n = Number(count.value);
        const allow = count.value === 'N/A' || n >= 2;
        const showPermutation = count.value !== 'N/A' && n >= 2;
        const recording = allow && form.querySelector('[name=record_components]:checked')?.value === 'yes';
        permutation.hidden = !showPermutation;
        form.elements.free_permutation.disabled = !showPermutation;
        if (showPermutation && !form.elements.free_permutation.value) form.elements.free_permutation.value = 'SIN INFORMACIÓN';
        registration.hidden = !allow;
        form.querySelectorAll('[name=record_components]').forEach(input => input.disabled = !allow);
        controls.hidden = !recording;
        if (recording && !list.children.length) addComponent();
        [...list.children].forEach(row => {
            row.querySelectorAll('input,select,textarea').forEach(input => input.disabled = !recording);
            componentType(row, recording);
        });
        const excess = recording && count.value !== 'N/A' && list.children.length > n;
        count.setCustomValidity(excess ? 'Los componentes identificados no pueden superar la cantidad declarada. Elimine los sobrantes.' : '');
        add.disabled = !recording || (count.value !== 'N/A' && list.children.length >= n);
        remove.disabled = !recording || list.children.length <= 1;
    }
    JSON.parse(document.getElementById('management-components').textContent).forEach(addComponent);
    count.addEventListener('change', toggle);
    form.querySelectorAll('[name=record_components]').forEach(input => input.addEventListener('change', toggle));
    add.addEventListener('click', () => { addComponent(); toggle(); });
    remove.addEventListener('click', () => { if (list.children.length > 1) list.lastElementChild.remove(); toggle(); });
    toggle();

    const relation = document.getElementById('management-relation');
    if (!relation) return;
    const current = JSON.parse(document.getElementById('current-relations').textContent);
    relation.addEventListener('change', () => {
        const duplicate = current.some(([id, parameter]) => String(id) === relation.elements.target_id.value && parameter === relation.elements.parameter.value);
        relation.elements.parameter.setCustomValidity(duplicate ? 'Esta relación ya está vigente.' : '');
        document.getElementById('relation-duplicate').hidden = !duplicate;
    });
})();
