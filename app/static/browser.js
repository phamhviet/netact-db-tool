(() => {
  const el = id => document.getElementById(id);
  let connection = null, schema = null, table = null, offset = 0, hasNext = false, loading = false, tables = [];
  const fieldIds = ['db_type', 'database', 'host', 'port', 'username', 'password', 'ssl_mode', 'use_saved_credentials'];
  for (const selector of ['.grid', '.grid.options']) {
    const source = document.querySelector(`#import-form ${selector}`);
    const copy = source.cloneNode(true);
    copy.querySelector('.check')?.remove();
    copy.querySelectorAll('[id]').forEach(node => {node.id = `browse-${node.id}`;});
    el('browse-fields').appendChild(copy);
  }
  for (const button of document.querySelectorAll('[data-view]')) {
    button.addEventListener('click', () => {
      const browse = button.dataset.view === 'browser';
      el('workspace-label').textContent = browse ? 'DATA TOOLS / Database browser' : 'DATA TOOLS / XML Import';
      el('workspace-badge').textContent = browse ? 'DATABASE VIEWER' : 'NOKIA RAML20';
      document.querySelectorAll('[data-view]').forEach(node => node.setAttribute('aria-pressed', String(node === button)));
      el('browser-view').hidden = !browse; el('import-view').hidden = browse;
      document.querySelectorAll('[data-view]').forEach(node => node.classList.toggle('active', node === button));
    });
  }
  el('browse-db_type').addEventListener('change', () => {
    const postgres = el('browse-db_type').value === 'postgres';
    el('browse-port').value = postgres ? 5432 : 3306;
    el('browse-tls-require').disabled = !postgres;
    if (!postgres && el('browse-ssl_mode').value === 'require') el('browse-ssl_mode').value = 'disable';
  });
  async function api(path, body) {
    return window.NetActUI.request(`/api/browser/${path}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  }

  function message(text = '', error = false) {
    el('browse-message').textContent = text; el('browse-message').className = error ? 'error' : '';
  }
  function setLoading(value) {
    loading = value;
    for (const id of ['browse-refresh','browse-schema','browse-filter','browse-page-size']) el(id).disabled = value;
    el('browse-export').disabled = value || !table;
    el('browse-prev').disabled = value || !table || offset === 0;
    el('browse-next').disabled = value || !table || !hasNext;
    el('browse-tables').querySelectorAll('button').forEach(node => {node.disabled = value;});
  }
  function renderList() {
    const filter = el('browse-filter').value.toLocaleLowerCase();
    el('browse-tables').replaceChildren();
    const visible = tables.filter(name => name.toLocaleLowerCase().includes(filter));
    el('browse-table-count').textContent = `${visible.length} / ${tables.length} tables`;
    for (const name of visible) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = name;
      button.className = name === table ? 'selected' : ''; button.disabled = loading;
      button.addEventListener('click', async () => {table = name; offset = 0; renderList(); await loadRows();});
      el('browse-tables').appendChild(button);
    }
  }
  function clearTable() {
    table = null; offset = 0; hasNext = false;
    el('browse-head').replaceChildren(); el('browse-rows').replaceChildren();
    el('browse-table-name').textContent = 'Select a table'; el('browse-table-info').textContent = '';
    el('browse-page-info').textContent = ''; message();
  }
  async function loadTables() {
    setLoading(true);
    try {
      const result = await api('tables', {config:connection, schema_name:schema});
      schema = result.schema; tables = result.tables; clearTable();
      el('browse-schema').replaceChildren();
      for (const name of result.schemas) {const option = new Option(name, name); el('browse-schema').appendChild(option);}
      el('browse-schema').value = schema;
      el('browse-data').hidden = false; renderList();
      document.dispatchEvent(new CustomEvent('netact:database', {detail:{connected:true, schema, schemas:result.schemas}}));
      if (!tables.length) message('This schema has no visible tables.');
      return true;
    } catch (error) {clearTable(); tables = []; renderList(); message(error.message, true); throw error;}
    finally {setLoading(false);}
  }
  async function loadRows() {
    if (!connection || !table) return;
    setLoading(true); message('Loading data…');
    el('browse-head').replaceChildren(); el('browse-rows').replaceChildren();
    el('browse-table-name').textContent = `${schema}.${table}`;
    el('browse-table-info').textContent = ''; el('browse-page-info').textContent = '';
    try {
      const data = await api('rows', {config:connection, schema_name:schema, table, offset, page_size:Number(el('browse-page-size').value)});
      window.NetActUI.renderGrid(el('browse-head'), el('browse-rows'), data.columns, data.rows);
      hasNext = data.has_next;
      el('browse-table-info').textContent = `${data.columns.length} columns · ${data.ordered ? 'Ordered by primary key' : 'No primary key; row order is not guaranteed'}`;
      el('browse-page-info').textContent = data.rows.length ? `Rows ${offset + 1}–${offset + data.rows.length}` : 'No rows to display';
      message('Cells show up to 500 characters. CSV export includes every row and the full values.');
    } catch (error) {hasNext = false; message(error.message, true);}
    finally {setLoading(false);}
  }
  el('browse-connect-form').addEventListener('submit', async event => {
    event.preventDefault(); if (loading || connection) return;
    connection = Object.fromEntries(fieldIds.map(id => [id, el(`browse-${id}`).value]));
    connection.use_saved_credentials = el('browse-use_saved_credentials').checked;
    connection.port = Number(connection.port); connection.create_database = false; schema = null;
    el('browse-connect').disabled = true;
    el('browse-connection').textContent = 'Connecting…';
    try {
      await loadTables();
      el('browse-password').value = '';
      fieldIds.forEach(id => {el(`browse-${id}`).disabled = true;});
      el('browse-disconnect').hidden = false;
      el('browse-connection').textContent = `Connected to ${connection.host}:${connection.port} / ${connection.database}`;
      el('browse-connection').className = 'success';
    } catch (error) {
      connection = null; el('browse-data').hidden = true;
      el('browse-connection').textContent = error.message; el('browse-connection').className = 'error';
      el('browse-connect').disabled = false;
    }
  });
  el('browse-disconnect').addEventListener('click', () => {
    if (loading) return;
    document.dispatchEvent(new CustomEvent('netact:database', {detail:{connected:false}}));
    connection = null; schema = null; tables = []; clearTable();
    el('browse-data').hidden = true; el('browse-disconnect').hidden = true;
    el('browse-connection').textContent = 'Disconnected.'; el('browse-connection').className = '';
    fieldIds.forEach(id => {el(`browse-${id}`).disabled = false;});
    el('browse-password').value = ''; el('browse-tls-require').disabled = el('browse-db_type').value !== 'postgres';
    el('browse-connect').disabled = false;
    window.NetActPresets.refresh('browse-');
  });
  window.NetActBrowser = {connection: () => connection, setBusy(value) {setLoading(value); el('browse-disconnect').disabled=value;}};
  window.NetActPresets.bind('browse-');
  el('browse-filter').addEventListener('input', renderList);
  el('browse-schema').addEventListener('change', async () => {schema = el('browse-schema').value; try {await loadTables();} catch {}});
  el('browse-refresh').addEventListener('click', async () => {try {await loadTables();} catch {}});
  el('browse-page-size').addEventListener('change', async () => {offset = 0; await loadRows();});
  el('browse-prev').addEventListener('click', async () => {offset = Math.max(0, offset - Number(el('browse-page-size').value)); await loadRows();});
  el('browse-next').addEventListener('click', async () => {offset += Number(el('browse-page-size').value); await loadRows();});
  el('browse-export').addEventListener('click', () => {
    if (!connection || !table || loading) return;
    // Native download streams to disk; fetch().blob() would buffer a large export in browser RAM.
    window.NetActUI.download('/api/browser/export', 'query', {config:connection, schema_name:schema, table});
    message('Full-table download requested. An export error will appear in the new tab.');
  });
})();
