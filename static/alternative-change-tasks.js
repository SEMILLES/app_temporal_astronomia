(() => {
  const components = document.getElementById('morphology-components');
  if (components) {
    const form = document.getElementById('morphology-proposal');
    const addComponent = document.getElementById('add-component');
    const removeComponent = document.getElementById('remove-last-component');
    const controls = document.getElementById('identified-component-controls');
    const count = form.elements.component_count;
    const permutation = form.elements.free_permutation;
    let index = Number(components.dataset.nextIndex);
    addComponent.hidden = false;
    removeComponent.hidden = false;
    const refresh = () => {
      const simple = Number(count.value.trim()) === 1;
      const showPermutation = Number.isInteger(Number(count.value)) && Number(count.value) >= 2;
      document.getElementById('morphology-permutation').hidden = !showPermutation;
      permutation.disabled = !showPermutation;
      document.getElementById('component-identification').hidden = simple;
      controls.hidden = simple || form.querySelector('[name="ui_identified"]:checked')?.value !== 'yes';
      components.querySelectorAll('[data-component-row]').forEach(row => {
        row.disabled = controls.hidden;
        const existing = row.querySelector('[type="radio"]:checked')?.value === 'existing';
        const target = row.querySelector('select');
        row.querySelector('[data-existing-component]').hidden = !existing;
        target.disabled = !existing;
        target.required = existing && !controls.hidden;
        const unavailable = row.querySelector('[data-unavailable-reference]');
        if (unavailable) unavailable.disabled = !existing || !!target.value;
        const label = row.querySelector('[name$="_label"]');
        const note = row.querySelector('[name$="_note"]');
        label.setCustomValidity(!controls.hidden && !existing && !label.value.trim() && !note.value.trim()
          ? 'Describa el componente o añada una nota.' : '');
      });
      removeComponent.disabled = components.children.length <= 1;
    };
    form.addEventListener('change', refresh);
    form.addEventListener('input', refresh);
    removeComponent.addEventListener('click', () => {
      if (components.children.length > 1) components.lastElementChild.remove();
      refresh();
    });
    addComponent.addEventListener('click', () => {
      const nextPosition = Math.max(0, ...Array.from(components.querySelectorAll('[name$="_position"]'), input => Number(input.value) || 0)) + 1;
      const template = document.createElement('template');
      template.innerHTML = document.getElementById('component-template').innerHTML.replaceAll('__index__', String(index++));
      template.content.querySelector('[name$="_position"]').value = nextPosition;
      components.append(template.content);
      refresh();
    });
    refresh();
  }
  const form = document.getElementById('relation-proposals');
  if (!form) return;
  const list = document.getElementById('relation-rows');
  const add = document.getElementById('add-relation');
  const send = document.getElementById('send-relations');
  const answer = document.getElementById('relation-answer');
  const positiveControls = document.getElementById('positive-relation-controls');
  const key = (id, value) => JSON.stringify([String(id), value]);
  const blocked = new Set(JSON.parse(document.getElementById('unavailable-relations').textContent).map(([id, value]) => key(id, value)));
  add.hidden = false;
  const update = () => {
    positiveControls.hidden = answer.value !== 'YES';
    positiveControls.disabled = answer.value !== 'YES';
    document.getElementById('no-relation-explanation').hidden = answer.value !== 'NO';
    const rows = [...list.querySelectorAll('[data-relation-row]')];
    const selections = rows.map(row => ({
      target: row.querySelector('[name="target_id"]'),
      parameter: row.querySelector('[name="parameter"]')
    }));
    let valid = 0;
    rows.forEach((row, index) => {
      const {target, parameter} = selections[index];
      const otherPairs = new Set(selections.filter((_, i) => i !== index)
        .map(other => key(other.target.value, other.parameter.value)));
      row.querySelector('legend').textContent = 'Relación ' + (index + 1);
      row.querySelector('[data-remove-relation]').hidden = false;
      const unavailable = pair => blocked.has(pair) || otherPairs.has(pair);
      // Keep selected values intact, including invalid POSTs, so users can correct them.
      for (const option of parameter.options) {
        option.disabled = !!option.value && !option.selected &&
          (option.hasAttribute('data-invalid') || (!!target.value && unavailable(key(target.value, option.value))));
      }
      for (const option of target.options) {
        option.disabled = !!option.value && !option.selected &&
          (option.hasAttribute('data-invalid') || (!!parameter.value && unavailable(key(option.value, parameter.value))));
      }
      const complete = !!target.value && !!parameter.value;
      const duplicate = complete && unavailable(key(target.value, parameter.value));
      const invalid = target.selectedOptions[0]?.hasAttribute('data-invalid') || parameter.selectedOptions[0]?.hasAttribute('data-invalid');
      const message = invalid ? 'Seleccione un destino y un parámetro disponibles.' :
        duplicate ? 'Esta relación ya está vigente, en revisión o en otra fila.' : '';
      parameter.setCustomValidity(message);
      row.querySelector('[data-relation-message]').textContent = message;
      if (complete && !message) valid++;
    });
    send.disabled = answer.value === 'NO' ? false : answer.value !== 'YES' || !valid || valid !== rows.length;
  };
  form.addEventListener('change', update);
  list.addEventListener('click', event => {
    if (event.target.matches('[data-remove-relation]')) {
      event.target.closest('[data-relation-row]').remove();
      update();
    }
  });
  add.addEventListener('click', () => {
    list.append(document.getElementById('relation-row-template').content.cloneNode(true));
    update();
  });
  form.addEventListener('submit', event => {
    update();
    if (send.disabled) event.preventDefault();
  });
  update();
})();

(() => {
  const form = document.getElementById('relation-review');
  if (!form) return;
  const negative = form.dataset.negative === 'true';
  const update = () => {
    const decision = form.querySelector('input[type="radio"]:checked')?.value;
    const resolution = negative ? (decision === 'accepted' ? 'NO_CONFIRMED' : null) :
      (decision === 'pending' ? null : decision);
    let valid = !resolution;
    form.querySelectorAll('[data-relation-preview]').forEach(section => {
      section.hidden = section.dataset.relationPreview !== resolution;
      if (!section.hidden) valid = section.dataset.valid === 'true';
    });
    form.elements.review_note.required = negative && decision === 'rejected';
    document.getElementById('apply-relation-review').disabled = !decision || !valid;
  };
  form.addEventListener('change', update);
  update();
})();
