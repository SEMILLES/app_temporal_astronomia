// Reuse the existing declared collaborator; this is not authentication.
document.addEventListener('DOMContentLoaded', function () {
  const selector = document.getElementById('lesico-collaborator');
  const link = document.getElementById('my-work-link');
  const content = document.getElementById('my-work-content');
  if (!selector || !link) return;

  function synchronize() {
    const target = new URL(link.href, window.location.href);
    if (selector.value) target.searchParams.set('collaborator_id', selector.value);
    else target.searchParams.delete('collaborator_id');
    link.href = target.href;
    if (!content) return;
    const current = new URL(window.location.href);
    if ((current.searchParams.get('collaborator_id') || '') !== selector.value) {
      content.hidden = true;
      if (selector.value) current.searchParams.set('collaborator_id', selector.value);
      else current.searchParams.delete('collaborator_id');
      current.searchParams.delete('page');
      window.location.replace(current.href);
    } else {
      content.hidden = false;
    }
  }
  selector.addEventListener('change', synchronize);
  window.addEventListener('pageshow', synchronize);
  window.addEventListener('storage', function (event) {
    if (event.key !== 'lesico-collaborator-id' && event.key !== null) return;
    const saved = localStorage.getItem('lesico-collaborator-id') || '';
    selector.value = [...selector.options].some(option => option.value === saved) ? saved : '';
    synchronize();
  });
  synchronize();
});
