import {N_} from './i18n/index.js';
const count = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Math.floor(Number(value)) : 0;
export function progressPercent(completed, total) {
  const size = count(total);
  if (!size) return null;
  const done=Math.min(size,count(completed));
  return done===size ? 100 : Math.min(99,Math.round(done*100/size));
}

// Count completed stages, never elapsed time or guessed upload bytes. Each
// ticket reserves its final stage for complete(), after the response is checked.
export function createOperationProgress(onChange = () => {}) {
  let epoch = 0, nextId = 0, records = new Map();
  let state = {active:false,label:'',completed:0,total:0,error:''};
  const publish = () => {
    const values = [...records.values()], pending = values.filter(item => !item.ended);
    const last = pending.at(-1) || values.at(-1);
    state = {
      active:pending.length > 0,
      label:last?.label || '',
      completed:values.reduce((sum,item) => sum + item.completed,0),
      total:values.some(item => !item.total) ? 0 : values.reduce((sum,item) => sum + item.total,0),
      error:values.filter(item=>item.error).map(item=>item.error).join(' · '),
    };
    onChange({...state});
  };
  const get = ticket => ticket?.epoch === epoch ? records.get(ticket.id) : null;
  const pending = ticket => {const item=get(ticket);return item&&!item.ended ? item : null;};
  return {
    get state() {return {...state};},
    begin(label,total=1) {
      if (![...records.values()].some(item=>!item.ended)) {epoch++;records=new Map();}
      const ticket={id:++nextId,epoch};
      records.set(ticket.id,{label:String(label||''),total:count(total),completed:0,ended:false,error:''});
      publish();return ticket;
    },
    stage(ticket,label) {const item=pending(ticket);if(!item)return false;item.label=String(label||'');publish();return true;},
    advance(ticket,amount=1,label) {
      const item=pending(ticket);if(!item)return false;
      item.completed=item.total ? Math.min(item.total-1,item.completed+count(amount)) : item.completed+count(amount);
      if(label!=null)item.label=String(label);publish();return true;
    },
    progress(ticket,value={}) {
      const item=pending(ticket);if(!item)return false;
      if(value.total!=null)item.total=count(value.total);
      if(value.completed!=null)item.completed=item.total ? Math.min(item.total-1,count(value.completed)) : count(value.completed);
      if(value.label!=null)item.label=String(value.label);publish();return true;
    },
    complete(ticket,label) {
      const item=pending(ticket);if(!item)return false;
      if(!item.total)item.total=Math.max(1,item.completed);
      item.completed=item.total;item.ended=true;
      if(label!=null)item.label=String(label);publish();return true;
    },
    fail(ticket,error) {
      const item=pending(ticket);if(!item)return false;
      if(!item.total)item.total=item.completed+1;
      item.completed=Math.min(item.completed,item.total-1);item.ended=true;
      item.error=String(error?.message||error||N_('Действие не завершено.'));publish();return true;
    },
    reset() {epoch++;records=new Map();state={active:false,label:'',completed:0,total:0,error:''};onChange({...state});},
  };
}
