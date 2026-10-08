export function isAppFileLink(url, origin) {
  let parsed;
  try {parsed=new URL(url,origin);} catch {return false;}
  return parsed.origin===origin && /^\/api\//.test(parsed.pathname) &&
    /(?:\/files\/(?:pdf|xlsx)$|\/sources\/\d+\/download$|\/(?:native|template)\.xlsx$|\/document(?:\/|$)|\/(?:documents|request-documents)\/\d+$|\/(?:export|exports)(?:\/|\.|$)|\.(?:csv|xlsx|pdf)$)/.test(parsed.pathname);
}

export function downloadFilename(disposition='', fallback='отчёт') {
  const encoded=/filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  const plain=/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i.exec(disposition);
  let filename=plain?.[1]||plain?.[2]||fallback;
  if(encoded) {try {filename=decodeURIComponent(encoded[1]);} catch {}}
  return String(filename).trim().replace(/[\\/:*?"<>|\u0000-\u001f]/g,'_').slice(0,220)||fallback;
}

export async function fetchDownload(url,{fetcher=fetch,signal,headers={},onResponse=()=>{},onBody=()=>{}}={}) {
  const response=await fetcher(url,{credentials:'same-origin',signal,headers});
  if(!response.ok) {
    let detail;
    try {detail=(await response.json()).detail;} catch {}
    throw Object.assign(Error(typeof detail==='string'?detail:'Не удалось скачать файл. Повторите загрузку.'),{status:response.status});
  }
  const type=response.headers.get('content-type')||'', disposition=response.headers.get('content-disposition')||'';
  const attachment=/^attachment\s*;/i.test(disposition) && /filename(?:\*)?\s*=/i.test(disposition);
  if(response.redirected || /text\/html/i.test(type) || /application\/json/i.test(type)&&!attachment) {
    throw Error('Сервер вернул страницу вместо файла. Войдите в систему и повторите скачивание.');
  }
  onResponse();
  const blob=await response.blob();
  if(!blob.size) throw Error('Получен пустой файл. Повторите скачивание.');
  onBody();
  const fallback=new URL(url,'http://local.test').pathname.split('/').at(-1)||'отчёт';
  return {blob,filename:downloadFilename(disposition,fallback)};
}
