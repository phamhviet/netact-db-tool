(() => {
  let profiles = {}, loadError = '';
  const ready = fetch('/api/db-presets').then(async response => {
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Cannot read .env settings');
    profiles = data;
  }).catch(error => {loadError = error.message;});
  const field = (prefix, name) => document.getElementById(prefix + name);
  function matches(prefix, profile) {
    return profile && field(prefix, 'host').value.trim().toLowerCase() === profile.host.trim().toLowerCase()
      && Number(field(prefix, 'port').value) === profile.port
      && field(prefix, 'username').value.trim() === profile.username.trim()
      && field(prefix, 'ssl_mode').value === profile.ssl_mode;
  }
  function refresh(prefix = '') {
    const checkbox = field(prefix, 'use_saved_credentials');
    const profile = profiles[field(prefix, 'db_type').value];
    const configured = !!profile?.password_configured;
    const bound = matches(prefix, profile);
    if (!configured || !bound) checkbox.checked = false;
    checkbox.disabled = !configured || !bound || field(prefix, 'host').disabled;
    field(prefix, 'saved-password-hint').textContent = loadError || (!configured ? 'No password configured in .env'
      : !bound ? 'Connection changed — enter a password' : 'The server uses the password without sending it to the browser');
    field(prefix, 'password').placeholder = checkbox.checked ? 'Using the server .env password' : 'Database password';
  }
  async function apply(prefix = '', initial = false) {
    const kind = field(prefix, 'db_type').value;
    await ready;
    if (field(prefix, 'db_type').value !== kind) return;
    const profile = profiles[kind];
    if (!profile) {refresh(prefix); return;}
    for (const name of ['host', 'port', 'username', 'ssl_mode']) {
      if (!initial || !field(prefix, name).value || ['port', 'ssl_mode'].includes(name)) {
        field(prefix, name).value = profile[name];
        field(prefix, name).dispatchEvent(new Event('input', {bubbles:true}));
      }
    }
    if (profile.database && (!initial || !field(prefix, 'database').value)) {
      field(prefix, 'database').value = profile.database;
      field(prefix, 'database').dispatchEvent(new Event('input', {bubbles:true}));
    }
    if (!initial) field(prefix, 'password').value = '';
    field(prefix, 'use_saved_credentials').checked = !!profile.password_configured && matches(prefix, profile) && !field(prefix, 'password').value;
    refresh(prefix);
  }
  function bind(prefix = '') {
    field(prefix, 'db_type').addEventListener('change', () => apply(prefix));
    for (const name of ['host', 'port', 'username', 'ssl_mode']) field(prefix, name).addEventListener('input', () => refresh(prefix));
    field(prefix, 'password').addEventListener('input', () => {
      if (field(prefix, 'password').value) field(prefix, 'use_saved_credentials').checked = false;
      refresh(prefix);
    });
    field(prefix, 'use_saved_credentials').addEventListener('change', () => {
      if (field(prefix, 'use_saved_credentials').checked) field(prefix, 'password').value = '';
      refresh(prefix);
    });
    apply(prefix, true);
  }
  window.NetActPresets = {bind, refresh};
})();
