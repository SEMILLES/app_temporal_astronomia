(() => {
  const form = document.getElementById('relation-proposals');
  if (!form) return;
  const target = form.querySelector('[name="target_id"]');
  const parameter = form.querySelector('[name="parameter"]');
  const list = document.getElementById('prepared-relations');
  const add = document.getElementById('prepare-relation');
  const send = document.getElementById('send-relations');
  const message = document.getElementById('relation-message');
  const key = (id, value) => JSON.stringify([String(id), value]);
  const blocked = new Set(JSON.parse(document.getElementById('unavailable-relations').textContent).map(([id, value]) => key(id, value)));
  const prepared = new Set();
  target.removeAttribute('name');
  parameter.removeAttribute('name');
  add.hidden = false;
  const update = () => {
    const duplicate = blocked.has(key(target.value, parameter.value)) || prepared.has(key(target.value, parameter.value));
    add.disabled = duplicate;
    send.disabled = !prepared.size;
    message.textContent = duplicate ? 'Esta relación ya está vigente, en revisión o preparada.' : '';
  };
  target.addEventListener('change', update);
  parameter.addEventListener('change', update);
  add.addEventListener('click', () => {
    const identity = key(target.value, parameter.value);
    if (blocked.has(identity) || prepared.has(identity)) return;
    prepared.add(identity);
    const row = document.createElement('li');
    row.append(document.createTextNode(target.selectedOptions[0].textContent + ' · ' + parameter.value + ' '));
    for (const [name, value] of [['target_id', target.value], ['parameter', parameter.value]]) {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = name; input.value = value; row.append(input);
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.textContent = 'Quitar';
    remove.addEventListener('click', () => {prepared.delete(identity); row.remove(); update();});
    row.append(remove); list.append(row); update();
  });
  form.addEventListener('submit', event => {if (!prepared.size) event.preventDefault();});
  update();
})();
