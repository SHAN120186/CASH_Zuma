// Keep decimal strings exact: formatting must not round financial input.
export const ungroupAmount = value => String(value ?? '').replace(/[\s\u00a0\u202f]/g, '').replace(',', '.');
export function groupAmount(value) {
  const raw=ungroupAmount(value);
  if(!/^\d*(?:\.\d*)?$/.test(raw))return raw;
  const [whole,fraction]=raw.split('.');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+(fraction===undefined?'':'.'+fraction);
}
export function amountInput(event,previous='') {
  const input=event.target;let value=input.value,caret=input.selectionStart??value.length;
  // Backspace/Delete over a grouping space must remove a digit, not reinsert
  // the same separator forever. Normal range deletion is left untouched.
  if(value.length===previous.length-1&&ungroupAmount(value)===ungroupAmount(previous)){
    if(event.inputType==='deleteContentBackward'&&caret>0){value=value.slice(0,caret-1)+value.slice(caret);caret--;}
    else if(event.inputType==='deleteContentForward'){value=value.slice(0,caret)+value.slice(caret+1);}
  }
  const logical=ungroupAmount(value.slice(0,caret)).length,raw=ungroupAmount(value),display=groupAmount(raw);
  let position=0,count=0;
  while(position<display.length&&count<logical){if(display[position]!==' ')count++;position++;}
  input.value=display;input.setSelectionRange?.(position,position);
  return raw;
}
