(() => {
  const el = id => document.getElementById(id);
  let connection = null, schema = null, table = null, offset = 0, hasNext = false, loading = false, tables = [];
  const fieldIds = ['db_type', 'database', 'host', 'port', 'username', 'password', 'ssl_mode'];
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
      el('workspace-label').textContent = browse ? 'DATA TOOLS / Duyệt database' : 'DATA TOOLS / XML Import';
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
    const response = await fetch(`/api/browser/${path}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Yêu cầu không hợp lệ.');
    return data;
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
    el('browse-table-count').textContent = `${visible.length} / ${tables.length} bảng`;
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
    el('browse-table-name').textContent = 'Chọn bảng để xem'; el('browse-table-info').textContent = '';
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
      if (!tables.length) message('Schema này chưa có bảng hoặc tài khoản không thấy bảng nào.');
      return true;
    } catch (error) {clearTable(); tables = []; renderList(); message(error.message, true); throw error;}
    finally {setLoading(false);}
  }
  async function loadRows() {
    if (!connection || !table) return;
    setLoading(true); message('Đang tải dữ liệu…');
    el('browse-head').replaceChildren(); el('browse-rows').replaceChildren();
    el('browse-table-name').textContent = `${schema}.${table}`;
    el('browse-table-info').textContent = ''; el('browse-page-info').textContent = '';
    try {
      const data = await api('rows', {config:connection, schema_name:schema, table, offset, page_size:Number(el('browse-page-size').value)});
      const heading = document.createElement('tr');
      for (const name of data.columns) {const cell = document.createElement('th'); cell.textContent = name; heading.appendChild(cell);}
      el('browse-head').appendChild(heading);
      for (const values of data.rows) {
        const row = document.createElement('tr');
        for (const value of values) {
          const cell = document.createElement('td');
          cell.textContent = value === null ? 'NULL' : value.slice(0, 500);
          if (value === null) cell.className = 'null-cell';
          if (value !== null && value.length > 500) cell.append('…');
          row.appendChild(cell);
        }
        el('browse-rows').appendChild(row);
      }
      hasNext = data.has_next;
      el('browse-table-info').textContent = `${data.columns.length} cột · ${data.ordered ? 'Sắp xếp theo khóa chính' : 'Bảng không có khóa chính; thứ tự dòng không cố định'}`;
      el('browse-page-info').textContent = data.rows.length ? `Dòng ${offset + 1}–${offset + data.rows.length}` : 'Không có dòng dữ liệu';
      message('Ô dài hiển thị tối đa 500 ký tự. Export CSV lấy đầy đủ mọi dòng và giá trị.');
    } catch (error) {hasNext = false; message(error.message, true);}
    finally {setLoading(false);}
  }
  el('browse-connect-form').addEventListener('submit', async event => {
    event.preventDefault(); if (loading || connection) return;
    connection = Object.fromEntries(fieldIds.map(id => [id, el(`browse-${id}`).value]));
    connection.port = Number(connection.port); connection.create_database = false; schema = null;
    el('browse-connect').disabled = true;
    el('browse-connection').textContent = 'Đang kết nối…';
    try {
      await loadTables();
      el('browse-password').value = '';
      fieldIds.forEach(id => {el(`browse-${id}`).disabled = true;});
      el('browse-disconnect').hidden = false;
      el('browse-connection').textContent = `Đã kết nối ${connection.host}:${connection.port} / ${connection.database}`;
      el('browse-connection').className = 'success';
    } catch (error) {
      connection = null; el('browse-data').hidden = true;
      el('browse-connection').textContent = error.message; el('browse-connection').className = 'error';
      el('browse-connect').disabled = false;
    }
  });
  el('browse-disconnect').addEventListener('click', () => {
    if (loading) return;
    connection = null; schema = null; tables = []; clearTable();
    el('browse-data').hidden = true; el('browse-disconnect').hidden = true;
    el('browse-connection').textContent = 'Đã ngắt kết nối.'; el('browse-connection').className = '';
    fieldIds.forEach(id => {el(`browse-${id}`).disabled = false;});
    el('browse-password').value = ''; el('browse-tls-require').disabled = el('browse-db_type').value !== 'postgres';
    el('browse-connect').disabled = false;
  });
  el('browse-filter').addEventListener('input', renderList);
  el('browse-schema').addEventListener('change', async () => {schema = el('browse-schema').value; try {await loadTables();} catch {}});
  el('browse-refresh').addEventListener('click', async () => {try {await loadTables();} catch {}});
  el('browse-page-size').addEventListener('change', async () => {offset = 0; await loadRows();});
  el('browse-prev').addEventListener('click', async () => {offset = Math.max(0, offset - Number(el('browse-page-size').value)); await loadRows();});
  el('browse-next').addEventListener('click', async () => {offset += Number(el('browse-page-size').value); await loadRows();});
  el('browse-export').addEventListener('click', () => {
    if (!connection || !table || loading) return;
    // Native download streams to disk; fetch().blob() would buffer a large export in browser RAM.
    const form = document.createElement('form'); form.method = 'POST'; form.action = '/api/browser/export'; form.target = '_blank'; form.rel = 'noopener';
    const input = document.createElement('input'); input.type = 'hidden'; input.name = 'query'; input.value = JSON.stringify({config:connection, schema_name:schema, table});
    form.appendChild(input); document.body.appendChild(form); form.submit(); form.remove();
    message('Đã yêu cầu tải toàn bộ bảng. Nếu export lỗi, thông báo sẽ xuất hiện ở tab mới.');
  });
})();
