(() => {
  async function request(url, options) {
    const response = await fetch(url, options);
    let data;
    try {data = await response.json();} catch {throw new Error(`Request failed (${response.status}). Check the server connection.`);}
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Invalid request. Check your settings.');
    return data;
  }
  function renderGrid(head, body, columns, rows) {
    head.replaceChildren(); body.replaceChildren();
    const heading = document.createElement('tr');
    for (const name of columns) {const cell = document.createElement('th'); cell.textContent = name; heading.appendChild(cell);}
    head.appendChild(heading);
    for (const values of rows) {
      const row = document.createElement('tr');
      for (const value of values) {
        const cell = document.createElement('td');
        cell.textContent = value === null ? 'NULL' : String(value).slice(0,500);
        if (value === null) cell.className = 'null-cell';
        if (value !== null && String(value).length > 500) cell.append('…');
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
  }
  function download(url, field, data) {
    const form = document.createElement('form'); form.method='POST'; form.action=url; form.target='_blank'; form.rel='noopener';
    const input = document.createElement('input'); input.type='hidden'; input.name=field; input.value=JSON.stringify(data);
    form.appendChild(input); document.body.appendChild(form); form.submit(); form.remove();
  }
  window.NetActUI = {request, renderGrid, download};
})();
