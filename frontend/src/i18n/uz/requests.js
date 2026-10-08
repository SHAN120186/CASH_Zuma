// Uzbek (Latin) translations: requests/*.
// Key: the exact Russian text; value: the Uzbek text with the same {placeholders}.
export default {
  // Request statuses, stages and route (workflow.js)
  'Утверждена': 'Tasdiqlangan',
  'Оплачена': 'Toʻlangan',
  'На доработке': 'Qayta ishlashda',
  'Закрыта без оплаты': 'Toʻlovsiz yopilgan',
  'Отклонена (архив)': 'Rad etilgan (arxiv)',
  'Финансовый директор': 'Moliya direktori',
  'Директор': 'Direktor',
  'Ожидает: проверка бухгалтера': 'Kutilmoqda: buxgalter tekshiruvi',
  'Ожидает: финансовый директор': 'Kutilmoqda: moliya direktori',
  'Ожидает: директор': 'Kutilmoqda: direktor',
  'Ожидает: согласования': 'Kelishish kutilmoqda',
  'Заявитель': 'Arizachi',
  'Проверка': 'Tekshiruv',
  'Проверка · не требовалась': 'Tekshiruv · talab qilinmagan',
  'Фин. директор': 'Moliya direktori',
  'Директор · не участвует': 'Direktor · ishtirok etmaydi',
  'Оплата': 'Toʻlov',

  // Priorities, channels and document sections
  'Обычный': 'Oddiy',
  'Высокий': 'Yuqori',
  'Срочный': 'Shoshilinch',
  'Банк (расчётный счёт)': 'Bank (hisob-kitob hisobvaragʻi)',
  'Внутренняя заявка / Индент': 'Ichki ariza / Indent',
  'Договор / Счёт на оплату': 'Shartnoma / Hisob-faktura',
  'Прочие подтверждающие документы': 'Boshqa tasdiqlovchi hujjatlar',

  // File checks and sizes
  '«{name}»: допустимы PDF, PNG, JPEG, DOCX или XLSX.': '«{name}»: faqat PDF, PNG, JPEG, DOCX yoki XLSX qabul qilinadi.',
  '«{name}»: пустой файл.': '«{name}»: fayl boʻsh.',
  '«{name}»: файл больше 5 МБ.': '«{name}»: fayl 5 MB dan katta.',
  '{n} МБ': '{n} MB',
  '{n} КБ': '{n} KB',

  // Decisions
  'Проверено: реквизиты и комплектность': 'Tekshirildi: rekvizitlar va hujjatlar toʻliqligi',
  'Согласовать': 'Kelishish',
  'Вернуть на доработку': 'Qayta ishlashga qaytarish',
  'Вернуть финансовому директору': 'Moliya direktoriga qaytarish',
  'Закрыть без оплаты': 'Toʻlovsiz yopish',
  'Перенести дату': 'Sanani koʻchirish',
  'Утвердить (директор не участвует)': 'Tasdiqlash (direktor ishtirok etmaydi)',
  'Подтвердить проверку бюджета': 'Byudjet tekshiruvini tasdiqlash',
  'Утвердить оплату': 'Toʻlovni tasdiqlash',

  // Request history
  'Отправлена на согласование': 'Kelishishga yuborildi',
  'Проверено бухгалтером: реквизиты и комплектность': 'Buxgalter tekshirdi: rekvizitlar va hujjatlar toʻliqligi',
  'Согласована': 'Kelishildi',
  'Возвращена на доработку': 'Qayta ishlashga qaytarildi',
  'Возвращена финансовому директору': 'Moliya direktoriga qaytarildi',
  'Перенесена плановая дата': 'Rejadagi sana koʻchirildi',
  'Отклонена (прежний порядок)': 'Rad etildi (avvalgi tartib)',
  'Отменена (прежний порядок)': 'Bekor qilindi (avvalgi tartib)',
  'версия {n}': '{n}-versiya',
  'согласование начато заново': 'kelishish qaytadan boshlandi',

  // Budget of the expense item
  'Использовано': 'Foydalanilgan',
  'Зарезервировано': 'Zaxiralangan',
  'Доступно': 'Mavjud',
  'Останется после заявки': 'Arizadan keyin qoladi',
  'Лимит на этот период не задан.': 'Bu davr uchun limit belgilanmagan.',
  'Мягкий лимит будет превышен: при согласовании понадобится обоснование.': 'Yumshoq limit oshib ketadi: kelishishda asoslash talab qilinadi.',
  'Жёсткий лимит будет превышен: заявку не примут.': 'Qatʼiy limit oshib ketadi: ariza qabul qilinmaydi.',
  'Бюджет статьи': 'Modda byudjeti',
  'Бюджет статьи · {period}': 'Modda byudjeti · {period}',
  'Бюджет статьи «{category}» · {period}': '«{category}» moddasi byudjeti · {period}',

  // Month names in the budget period (lower case, as in running text)
  'январь': 'yanvar',
  'февраль': 'fevral',
  'март': 'mart',
  'апрель': 'aprel',
  'май': 'may',
  'июнь': 'iyun',
  'июль': 'iyul',
  'август': 'avgust',
  'сентябрь': 'sentabr',
  'октябрь': 'oktabr',
  'ноябрь': 'noyabr',
  'декабрь': 'dekabr',

  // Saving the request form (saveFlow.js)
  'Повторное сохранение формы после ошибки загрузки файла': 'Fayl yuklashdagi xatodan keyin shaklni qayta saqlash',
  'Сервер не ответил при создании черновика: он мог сохраниться. Чтобы не создать вторую заявку, закройте форму и найдите черновик в списке заявок.':
    'Qoralama yaratilayotganda server javob bermadi: u saqlangan boʻlishi mumkin. Ikkinchi ariza yaratmaslik uchun shaklni yoping va qoralamani arizalar roʻyxatidan toping.',
  'Заявка {n} уже отправлена на согласование. Дальнейшие изменения — в карточке заявки с указанием причины.':
    '{n} arizasi allaqachon kelishishga yuborilgan. Keyingi oʻzgarishlar — ariza kartochkasida, sababi koʻrsatilgan holda.',
  'Заявка {n} уже в статусе «{status}»: форма её не меняет. Откройте карточку заявки.':
    '{n} arizasi allaqachon «{status}» holatida: shakl uni oʻzgartirmaydi. Ariza kartochkasini oching.',
  'Статус заявки {n} изменился: «{status}». Закройте форму и откройте карточку заявки.':
    '{n} arizasining holati oʻzgardi: «{status}». Shaklni yoping va ariza kartochkasini oching.',
  'Состояние заявки обновлено с сервера (версия {v}). Проверьте поля и нажмите кнопку ещё раз.':
    'Ariza holati serverdan yangilandi ({v}-versiya). Maydonlarni tekshiring va tugmani yana bir bor bosing.',
  'Состояние заявки обновлено с сервера. Проверьте поля и нажмите кнопку ещё раз.':
    'Ariza holati serverdan yangilandi. Maydonlarni tekshiring va tugmani yana bir bor bosing.',
  'Сервер не ответил. Нажмите кнопку ещё раз: форма сначала проверит состояние заявки на сервере.':
    'Server javob bermadi. Tugmani yana bir bor bosing: shakl avval serverdagi ariza holatini tekshiradi.',
  'Для отправки прикрепите: {list}. Черновик можно сохранить без файлов.':
    'Yuborish uchun biriktiring: {list}. Qoralamani fayllarsiz saqlash mumkin.',
  'сервер не ответил': 'server javob bermadi',
  'Файл «{name}» не загружен: {why}. Черновик {number} сохранён; исправьте и нажмите кнопку ещё раз — уже загруженные файлы повторно не отправляются.':
    '«{name}» fayli yuklanmadi: {why}. {number} qoralamasi saqlandi; xatoni tuzating va tugmani yana bir bor bosing — yuklangan fayllar qayta yuborilmaydi.',
  'Черновик {number} сохранён, но не отправлен: {reason}.': '{number} qoralamasi saqlandi, lekin yuborilmadi: {reason}.',

  // Request form (RequestEditor.vue)
  'Изменить {number}': '{number} arizasini tahrirlash',
  'Черновик {number}': 'Qoralama {number}',
  'Новая заявка на оплату': 'Yangi toʻlov arizasi',
  'Добавить файлы': 'Fayllar qoʻshish',
  'Заменить файл': 'Faylni almashtirish',
  'Выбрать файл': 'Faylni tanlash',
  'Сохраняем заявку…': 'Ariza saqlanmoqda…',
  'Загружаем файлы…': 'Fayllar yuklanmoqda…',
  'Отправляем на согласование…': 'Kelishishga yuborilmoqda…',
  'Файл «{name}» убран из черновика.': '«{name}» fayli qoralamadan olib tashlandi.',
  'Черновик {number} сохранён. Уже загруженные файлы показаны в блоках ниже.':
    '{number} qoralamasi saqlandi. Yuklangan fayllar quyidagi bloklarda koʻrsatilgan.',
  'Заявка на согласовании. После сохранения она останется на согласовании, а проверка и согласования начнутся заново. Новый файл заменит прежний (прежняя версия останется в истории); убрать файл можно в карточке заявки с причиной.':
    'Ariza kelishishda. Saqlangandan keyin u kelishishda qoladi, tekshiruv va kelishuvlar esa qaytadan boshlanadi. Yangi fayl avvalgisining oʻrnini oladi (avvalgi versiya tarixda qoladi); faylni ariza kartochkasida sababini koʻrsatib olib tashlash mumkin.',
  '«Сохранить черновик» — без отправки, файлы можно приложить позже. «Отправить на согласование» — сохранить, загрузить файлы и отправить: нужны внутренняя заявка (индент) и договор или счёт. Автор и последний редактор не согласуют свою заявку.':
    '«Qoralamani saqlash» — yubormasdan, fayllarni keyinroq biriktirish mumkin. «Kelishishga yuborish» — saqlash, fayllarni yuklash va yuborish: ichki ariza (indent) hamda shartnoma yoki hisob-faktura kerak. Muallif va oxirgi tahrirlovchi oʻz arizasini kelishmaydi.',
  'Канал оплаты': 'Toʻlov kanali',
  'Счёт списания': 'Pul yechiladigan hisob',
  'Нет счёта для выбранного канала и валюты.': 'Tanlangan kanal va valyuta uchun hisob yoʻq.',
  'Статья расходов': 'Xarajat moddasi',
  'Приоритет': 'Ustuvorlik',
  '{label} · от {n} раб. дн.': '{label} · kamida {n} ish kuni',
  'Плановая дата оплаты': 'Rejadagi toʻlov sanasi',
  'Не раньше {date}: сегодняшняя дата не допускается.': '{date} dan oldin emas: bugungi sana qabul qilinmaydi.',
  'Причина изменения': 'Oʻzgartirish sababi',
  'Документы к заявке': 'Ariza hujjatlari',
  'Первые два раздела обязательны.': 'Dastlabki ikki boʻlim majburiy.',
  'Черновик можно сохранить без файлов. Для отправки обязательны первые два раздела.':
    'Qoralamani fayllarsiz saqlash mumkin. Yuborish uchun dastlabki ikki boʻlim majburiy.',
  'PDF, PNG, JPEG, DOCX или XLSX, до 5 МБ на файл.': 'PDF, PNG, JPEG, DOCX yoki XLSX, har bir fayl 5 MB gacha.',
  'обязательно': 'majburiy',
  'будет заменён': 'almashtiriladi',
  'Убрать': 'Olib tashlash',
  '{size} · не загружен': '{size} · yuklanmagan',
  'Открыть карточку заявки': 'Ariza kartochkasini ochish',
  'Сохранить изменения': 'Oʻzgarishlarni saqlash',
  'Отправить на согласование': 'Kelishishga yuborish',
  'Для отправки не хватает: {list}.': 'Yuborish uchun yetishmaydi: {list}.',

  // Request card (RequestCard.vue)
  'Заявка отправлена на согласование.': 'Ariza kelishishga yuborildi.',
  'Заявка закрыта без оплаты.': 'Ariza toʻlovsiz yopildi.',
  'Заявка возвращена на доработку.': 'Ariza qayta ishlashga qaytarildi.',
  'Решение сохранено.': 'Qaror saqlandi.',
  'Документ изменён после отправки: проверка и согласования начаты заново.':
    'Hujjat yuborilgandan keyin oʻzgartirildi: tekshiruv va kelishuvlar qaytadan boshlandi.',
  'Загружена новая версия документа; прежняя осталась в истории.': 'Hujjatning yangi versiyasi yuklandi; avvalgisi tarixda qoldi.',
  'Документ приложен.': 'Hujjat biriktirildi.',
  'Документ убран.': 'Hujjat olib tashlandi.',
  'Документ убран: проверка и согласования начаты заново.': 'Hujjat olib tashlandi: tekshiruv va kelishuvlar qaytadan boshlandi.',
  'Карточка обновлена.': 'Kartochka yangilandi.',
  'Загрузка заявки…': 'Ariza yuklanmoqda…',
  'Срок оплаты прошёл': 'Toʻlov muddati oʻtgan',
  'Этапы заявки': 'Ariza bosqichlari',
  'Маршрут: проверка бухгалтера → финансовый директор → директор по политике статьи → оплата.':
    'Yoʻnalish: buxgalter tekshiruvi → moliya direktori → modda siyosati boʻyicha direktor → toʻlov.',
  'Канал и счёт': 'Kanal va hisob',
  'Плановая дата': 'Rejadagi sana',
  '{priority} приоритет': '{priority} ustuvorlik',
  'Проект': 'Loyiha',
  'Автор': 'Muallif',
  'Последний редактор': 'Oxirgi tahrirlovchi',
  'Проверил': 'Tekshirgan',
  'Документы': 'Hujjatlar',
  'Необязательно': 'Ixtiyoriy',
  'Причина, не короче 10 символов': 'Sabab, kamida 10 ta belgi',
  'Файл не приложен.': 'Fayl biriktirilmagan.',
  'Приложить файл': 'Fayl biriktirish',
  'Скрыть прежние версии': 'Avvalgi versiyalarni yashirish',
  'Прежние версии: {n}': 'Avvalgi versiyalar: {n}',
  'заменена или убрана': 'almashtirilgan yoki olib tashlangan',
  'убран': 'olib tashlangan',
  'PDF, PNG, JPEG, DOCX или XLSX до 5 МБ. После отправки новая версия документа или удаление прочего документа начинают проверку и согласования заново.':
    'PDF, PNG, JPEG, DOCX yoki XLSX, 5 MB gacha. Yuborilgandan keyin hujjatning yangi versiyasi yoki boshqa hujjatning olib tashlanishi tekshiruv va kelishuvlarni qaytadan boshlaydi.',
  'Факт оплаты': 'Toʻlov fakti',
  'Решение': 'Qaror',
  'Новая дата': 'Yangi sana',
  'Комментарий (обязательно, от 10 символов)': 'Izoh (majburiy, kamida 10 ta belgi)',
  'Заявка завершится без оплаты и останется в истории со статусом «Закрыта без оплаты».':
    'Ariza toʻlovsiz yakunlanadi va tarixda «Toʻlovsiz yopilgan» holati bilan qoladi.',
  'Подтвердить решение': 'Qarorni tasdiqlash',
  'История': 'Tarix',

  // Director policy and work calendar (PolicyPage.vue)
  'Директор утверждает любую сумму': 'Direktor istalgan summani tasdiqlaydi',
  'Директор — только выше порога': 'Direktor — faqat chegaradan yuqori summani',
  'Директор не участвует': 'Direktor ishtirok etmaydi',
  'Политика статьи «{name}» сохранена.': '«{name}» moddasi siyosati saqlandi.',
  'Статья исключена из перечня.': 'Modda roʻyxatdan chiqarildi.',
  'Статья добавлена в перечень регулярных платежей.': 'Modda muntazam toʻlovlar roʻyxatiga qoʻshildi.',
  'Календарь обновлён.': 'Kalendar yangilandi.',
  'Запись календаря удалена.': 'Kalendar yozuvi oʻchirildi.',
  'любая сумма': 'istalgan summa',
  'Порядок согласования и оплаты': 'Kelishish va toʻlov tartibi',
  'Расчётный бухгалтер проверяет реквизиты и комплектность': 'Hisob-kitob buxgalteri rekvizitlar va hujjatlar toʻliqligini tekshiradi',
  'Внутренняя заявка (индент) и договор или счёт обязательны до отправки.':
    'Ichki ariza (indent) hamda shartnoma yoki hisob-faktura yuborishdan oldin majburiy.',
  'Финансовый директор проверяет бюджет и дату': 'Moliya direktori byudjet va sanani tekshiradi',
  'Бюджет статьи повторно проверяется при отправке, каждом согласовании и оплате.':
    'Modda byudjeti yuborishda, har bir kelishishda va toʻlovda qayta tekshiriladi.',
  'Директор — по политике статьи': 'Direktor — modda siyosati boʻyicha',
  'Политика фиксируется при отправке заявки: поздняя правка справочника маршрут уже отправленной заявки не меняет. Нет политики или порога валюты — директор утверждает любую сумму.':
    'Siyosat ariza yuborilganda qayd etiladi: maʼlumotnomaning keyingi tahriri yuborilgan ariza yoʻnalishini oʻzgartirmaydi. Siyosat yoki valyuta chegarasi boʻlmasa, direktor istalgan summani tasdiqlaydi.',
  'Банк — расчётный бухгалтер, касса — кассир. Автор, редактор, проверявший бухгалтер и согласующие заявку не оплачивают.':
    'Bank — hisob-kitob buxgalteri, kassa — kassir. Muallif, tahrirlovchi, tekshirgan buxgalter va arizani kelishganlar toʻlovni amalga oshirmaydi.',
  'Политика директора по статьям': 'Moddalar boʻyicha direktor siyosati',
  'Порог по умолчанию для обычных статей: {uzs} UZS и {usd} USD; EUR без порога — директор всегда.':
    'Oddiy moddalar uchun standart chegara: {uzs} UZS va {usd} USD; EUR uchun chegara yoʻq — direktor har doim ishtirok etadi.',
  'Политика': 'Siyosat',
  'Изменено': 'Oʻzgartirilgan',
  'Регулярный платёж: разрешено без директора': 'Muntazam toʻlov: direktorsiz ruxsat etiladi',
  'Убрать из перечня': 'Roʻyxatdan chiqarish',
  'В перечень без директора': 'Direktorsiz roʻyxatga qoʻshish',
  'Порог {currency}': '{currency} chegarasi',
  'нет — любая сумма': 'yoʻq — istalgan summa',
  'Календарь рабочих дней': 'Ish kunlari kalendari',
  'Суббота и воскресенье — выходные. Праздники с постоянной датой уже внесены; Рамазан и Курбан хайит и переносы выходных добавляются по постановлению. Сроки заявок: обычная — от 7, высокая — от 3, срочная — от 1 рабочего дня. Праздников в {year}: {n}.':
    'Shanba va yakshanba — dam olish kunlari. Sanasi doimiy bayramlar allaqachon kiritilgan; Ramazon va Qurbon hayiti hamda dam olish kunlarining koʻchirilishi qaror asosida qoʻshiladi. Arizalar muddati: oddiy — kamida 7, yuqori — kamida 3, shoshilinch — kamida 1 ish kuni. {year}-yildagi bayramlar: {n}.',
  'Выходной': 'Dam olish kuni',
  'Рабочий': 'Ish kuni',
  'Тип дня': 'Kun turi',
  'Праздник / выходной': 'Bayram / dam olish kuni',
  'Рабочий выходной день': 'Ish kuniga aylantirilgan dam olish kuni',
  'Добавить в календарь': 'Kalendarga qoʻshish',

  // Temporary substitution (Delegations.vue)
  'Действует': 'Amalda',
  'Запланировано': 'Rejalashtirilgan',
  'Истекло': 'Muddati tugagan',
  'Отменено': 'Bekor qilingan',
  'Без основания': 'Asossiz',
  'ВрИО назначен.': 'V.b. tayinlandi.',
  'Замещение отменено.': 'Almashtirish bekor qilindi.',
  'Временное замещение (ВрИО) · {company}': 'Vaqtincha almashtirish (v.b.) · {company}',
  'Скрыть форму': 'Shaklni yashirish',
  'Назначить ВрИО': 'V.b. tayinlash',
  'ВрИО работает под своим логином и на срок замещения получает роль отсутствующего сотрудника в этой компании. После окончания срока права прекращаются автоматически. Собственную заявку ВрИО не согласует; в журнале сохраняются оба сотрудника.':
    'V.b. oʻz logini bilan ishlaydi va almashtirish muddatiga ushbu kompaniyada yoʻq xodimning rolini oladi. Muddat tugagach, huquqlar avtomatik ravishda toʻxtaydi. V.b. oʻz arizasini kelishmaydi; jurnalda ikkala xodim ham saqlanadi.',
  'ВрИО назначается только из сотрудников {company} (или из администраторов холдинга). Сотрудника другой компании сначала переведите сюда в центре администрирования. Смена назначения или архив сотрудника завершают его замещения.':
    'V.b. faqat {company} xodimlari (yoki xolding administratorlari) orasidan tayinlanadi. Boshqa kompaniya xodimini avval boshqaruv markazida shu kompaniyaga oʻtkazing. Xodimning tayinlovi oʻzgarsa yoki u arxivga oʻtkazilsa, uning almashtirishlari tugaydi.',
  'этой компании': 'ushbu kompaniya',
  'Кого заменяет': 'Kimni almashtiradi',
  'В компании нет сотрудника с этой ролью.': 'Kompaniyada bu roldagi xodim yoʻq.',
  'В компании нет другого сотрудника, который может замещать.': 'Kompaniyada almashtira oladigan boshqa xodim yoʻq.',
  'С': 'Boshlanish sanasi',
  'По (включительно)': 'Tugash sanasi (shu kun bilan)',
  'Основание': 'Asos',
  'Например, отпуск по приказу №…': 'Masalan, №… buyruq boʻyicha taʼtil',
  'Назначить': 'Tayinlash',
  'Заменяет': 'Almashtiradi',
  'Отменить': 'Bekor qilish',
  'Причина отмены': 'Bekor qilish sababi',
  'Отменить замещение': 'Almashtirishni bekor qilish',
  'Замещений в этой компании нет.': 'Bu kompaniyada almashtirishlar yoʻq.',
};

// [RegExp over the whole Russian message, Uzbek replacement with $1…] for texts with numbers or names.
export const patterns = [
];
