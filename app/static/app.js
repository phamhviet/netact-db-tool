const $ = id => document.getElementById(id);
const form = $('import-form');
let busy = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function config() {
  return {db_type: $('db_type').value, host: $('host').value.trim(), port: Number($('port').value),
    database: $('database').value.trim(), username: $('username').value.trim(), password: $('password').value,
    create_database: $('create_database').checked, ssl_mode: $('ssl_mode').value};
}
async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Cấu hình không hợp lệ. Vui lòng kiểm tra lại.');
  return data;
}
function setBusy(value) {
  busy = value;
  for (const control of form.querySelectorAll('input,select,button')) control.disabled = value;
  if (!value) $('tls-require').disabled = $('db_type').value !== 'postgres';
}
function showFile() {
  const file = $('xml-file').files[0];
  $('filename').textContent = file ? file.name : 'Kéo thả file XML vào đây';
  $('filedetail').textContent = file ? `${(file.size / 1024 ** 2).toFixed(2)} MB · sẵn sàng chuyển đổi` : 'hoặc nhấn để chọn file từ máy tính';
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
});
form.addEventListener('input', () => {$('connection-result').textContent = '';});
$('test-connection').addEventListener('click', async () => {
  for (const id of ['database','host','port','username']) if (!$(id).reportValidity()) return;
  $('test-connection').disabled = true;
  $('connection-result').className = '';
  $('connection-result').textContent = 'Đang kiểm tra…';
  try {
    const data = await request('/api/connection', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config())});
    $('connection-result').textContent = data.message;
    $('connection-result').className = 'success';
  } catch (error) {
    $('connection-result').textContent = error.message;
    $('connection-result').className = 'error';
  } finally {if (!busy) $('test-connection').disabled = false;}
});
function render(job) {
  $('status-title').textContent = {queued:'Đã nhận dữ liệu',running:'Đang xử lý…',completed:'Nhập dữ liệu thành công',failed:'Tác vụ thất bại'}[job.status];
  $('status-message').textContent = job.message;
  $('status-message').className = job.status === 'failed' ? 'error' : '';
  $('progress').value = job.progress; $('percent').textContent = `${job.progress}%`;
  if (job.status === 'completed') {
    $('results').hidden = false;
    $('totals').textContent = `${job.tables.length} bảng · ${job.total_rows.toLocaleString('vi-VN')} dòng đã được lưu`;
    $('result-rows').replaceChildren();
    for (const table of job.tables) {
      const row = document.createElement('tr');
      for (const value of [table.table, table.rows.toLocaleString('vi-VN'), table.columns.length]) {
        const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell);
      }
      $('result-rows').appendChild(row);
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
      $('status-message').textContent = 'Mất kết nối theo dõi. Đang thử lại; tác vụ có thể vẫn chạy trên máy chủ.';
      if (failures >= 10) throw new Error('Không lấy được trạng thái. Tác vụ có thể vẫn chạy; tải lại trang để tiếp tục theo dõi.');
      await sleep(3000); continue;
    }
    render(job);
    if (['completed','failed'].includes(job.status)) {
      sessionStorage.removeItem('netact-job'); return;
    }
    await sleep(1200);
  }
}
form.addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  const file = $('xml-file').files[0];
  if (!file || !file.name.toLowerCase().endsWith('.xml') || !file.size) {
    $('status-message').textContent = 'Vui lòng chọn file .xml không rỗng.';
    $('status-message').className = 'error'; return;
  }
  const body = new FormData(); body.append('config',JSON.stringify(config())); body.append('file',file);
  setBusy(true); $('results').hidden = true;
  $('status-title').textContent = 'Đang tải XML lên…'; $('status-message').textContent = 'Vui lòng chờ tải file hoàn tất.';
  $('status-message').className = ''; $('progress').value = 0; $('percent').textContent = '0%';
  try {
    const job = await request('/api/jobs',{method:'POST',body});
    $('password').value = ''; sessionStorage.setItem('netact-job',job.id);
    await poll(job.id);
  } catch (error) {$('status-message').textContent = error.message; $('status-message').className = 'error';}
  finally {setBusy(false);}
});
(async () => {
  try {
    const health = await request('/api/health');
    $('upload-limit').textContent = '.XML · RAML20 · không giới hạn dung lượng';
    if (!health.java) {$('status-message').textContent = 'Máy chủ chưa có Java. Cần cài Java trước khi chuyển đổi.'; $('status-message').className = 'error';}
    const job = sessionStorage.getItem('netact-job');
    if (job) {setBusy(true); try {await poll(job);} finally {setBusy(false);}}
  } catch (error) {$('status-message').textContent = error.message;}
})();
