const $ = id => document.getElementById(id);
const form = $('import-form');
let busy = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function config() {
  return {db_type: $('db_type').value, host: $('host').value.trim(), port: Number($('port').value),
    database: $('database').value.trim(), username: $('username').value.trim(), password: $('password').value,
    create_database: $('create_database').checked, use_saved_credentials: $('use_saved_credentials').checked, ssl_mode: $('ssl_mode').value};
}
const request = window.NetActUI.request;
function setBusy(value) {
  busy = value;
  $('submit').setAttribute('aria-busy', String(value));
  $('submit').firstChild.textContent = value ? 'Processing…' : 'Convert & import';
  for (const control of form.querySelectorAll('input,select,button')) control.disabled = value;
  if (!value) {
    $('tls-require').disabled = $('db_type').value !== 'postgres';
    window.NetActPresets.refresh();
  }
}
function updateSummary() {
  const file = $('xml-file').files[0];
  $('summary-file').textContent = file ? file.name : 'No file selected';
  const engine = $('db_type').selectedOptions[0].textContent;
  $('summary-db').textContent = `${engine} · ${$('database').value.trim() || 'No name entered'}`;
  $('summary-mode').textContent = $('create_database').checked ? 'Create new; replace if it exists' : 'Use existing database — no DROP/CREATE';
  $('summary-host').textContent = $('host').value.trim() ? `${$('host').value.trim()}:${$('port').value}` : 'Not configured';
}
for (const id of ['db_type', 'database', 'host', 'port', 'create_database']) $(id).addEventListener('input', updateSummary);
function showFile() {
  const file = $('xml-file').files[0];
  updateSummary();
  $('filename').textContent = file ? file.name : 'Choose XML or drop it here';
  $('filedetail').textContent = file ? `${(file.size / 1024 ** 2).toFixed(2)} MB · ready to convert` : 'Select a Nokia RAML20 export';
}
$('xml-file').addEventListener('change', showFile);
const zone = $('dropzone');
zone.addEventListener('dragover', event => {event.preventDefault(); if (!busy) zone.classList.add('drag');});
zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
zone.addEventListener('drop', event => {
  event.preventDefault(); zone.classList.remove('drag');
  if (busy || !event.dataTransfer.files.length) return;
  const transfer = new DataTransfer(); transfer.items.add(event.dataTransfer.files[0]);
  $('xml-file').files = transfer.files; showFile();
});
$('db_type').addEventListener('change', () => {
  $('port').value = $('db_type').value === 'postgres' ? 5432 : 3306;
  $('tls-require').disabled = $('db_type').value !== 'postgres';
  if ($('tls-require').disabled && $('ssl_mode').value === 'require') $('ssl_mode').value = 'disable';
  $('connection-result').textContent = '';
  updateSummary();
});
form.addEventListener('input', () => {$('connection-result').textContent = '';});
$('test-connection').addEventListener('click', async () => {
  for (const id of ['database','host','port','username']) if (!$(id).reportValidity()) return;
  $('test-connection').disabled = true;
  $('connection-result').className = '';
  $('connection-result').textContent = 'Testing connection…';
  try {
    const data = await request('/api/connection', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config())});
    $('connection-result').textContent = data.message;
    $('connection-result').className = 'success';
  } catch (error) {
    $('connection-result').textContent = error.message;
    $('connection-result').className = 'error';
  } finally {if (!busy) $('test-connection').disabled = false;}
});
$('diagnose-db').addEventListener('click', async () => {
  for (const id of ['database','host','port','username']) if (!$(id).reportValidity()) return;
  $('diagnose-db').disabled = true;
  $('db-diagnostics').hidden = false;
  $('db-diagnostics').textContent = 'Reading server and account details. No DROP/CREATE is executed.';
  try {
    const data = await request('/api/diagnostics', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config())});
    $('db-diagnostics').textContent = JSON.stringify(data, null, 2);
  } catch (error) {$('db-diagnostics').textContent = error.message;}
  finally {if (!busy) $('diagnose-db').disabled = false;}
});
function render(job) {
  $('status-title').textContent = {queued:'File received',running:'Processing…',completed:'Import completed',completed_with_errors:'Completed with errors',failed:'Import failed'}[job.status];
  $('status-message').textContent = job.message;
  $('job-mode').hidden = typeof job.create_database !== 'boolean';
  $('job-mode').textContent = job.create_database ? 'Submitted mode: create new / replace an existing database' : 'Submitted mode: use existing database, no DROP/CREATE';
  $('status-message').className = ['failed','completed_with_errors'].includes(job.status) ? 'error' : '';
  $('progress').value = job.progress; $('percent').textContent = `${job.progress}%`;
  if (['completed','completed_with_errors'].includes(job.status)) {
    $('results').hidden = false;
    $('totals').textContent = `${job.tables.length} CSV files · ${job.total_rows.toLocaleString('en-US')} rows saved · ${job.failed_rows || 0} failed rows · ${job.failed_tables || 0} failed tables`;
    $('result-rows').replaceChildren();
    $('import-errors').replaceChildren();
    for (const table of job.tables) {
      const row = document.createElement('tr');
      for (const value of [table.table, table.rows.toLocaleString('en-US'), table.columns.length, table.rows_failed || 0, {imported:'Imported',partial:'Some rows failed',failed_create:'Table creation failed',skipped_empty:'Empty file, skipped'}[table.status] || 'Imported']) {
        const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell);
      }
      $('result-rows').appendChild(row);
      for (const error of table.errors || []) {
        const detail = document.createElement('p');
        detail.textContent = `${table.file || table.table}${error.line ? ` · CSV line ${error.line}` : ''}: ${error.message}`;
        $('import-errors').appendChild(detail);
      }
    }
  }
}
async function poll(id) {
  let failures = 0;
  for (;;) {
    let job;
    try {job = await request(`/api/jobs/${encodeURIComponent(id)}`); failures = 0;}
    catch (error) {
      failures++;
      $('status-message').textContent = 'Lost the status connection. Retrying; the import may still be running on the server.';
      if (failures >= 10) throw new Error('Cannot retrieve status. The import may still be running; reload to reconnect.');
      await sleep(3000); continue;
    }
    render(job);
    if (['completed','completed_with_errors','failed'].includes(job.status)) {
      sessionStorage.removeItem('netact-job'); return;
    }
    await sleep(1200);
  }
}
form.addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  const file = $('xml-file').files[0];
  if (!file || !file.name.toLowerCase().endsWith('.xml') || !file.size) {
    $('status-message').textContent = 'Choose a non-empty .xml file.';
    $('status-message').className = 'error'; return;
  }
  const body = new FormData(); body.append('config',JSON.stringify(config())); body.append('file',file);
  setBusy(true); $('results').hidden = true;
  $('status-title').textContent = 'Uploading XML…'; $('status-message').textContent = 'Wait for the upload to finish.';
  $('status-message').className = ''; $('progress').value = 0; $('percent').textContent = '0%';
  try {
    const job = await request('/api/jobs',{method:'POST',body});
    $('password').value = ''; sessionStorage.setItem('netact-job',job.id);
    await poll(job.id);
  } catch (error) {$('status-message').textContent = error.message; $('status-message').className = 'error';}
  finally {setBusy(false);}
});
window.NetActPresets.bind();
updateSummary();
(async () => {
  try {
    const health = await request('/api/health');
    $('upload-limit').textContent = '.XML · RAML20 · no upload size limit';
    if (!health.java) {$('status-message').textContent = 'Java is not installed on the server. Install it before converting.'; $('status-message').className = 'error';}
    const job = sessionStorage.getItem('netact-job');
    if (job) {setBusy(true); try {await poll(job);} finally {setBusy(false);}}
  } catch (error) {$('status-message').textContent = error.message;}
})();
