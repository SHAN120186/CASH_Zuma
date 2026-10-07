export function groupCodePayload(chatId, companyCode, companies) {
  const value = String(chatId).trim();
  const id = Number(value);
  if (!/^-[1-9]\d*$/.test(value) || !Number.isSafeInteger(id)) {
    throw new Error('Введите отрицательный ID Telegram-группы, который показывает команда /id.');
  }
  if (!companies.some(c => c.code === companyCode && c.code !== 'UNASSIGNED')) {
    throw new Error('Выберите компанию этой группы.');
  }
  return {chat_id: id, company_code: companyCode};
}

export function groupCompanyInfo(group) {
  const companies = Array.isArray(group?.companies) ? group.companies : [];
  if (companies.length !== 1) {
    return {label: companies.length ? 'Требуется одна компания' : 'Компания не выбрана', summaryEnabled: false};
  }
  const company = companies[0];
  if (!company?.code || company.code === 'UNASSIGNED') {
    return {label: 'Компания не выбрана', summaryEnabled: false};
  }
  return {
    label: (company.name || company.code) + (company.active ? '' : ' (отключена)'),
    summaryEnabled: company.active === true,
  };
}

// Ответ должен относиться именно к группе и компании, которые подтвердил администратор.
export function registrationCommand(result, payload) {
  if (!result || !/^[A-Z2-9]{20}$/.test(result.code || '') ||
      result.chat_id !== payload.chat_id || result.company_code !== payload.company_code ||
      !Number.isFinite(Date.parse(result.expires_at))) {
    throw new Error('Сайт вернул неполные данные подключения. Получите новый код.');
  }
  return `/register ${result.code}`;
}

// Только один POST: повтор после неопределённого сетевого исхода отменил бы первый код.
export async function issueGroupCode(api, payload) {
  const result = await api('/api/admin/telegram-group-codes', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  return {...result, command: registrationCommand(result, payload)};
}
