(() => {
  const el = id => document.getElementById(id);
  let run = null, busy = false;
  function switchTab(sql) {
    el('table-workspace').hidden = sql; el('sql-workspace').hidden = !sql;
    for (const [id, selected] of [['table-tab',!sql],['sql-tab',sql]]) {
      el(id).setAttribute('aria-selected',String(selected)); el(id).classList.toggle('selected',selected);
    }
  }
  el('table-tab').addEventListener('click',()=>switchTab(false));
  el('sql-tab').addEventListener('click',()=>switchTab(true));
  function message(text, error=false) {el('sql-message').textContent=text; el('sql-message').className=error?'error':'';}
  function clearResults() {
    if(run) fetch(`/api/sql/results/${encodeURIComponent(run.id)}`,{method:'DELETE'}).catch(()=>{});
    run=null;el('sql-results').hidden=true;el('sql-export').disabled=true;
    el('sql-head').replaceChildren();el('sql-rows').replaceChildren();
  }
  document.addEventListener('netact:database',event=>{
    clearResults();
    el('sql-schema').replaceChildren();
    if(event.detail.connected){
      for(const name of event.detail.schemas)el('sql-schema').appendChild(new Option(name,name));
      el('sql-schema').value=event.detail.schema;
    }
  });
  function renderResult() {
    const result=run?.results[Number(el('sql-result-select').value)];
    if(!result)return;
    el('sql-export').disabled=result.type!=='rows';
    window.NetActUI.renderGrid(el('sql-head'),el('sql-rows'),result.columns,result.rows);
    el('sql-result-info').textContent=result.type==='rows'
      ? `${result.total_rows.toLocaleString('en-US')} rows captured · ${result.rows.length} shown · ${result.columns.length} columns`
      : `Statement completed · ${result.affected_rows===null?'Affected-row count unavailable':`${result.affected_rows.toLocaleString('en-US')} rows affected`}`;
  }
  async function execute() {
    if(busy)return;
    const connection=window.NetActBrowser.connection();
    if(!connection){message('Connect to a database first.',true);return;}
    if(!el('sql-editor').value.trim()){message('Enter SQL or open a UTF-8 .sql file.',true);return;}
    clearResults(); busy=true; window.NetActBrowser.setBusy(true);
    for(const id of ['sql-run','sql-clear','sql-file','sql-schema','sql-preview-limit'])el(id).disabled=true;
    el('sql-run').textContent='Executing…';message('Executing script and capturing results. Keep this page open.');
    try {
      run=await window.NetActUI.request('/api/sql/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:connection,schema_name:el('sql-schema').value,sql:el('sql-editor').value,preview_limit:Number(el('sql-preview-limit').value)})});
      el('sql-results').hidden=false;el('sql-result-select').replaceChildren();
      for(const result of run.results)el('sql-result-select').appendChild(new Option(`Statement ${result.statement} · ${result.type==='rows'?`${result.total_rows} rows`:'command'} · Result ${result.index+1}`,result.index));
      el('sql-summary').textContent=`${run.statement_count} statements · ${run.results.length} results · ${run.elapsed_seconds}s · committed`;
      renderResult();message('Script completed. CSV exports use these captured results; no SQL is rerun.');
    } catch(error){message(error instanceof TypeError ? 'Connection lost. The script may still be running or committed. Check the database before rerunning.' : error.message,true);}
    finally {
      busy=false;window.NetActBrowser.setBusy(false);
      for(const id of ['sql-run','sql-clear','sql-file','sql-schema','sql-preview-limit'])el(id).disabled=false;
      el('sql-run').textContent='Run script →';
    }
  }
  el('sql-run').addEventListener('click',execute);
  el('sql-editor').addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();execute();}});
  el('sql-clear').addEventListener('click',()=>{if(!busy){clearResults();message('Results cleared.');}});
  el('sql-result-select').addEventListener('change',renderResult);
  el('sql-export').addEventListener('click',()=>{
    if(!run)return;
    const link=document.createElement('a');link.href=`/api/sql/results/${encodeURIComponent(run.id)}/${Number(el('sql-result-select').value)}/csv`;
    link.download='sql-result.csv';document.body.appendChild(link);link.click();link.remove();
  });
  el('sql-file').addEventListener('change',async()=>{
    const file=el('sql-file').files[0];if(!file)return;
    try {el('sql-editor').value=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer()).replace(/^\uFEFF/,'');message(`Loaded ${file.name}. Review the script before executing.`);}
    catch {message('Cannot read this file as UTF-8. Save it as UTF-8 SQL and try again.',true);}
    finally {el('sql-file').value='';}
  });
})();
