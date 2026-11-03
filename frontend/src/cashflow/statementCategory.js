import {N_,tx} from '../i18n/index.js';

// Only reserved automatic charge names are interface text. Catalog names
// entered by users remain verbatim, including names that merely resemble these.
const technicalNames=new Set([N_('Банковские комиссии (выписки)'),N_('Проценты по кредитам (выписки)')]);
export function statementCategoryName(value){
 const name=String(value??''),suffix=name.match(/ \([1-9]\d*\)$/)?.[0]||'';
 const base=suffix?name.slice(0,-suffix.length):name;
 return technicalNames.has(base)?tx(base)+suffix:name;
}
