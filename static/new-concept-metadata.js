document.querySelectorAll('.new-concept-metadata').forEach(function (editor) {
    if (editor.dataset.initialized) return;
    editor.dataset.initialized = 'yes';
    const form = editor.closest('form');
    const action = form.elements.concept_action;
    function update() {
        const enabled = !action || action.value === 'CREATE_NEW' ||
            (action.value === 'ACCEPT_PROPOSAL' && editor.dataset.proposalCreatesNew === 'yes');
        editor.hidden = !enabled;
        editor.querySelectorAll('[data-new-collection]').forEach(function (checkbox) {
            checkbox.disabled = !enabled;
        });
        editor.querySelectorAll('[data-new-system]').forEach(function (group) {
            const collection = group.dataset.collection;
            const checkbox = collection && editor.querySelector('[data-new-collection="' + collection + '"]');
            const active = enabled && (!collection || checkbox.checked);
            group.hidden = !active;
            group.disabled = !active;
            group.querySelectorAll('input, select').forEach(function (input) {
                input.disabled = !active;
                input.required = active && input.dataset.position === '1';
            });
            const selects = group.querySelectorAll('select');
            selects.forEach(function (select, index) {
                Array.from(select.options).forEach(function (option) {
                    option.disabled = Boolean(option.value && option.value === selects[1 - index].value);
                });
                select.setCustomValidity(active && select.value && select.value === selects[1 - index].value
                    ? 'Seleccione dos categorías distintas.' : '');
            });
        });
    }
    editor.addEventListener('change', update);
    if (action) action.addEventListener('change', update);
    update();
});
