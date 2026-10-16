// Uzbek (Latin) translations: businessProjects.js: поля, подсказки и проверки бизнес-плана.
// Key: the exact Russian text; value: the Uzbek text with the same {placeholders}.
// Terms as in business_ui.js: исходный — manba, расчёт — hisob-kitob, запасы — zaxiralar,
// дебиторская/кредиторская задолженность — debitorlik/kreditorlik qarzi, на начало — boshlangʻich.
// Sheet and file names of the source documents are {placeholders}: they stay as in the files.
export default {
  // Field labels of the model (BUSINESS_SCALARS, NARRATIVE_FIELDS, fieldLabel)
  'Деньги на начало': 'Boshlangʻich pul mablagʻlari',
  'Дебиторская задолженность на начало': 'Boshlangʻich debitorlik qarzi',
  'Запасы на начало': 'Boshlangʻich zaxiralar',
  'Кредиторская задолженность на начало': 'Boshlangʻich kreditorlik qarzi',
  'Инвестиции до начала прогноза (без CAPEX следующих месяцев)': 'Prognoz boshlanishigacha boʻlgan investitsiyalar (keyingi oylar CAPEXisiz)',
  'Срок оплаты покупателей, дней': 'Xaridorlarning toʻlov muddati, kun',
  'Запасы, дней': 'Zaxiralar, kun',
  'Срок оплаты поставщикам, дней': 'Yetkazib beruvchilarga toʻlov muddati, kun',
  'Описание проекта': 'Loyiha tavsifi',
  'Инициатор проекта': 'Loyiha tashabbuskori',
  'Стратегия развития': 'Rivojlanish strategiyasi',
  'Рынок и сбыт': 'Bozor va sotuv',
  'Сырьё и ресурсы': 'Xomashyo va resurslar',
  'Место реализации': 'Amalga oshirish joyi',
  'Технология и производственный процесс': 'Texnologiya va ishlab chiqarish jarayoni',
  'Организация работы': 'Ishni tashkil etish',
  'Персонал': 'Xodimlar',
  'Назначение инвестиций': 'Investitsiyalar maqsadi',
  'Страхование': 'Sugʻurta',
  'Риски и меры снижения': 'Xavflar va ularni kamaytirish choralari',
  'Название': 'Nomi',
  'Начало прогноза': 'Prognoz boshlanishi',
  'Количество месяцев': 'Oylar soni',
  'Налог на прибыль': 'Foyda soligʻi',
  'Ставка дисконтирования': 'Diskont stavkasi',
  'Постоянные расходы': 'Doimiy xarajatlar',
  'Взносы в капитал': 'Kapitalga badallar',
  'Единица измерения': 'Oʻlchov birligi',
  'Цена реализации': 'Sotish narxi',
  'Себестоимость единицы': 'Birlik tannarxi',
  'Объём реализации': 'Sotuv hajmi',
  'Мощность': 'Quvvat',
  'Стоимость': 'Qiymati',
  'Срок амортизации': 'Amortizatsiya muddati',
  'Месяц ввода': 'Foydalanishga topshirish oyi',
  'Остаток кредита на начало': 'Kreditning boshlangʻich qoldigʻi',
  'Получение кредита': 'Kredit olish',
  'Погашение основного долга': 'Asosiy qarzni soʻndirish',
  'Проценты': 'Foizlar',
  'Исходные файлы': 'Manba fayllar',
  'Документы': 'Hujjatlar',
  'Проверка источников': 'Manbalarni tekshirish',
  'Исходные данные': 'Manba maʼlumotlar',
  'Продукция': 'Mahsulot',
  'Активы': 'Aktivlar',
  'Кредиты': 'Kreditlar',
  'Параметр': 'Parametr',
  'Параметр проекта': 'Loyiha parametri',
  '{label} · месяц {n}': '{label} · {n}-oy',
  'Месяц {n}': '{n}-oy',

  // Guidance: what the field means, where to take the value, how to fill it (fieldGuidance)
  'Название проекта': 'Loyiha nomi',
  'Название, которое будет напечатано в бизнес-плане и ТЭО.': 'Biznes-reja va TIAda chop etiladigan nom.',
  'Название папки или паспорт проекта.': 'Papka nomi yoki loyiha pasporti.',
  'Укажите понятное название проекта; предложенное название папки можно изменить.': 'Loyihaning tushunarli nomini kiriting; taklif qilingan papka nomini oʻzgartirish mumkin.',
  'Первый месяц, за который нужно рассчитать деятельность проекта.': 'Loyiha faoliyati hisoblanadigan birinchi oy.',
  'План запуска проекта и дата подтверждённых начальных остатков.': 'Loyihani ishga tushirish rejasi va tasdiqlangan boshlangʻich qoldiqlar sanasi.',
  'Выберите месяц и год. Начальные остатки должны относиться к началу этого месяца; между датой отчётности и началом прогноза потребуется сверка движений.':
    'Oy va yilni tanlang. Boshlangʻich qoldiqlar shu oyning boshiga tegishli boʻlishi kerak; hisobot sanasi bilan prognoz boshlanishi oraligʻidagi harakatlarni solishtirish talab qilinadi.',
  'Продолжительность финансового прогноза.': 'Moliyaviy prognoz davomiyligi.',
  'Задание на бизнес-план или ТЭО.': 'Biznes-reja yoki TIA uchun topshiriq.',
  'Укажите число месяцев. Каждый помесячный график должен покрывать этот период.': 'Oylar sonini kiriting. Har bir oylik jadval shu davrni qamrab olishi kerak.',
  'Валюта и единицы сумм': 'Valyuta va summa birliklari',
  'Единая валюта и масштаб всех денежных параметров модели.': 'Modeldagi barcha pul parametrlarining yagona valyutasi va masshtabi.',
  'Заголовки исходных таблиц и утверждённые условия пересчёта.': 'Manba jadvallar sarlavhalari va tasdiqlangan qayta hisoblash shartlari.',
  'Выберите валюту и приведите суммы к её денежным единицам. Форма-1 и Форма-2 из папки UZGERMED содержат тысячи сумов. Дату и курс пересчёта нужно подтвердить отдельно.':
    'Valyutani tanlang va summalarni uning pul birliklariga keltiring. UZGERMED papkasidagi 1-shakl va 2-shaklda summalar ming soʻmda. Qayta hisoblash sanasi va kursini alohida tasdiqlash kerak.',
  'Подтверждённая ставка налога на прибыль для этого проекта.': 'Ushbu loyiha uchun tasdiqlangan foyda soligʻi stavkasi.',
  'Налоговые условия организации и согласованные параметры проекта.': 'Tashkilotning soliq shartlari va loyihaning kelishilgan parametrlari.',
  'Введите ставку в процентах. Ставку из старой финансовой модели нужно проверить для выбранного проекта и периода.':
    'Stavkani foizda kiriting. Eski moliyaviy modeldagi stavkani tanlangan loyiha va davr uchun tekshirish kerak.',
  'Явный 0 допустим только при подтверждённой нулевой ставке в расчёте.': 'Aniq 0 faqat hisob-kitobda nol stavka tasdiqlangan boʻlsa ruxsat etiladi.',
  'Годовая ставка дисконтирования': 'Yillik diskont stavkasi',
  'Согласованная ставка для оценки будущих денежных потоков.': 'Kelajakdagi pul oqimlarini baholash uchun kelishilgan stavka.',
  'Утверждённые предпосылки расчёта проекта.': 'Loyiha hisob-kitobining tasdiqlangan dastlabki shartlari.',
  'Введите годовую ставку в процентах. В исходной книге UZGERMED она связана со ставкой кредита; эту связь нужно подтвердить.':
    'Yillik stavkani foizda kiriting. UZGERMED manba kitobida u kredit stavkasi bilan bogʻlangan; bu bogʻliqlikni tasdiqlash kerak.',
  'Явный 0 означает расчёт без дисконтирования и требует осознанного выбора.': 'Aniq 0 diskontlashsiz hisob-kitobni bildiradi va ongli tanlovni talab qiladi.',
  'Доступные деньги проекта на начало первого месяца прогноза.': 'Prognozning birinchi oyi boshida loyihada mavjud pul mablagʻlari.',
  'Остатки банковских счетов и кассы. Для UZGERMED: Форма-1, list02!E46, строка 320, конец II квартала 2026 года, тыс. сумов.':
    'Bank hisoblari va kassa qoldiqlari. UZGERMED uchun: 1-shakl, list02!E46, 320-qator, 2026-yil II chorak oxiri, ming soʻm.',
  'Сверьте остаток с выбранной датой начала и валютой проекта. Укажите общую подтверждённую сумму; будущие поступления учитываются в своих месяцах.':
    'Qoldiqni tanlangan boshlanish sanasi va loyiha valyutasi bilan solishtiring. Umumiy tasdiqlangan summani kiriting; kelgusi tushumlar oʻz oylarida hisobga olinadi.',
  'Укажите 0, если на начало прогноза денег действительно нет.': 'Prognoz boshida pul mablagʻlari haqiqatan ham boʻlmasa, 0 kiriting.',
  'Сумма, которую покупатели уже должны проекту на начало прогноза.': 'Prognoz boshida xaridorlarning loyihaga boʻlgan qarzi summasi.',
  'Расшифровка расчётов с покупателями. Для UZGERMED: Форма-1, list02!E36, строка 220, конец II квартала 2026 года, тыс. сумов.':
    'Xaridorlar bilan hisob-kitoblar tafsiloti. UZGERMED uchun: 1-shakl, list02!E36, 220-qator, 2026-yil II chorak oxiri, ming soʻm.',
  'Выделите задолженность покупателей и подтвердите её остаток на выбранную дату в валюте проекта. Общая дебиторская задолженность отчётности может включать другие расчёты.':
    'Xaridorlar qarzini ajrating va uning tanlangan sanadagi qoldigʻini loyiha valyutasida tasdiqlang. Hisobotdagi umumiy debitorlik qarzi boshqa hisob-kitoblarni ham oʻz ichiga olishi mumkin.',
  'Укажите 0, если задолженности покупателей на начало действительно нет.': 'Boshida xaridorlar qarzi haqiqatan ham boʻlmasa, 0 kiriting.',
  'Стоимость сырья, незавершённого производства и готовой продукции на начало прогноза.': 'Prognoz boshidagi xomashyo, tugallanmagan ishlab chiqarish va tayyor mahsulot qiymati.',
  'Инвентаризация и расшифровка запасов. Для UZGERMED: Форма-1, list02!E27, строка 140, компоненты E28:E30; тыс. сумов.':
    'Inventarizatsiya va zaxiralar tafsiloti. UZGERMED uchun: 1-shakl, list02!E27, 140-qator, tarkibiy qismlar E28:E30; ming soʻm.',
  'Укажите стоимость запасов в валюте проекта. Сверьте дату остатка; количество упаковок само по себе не является стоимостью запасов.':
    'Zaxiralar qiymatini loyiha valyutasida kiriting. Qoldiq sanasini solishtiring; qadoqlar sonining oʻzi zaxiralar qiymati hisoblanmaydi.',
  'Укажите 0, если запасов на начало действительно нет.': 'Boshida zaxiralar haqiqatan ham boʻlmasa, 0 kiriting.',
  'Долг поставщикам за уже полученные товары и услуги на начало прогноза.': 'Prognoz boshida allaqachon olingan tovar va xizmatlar uchun yetkazib beruvchilarga qarz.',
  'Расшифровка расчётов с поставщиками. Для UZGERMED: Форма-1, list02!E81, строка 610, оставлена пустой; нужен подтверждённый остаток.':
    'Yetkazib beruvchilar bilan hisob-kitoblar tafsiloti. UZGERMED uchun: 1-shakl, list02!E81, 610-qator boʻsh qoldirilgan; tasdiqlangan qoldiq kerak.',
  'Уточните именно задолженность поставщикам на выбранную дату. Сверьте её состав и валюту; общий итог обязательств включает также кредиты и другие долги.':
    'Aynan yetkazib beruvchilarga boʻlgan qarzni tanlangan sana boʻyicha aniqlang. Uning tarkibi va valyutasini solishtiring; majburiyatlarning umumiy jami kreditlar va boshqa qarzlarni ham oʻz ichiga oladi.',
  'Укажите 0 только после подтверждения отсутствия долга поставщикам. Пустая строка отчёта такого подтверждения не даёт.':
    '0 ni faqat yetkazib beruvchilarga qarz yoʻqligi tasdiqlangandan keyin kiriting. Hisobotdagi boʻsh qator buni tasdiqlamaydi.',
  'Инвестиции до начала прогноза': 'Prognoz boshlanishigacha boʻlgan investitsiyalar',
  'Отдельная сумма инвестиций до первого месяца расчёта для оценки денежных потоков проекта.':
    'Loyiha pul oqimlarini baholash uchun hisob-kitobning birinchi oyigacha boʻlgan investitsiyalarning alohida summasi.',
  'Смета и подтверждённый план вложений. Лист «{costSheet}» UZGERMED содержит также существующие активы и рабочий капитал.':
    'Smeta va tasdiqlangan investitsiya rejasi. UZGERMED dagi «{costSheet}» varagʻida mavjud aktivlar va aylanma kapital ham bor.',
  'Укажите согласованную сумму до начала прогноза. Покупки активов в следующих месяцах задайте в разделе основных средств; один расход учитывается один раз.':
    'Prognoz boshlanishigacha boʻlgan kelishilgan summani kiriting. Keyingi oylardagi aktiv xaridlarini asosiy vositalar boʻlimida kiriting; bitta xarajat bir marta hisobga olinadi.',
  'Укажите 0, если в выбранном расчёте инвестиций до начала прогноза нет.': 'Tanlangan hisob-kitobda prognoz boshlanishigacha investitsiyalar boʻlmasa, 0 kiriting.',
  'Регулярные операционные расходы, которые не включены в затраты на единицу продукции.': 'Mahsulot birligiga xarajatlarga kiritilmagan muntazam operatsion xarajatlar.',
  'Смета постоянных затрат, штатное расписание и коммунальные условия. В UZGERMED: листы «{laborSheet}» и «{workshopSheet}».':
    'Doimiy xarajatlar smetasi, shtat jadvali va kommunal shartlar. UZGERMED da: «{laborSheet}» va «{workshopSheet}» varaqlari.',
  'Сложите подтверждённые постоянные расходы и укажите месячную сумму или график. Амортизация, проценты и затраты на единицу продукции рассчитываются отдельно.':
    'Tasdiqlangan doimiy xarajatlarni qoʻshing va oylik summani yoki jadvalni kiriting. Amortizatsiya, foizlar va mahsulot birligiga xarajatlar alohida hisoblanadi.',
  'Укажите 0 только для месяца, в котором постоянных расходов действительно нет.': '0 ni faqat doimiy xarajatlar haqiqatan ham boʻlmagan oy uchun kiriting.',
  'Деньги, которые собственники внесут в проект в течение прогноза.': 'Mulkdorlar prognoz davomida loyihaga kiritadigan pul mablagʻlari.',
  'План финансирования и подтверждённые решения собственников.': 'Moliyalashtirish rejasi va mulkdorlarning tasdiqlangan qarorlari.',
  'Задайте сумму или график будущих взносов по месяцам. Уже имеющийся уставный капитал сам по себе не означает новое денежное поступление.':
    'Kelgusi badallar summasini yoki oylar boʻyicha jadvalini kiriting. Mavjud ustav kapitalining oʻzi yangi pul tushumini anglatmaydi.',
  'Если будущих денежных взносов нет, укажите 0 явно.': 'Kelgusida pul badallari boʻlmasa, 0 ni aniq kiriting.',
  'Срок оплаты покупателей': 'Xaridorlarning toʻlov muddati',
  'Согласованное число дней, за которое покупатели оплачивают реализацию.': 'Xaridorlar sotilgan mahsulot uchun toʻlov qiladigan kelishilgan kunlar soni.',
  'Условия продаж и договоры с покупателями.': 'Sotuv shartlari va xaridorlar bilan shartnomalar.',
  'Введите подтверждённый срок в днях. При разных условиях продаж согласуйте общий срок для этой модели.':
    'Tasdiqlangan muddatni kunlarda kiriting. Sotuv shartlari turlicha boʻlsa, ushbu model uchun umumiy muddatni kelishib oling.',
  'Укажите 0, если в модели покупатели оплачивают реализацию сразу.': 'Modelda xaridorlar darhol toʻlasa, 0 kiriting.',
  'Согласованный срок хранения запасов для расчёта оборотного капитала.': 'Aylanma kapitalni hisoblash uchun zaxiralarni saqlashning kelishilgan muddati.',
  'Производственный и закупочный план. В UZGERMED сырьё и готовая продукция имеют отдельные сроки на листе «{capitalSheet}».':
    'Ishlab chiqarish va xarid rejasi. UZGERMED da xomashyo va tayyor mahsulot uchun «{capitalSheet}» varagʻida alohida muddatlar belgilangan.',
  'Подтвердите единый срок запасов в днях для этой модели.': 'Ushbu model uchun zaxiralarning yagona muddatini kunlarda tasdiqlang.',
  'Укажите 0 только при согласованном расчёте без остатка запасов.': '0 ni faqat zaxira qoldigʻisiz hisob-kitob kelishilgan boʻlsa kiriting.',
  'Срок оплаты поставщикам': 'Yetkazib beruvchilarga toʻlov muddati',
  'Согласованный срок оплаты закупок и возникновения задолженности поставщикам.': 'Xaridlar uchun toʻlov va yetkazib beruvchilarga qarz yuzaga kelishining kelishilgan muddati.',
  'Договоры поставок и план расчётов.': 'Yetkazib berish shartnomalari va hisob-kitoblar rejasi.',
  'Введите подтверждённый срок в днях. Условия разных поставщиков нужно согласовать для общего расчёта.':
    'Tasdiqlangan muddatni kunlarda kiriting. Turli yetkazib beruvchilar shartlarini umumiy hisob-kitob uchun kelishib olish kerak.',
  'Укажите 0, если в модели задолженность поставщикам не возникает.': 'Modelda yetkazib beruvchilarga qarz yuzaga kelmasa, 0 kiriting.',
  'Продукты или услуги, из которых складываются выручка и переменные затраты.': 'Tushum va oʻzgaruvchan xarajatlarni tashkil etuvchi mahsulot yoki xizmatlar.',
  'Производственный план, прайс-лист и калькуляции.': 'Ishlab chiqarish rejasi, narxlar roʻyxati va kalkulyatsiyalar.',
  'Добавьте хотя бы одну позицию и заполните название, единицу, цену, затраты на единицу и объём реализации.':
    'Kamida bitta pozitsiya qoʻshing va nomi, birligi, narxi, birlik xarajatlari hamda sotuv hajmini toʻldiring.',
  'Однозначное название продукции или услуги.': 'Mahsulot yoki xizmatning aniq nomi.',
  'Ассортимент и производственный план.': 'Assortiment va ishlab chiqarish rejasi.',
  'Укажите название, дозировку и упаковку, если они различают позиции.': 'Pozitsiyalar nomi, dozasi va qadogʻi bilan farq qilsa, ularni koʻrsating.',
  'Единица, к которой относятся цена, затраты и объём.': 'Narx, xarajatlar va hajm tegishli boʻlgan birlik.',
  'Прайс-лист и производственный план.': 'Narxlar roʻyxati va ishlab chiqarish rejasi.',
  'Укажите единицу, например упаковка или килограмм; все три показателя должны использовать эту единицу.':
    'Birlikni kiriting, masalan qadoq yoki kilogramm; uchala koʻrsatkich ham shu birlikda boʻlishi kerak.',
  'Цена реализации одной единицы без НДС в валюте проекта.': 'Bir birlikning QQSsiz sotish narxi, loyiha valyutasida.',
  'Подтверждённый действующий прайс-лист. В UZGERMED между основным файлом и дополнительными ценами есть расхождения.':
    'Tasdiqlangan amaldagi narxlar roʻyxati. UZGERMED da asosiy fayl va qoʻshimcha narxlar oʻrtasida tafovutlar bor.',
  'Введите выбранную цену за указанную единицу и подтвердите её валюту и учёт НДС.': 'Koʻrsatilgan birlik uchun tanlangan narxni kiriting, uning valyutasi va QQS hisobga olinishini tasdiqlang.',
  'Явный 0 означает реализацию без выручки; указывайте его только при таком плане.': 'Aniq 0 tushumsiz sotuvni bildiradi; uni faqat shunday reja boʻlsa kiriting.',
  'Переменные затраты на одну единицу продукции.': 'Mahsulot birligiga oʻzgaruvchan xarajatlar.',
  'Калькуляция материалов и других переменных затрат. Основная книга UZGERMED выделяет материальную себестоимость.':
    'Materiallar va boshqa oʻzgaruvchan xarajatlar kalkulyatsiyasi. UZGERMED ning asosiy kitobida moddiy tannarx alohida koʻrsatilgan.',
  'Уточните полный состав переменных затрат на единицу; постоянные расходы отражаются отдельно.':
    'Birlikka toʻgʻri keladigan oʻzgaruvchan xarajatlarning toʻliq tarkibini aniqlang; doimiy xarajatlar alohida aks ettiriladi.',
  'Укажите 0 только при подтверждённом отсутствии переменных затрат на единицу.': '0 ni faqat birlikka oʻzgaruvchan xarajatlar yoʻqligi tasdiqlangan boʻlsa kiriting.',
  'Количество единиц реализации в каждом месяце прогноза.': 'Prognozning har bir oyida sotiladigan birliklar soni.',
  'План продаж и производства. В UZGERMED годовые объёмы преобразованы в месячные по правилу исходной модели.':
    'Sotuv va ishlab chiqarish rejasi. UZGERMED da yillik hajmlar manba model qoidasi boʻyicha oylik hajmlarga aylantirilgan.',
  'Укажите одно количество для каждого месяца или заполните все месяцы графика; подтвердите правило распределения годового объёма.':
    'Har bir oy uchun bitta miqdorni kiriting yoki jadvalning barcha oylarini toʻldiring; yillik hajmni taqsimlash qoidasini tasdiqlang.',
  'Укажите 0 для месяца, в котором реализации действительно не будет.': 'Sotuv haqiqatan ham boʻlmaydigan oy uchun 0 kiriting.',
  'Производственная мощность в тех же единицах, что и объём реализации.': 'Ishlab chiqarish quvvati, sotuv hajmi bilan bir xil birliklarda.',
  'Технические данные производства.': 'Ishlab chiqarishning texnik maʼlumotlari.',
  'Если мощность включена, заполните значение или каждый месяц графика. Если ограничение мощности не используется, снимите отметку «Указать производственную мощность».':
    'Quvvat yoqilgan boʻlsa, qiymatni yoki jadvalning har bir oyini toʻldiring. Quvvat cheklovi ishlatilmasa, «Ishlab chiqarish quvvatini koʻrsatish» belgisini olib tashlang.',
  'Явный 0 означает отсутствие мощности в соответствующем месяце.': 'Aniq 0 tegishli oyda quvvat yoʻqligini bildiradi.',
  'Основные средства': 'Asosiy vositalar',
  'Активы, по которым модель рассчитывает амортизацию и покупки в периоде.': 'Model amortizatsiyasini va davr ichidagi xaridlarini hisoblaydigan aktivlar.',
  'Ведомость основных средств и план покупок. {assetsFile} UZGERMED описывает существующие активы на конец II квартала 2026 года.':
    'Asosiy vositalar qaydnomasi va xaridlar rejasi. UZGERMED dagi {assetsFile} fayli 2026-yil II chorak oxiridagi mavjud aktivlarni tavsiflaydi.',
  'Если активы есть, нажмите «Добавить актив» и укажите его стоимость, срок и месяц ввода. Для существующего актива нужны остаточная стоимость и оставшийся срок амортизации.':
    'Aktivlar boʻlsa, «Aktiv qoʻshish» tugmasini bosing va uning qiymati, muddati va foydalanishga topshirish oyini kiriting. Mavjud aktiv uchun qoldiq qiymati va amortizatsiyaning qolgan muddati kerak.',
  'Отметьте «Основных средств в этой модели нет» только при их действительном отсутствии в выбранной модели.':
    '«Bu modelda asosiy vositalar yoʻq» belgisini faqat tanlangan modelda ular haqiqatan ham boʻlmasa qoʻying.',
  'Название отдельного актива или обоснованной группы активов.': 'Alohida aktiv yoki asoslangan aktivlar guruhining nomi.',
  'Ведомость основных средств или смета закупки.': 'Asosiy vositalar qaydnomasi yoki xarid smetasi.',
  'Укажите понятное уникальное название.': 'Tushunarli va takrorlanmas nom kiriting.',
  'Стоимость актива в валюте проекта.': 'Aktivning loyiha valyutasidagi qiymati.',
  'Остаточная стоимость существующего актива или подтверждённая стоимость покупки нового.': 'Mavjud aktivning qoldiq qiymati yoki yangi aktivning tasdiqlangan xarid qiymati.',
  'Введите стоимость, соответствующую выбранному месяцу ввода и составу актива.': 'Tanlangan foydalanishga topshirish oyi va aktiv tarkibiga mos qiymatni kiriting.',
  'Явный 0 допустим только для подтверждённой нулевой стоимости в этом расчёте.': 'Aniq 0 faqat ushbu hisob-kitobda nol qiymat tasdiqlangan boʻlsa ruxsat etiladi.',
  'Срок амортизации актива в месяцах.': 'Aktivning amortizatsiya muddati, oylarda.',
  'Данные об учёте актива и согласованные параметры амортизации.': 'Aktiv hisobi maʼlumotlari va kelishilgan amortizatsiya parametrlari.',
  'Для существующего актива укажите оставшийся срок, для нового — его срок амортизации. Годовая ставка сама по себе не подтверждает оставшийся срок.':
    'Mavjud aktiv uchun qolgan muddatni, yangi aktiv uchun esa uning amortizatsiya muddatini kiriting. Yillik stavkaning oʻzi qolgan muddatni tasdiqlamaydi.',
  'Месяц, с которого актив участвует в расчёте.': 'Aktiv hisob-kitobda ishtirok eta boshlaydigan oy.',
  'Дата начала прогноза и план ввода актива.': 'Prognoz boshlanish sanasi va aktivni foydalanishga topshirish rejasi.',
  '0 — существующий актив на начало прогноза; 1 — первый месяц. Для новой покупки укажите соответствующий месяц прогноза.':
    '0 — prognoz boshida mavjud aktiv; 1 — birinchi oy. Yangi xarid uchun prognozning tegishli oyini kiriting.',
  'Действующие и планируемые кредиты с денежными графиками.': 'Pul jadvallari bilan amaldagi va rejalashtirilgan kreditlar.',
  'Кредитные договоры, графики платежей и подтверждённые остатки. В UZGERMED есть разные валюты и версии графиков.':
    'Kredit shartnomalari, toʻlov jadvallari va tasdiqlangan qoldiqlar. UZGERMED da turli valyutalar va jadval versiyalari bor.',
  'Если кредиты есть, нажмите «Добавить кредит» и заполните долг на начало, выдачи, погашения и суммы процентов. Сверьте каждый график с договором и выбранной датой.':
    'Kreditlar boʻlsa, «Kredit qoʻshish» tugmasini bosing va boshlangʻich qarz, kredit berilishi, soʻndirishlar va foizlar summalarini toʻldiring. Har bir jadvalni shartnoma va tanlangan sana bilan solishtiring.',
  'Отметьте «Кредитов в этой модели нет» только если в выбранной модели действительно нет кредитов.':
    '«Bu modelda kreditlar yoʻq» belgisini faqat tanlangan modelda kreditlar haqiqatan ham boʻlmasa qoʻying.',
  'Название, позволяющее отличить кредит от других.': 'Kreditni boshqalaridan ajratishga imkon beradigan nom.',
  'Кредитный договор.': 'Kredit shartnomasi.',
  'Укажите уникальное название или идентификатор договора.': 'Takrorlanmas nom yoki shartnoma identifikatorini kiriting.',
  'Непогашенный основной долг на начало прогноза.': 'Prognoz boshidagi soʻndirilmagan asosiy qarz.',
  'Выписка кредитора или сверенный график на выбранную дату.': 'Kreditor koʻchirmasi yoki tanlangan sana boʻyicha solishtirilgan jadval.',
  'Введите остаток основного долга в валюте проекта и проверьте, что график относится к действующему договору.':
    'Asosiy qarz qoldigʻini loyiha valyutasida kiriting va jadval amaldagi shartnomaga tegishli ekanini tekshiring.',
  'Укажите 0 для нового кредита, который будет получен в периоде прогноза, если до его начала долга действительно нет.':
    'Prognoz davrida olinadigan yangi kredit uchun, agar prognoz boshlanishigacha qarz haqiqatan ham boʻlmasa, 0 kiriting.',
  'Суммы получения кредита в каждом месяце.': 'Har bir oyda olinadigan kredit summalari.',
  'План выдач и траншей кредитора.': 'Kreditorning kredit berish va transhlar rejasi.',
  'Задайте месячный график получения денег. Одно значение в форме повторяется каждый месяц.': 'Pul olishning oylik jadvalini kiriting. Shakldagi bitta qiymat har oy takrorlanadi.',
  'Укажите 0 во всех месяцах, когда выдачи кредита не будет.': 'Kredit berilmaydigan barcha oylarda 0 kiriting.',
  'Суммы погашения основного долга в каждом месяце.': 'Har bir oyda asosiy qarzni soʻndirish summalari.',
  'График погашения кредитора.': 'Kreditorning soʻndirish jadvali.',
  'Введите суммы погашения по месяцам; проценты указываются отдельным графиком.': 'Soʻndirish summalarini oylar boʻyicha kiriting; foizlar alohida jadvalda koʻrsatiladi.',
  'Укажите 0 в месяце без погашения основного долга.': 'Asosiy qarz soʻndirilmaydigan oyda 0 kiriting.',
  'Денежные суммы процентов в каждом месяце.': 'Har bir oydagi foizlarning pul summalari.',
  'Проверенный график начислений кредитора.': 'Kreditorning tekshirilgan foiz hisoblash jadvali.',
  'Введите суммы процентов в валюте проекта. Процентная ставка вместо суммы не заполнит этот график.':
    'Foizlar summalarini loyiha valyutasida kiriting. Summa oʻrniga kiritilgan foiz stavkasi bu jadvalni toʻldirmaydi.',
  'Укажите 0 только для месяца без процентов.': '0 ni faqat foizlarsiz oy uchun kiriting.',
  'Обязательное значение для выбранного расчёта.': 'Tanlangan hisob-kitob uchun majburiy qiymat.',
  'Подтверждённый документ или согласованный параметр проекта.': 'Tasdiqlangan hujjat yoki loyihaning kelishilgan parametri.',
  'Заполните значение по исходным данным; пустое поле не означает ноль.': 'Qiymatni manba maʼlumotlar boʻyicha toʻldiring; boʻsh maydon nolni anglatmaydi.',

  // Hints for empty fields: what to write and an illustrative example (emptyFieldHint)
  'Например: {example}': 'Masalan: {example}',
  'Производство таблеток в Ташкенте': 'Toshkentda tabletkalar ishlab chiqarish',
  'Парацетамол 500 мг, 20 таблеток': 'Paratsetamol 500 mg, 20 ta tabletka',
  'упаковка': 'qadoq',
  'Таблеточный пресс': 'Tabletka pressi',
  'Кредит банка, договор № 12/26': 'Bank krediti, shartnoma № 12/26',
  'Что производит или продаёт проект, для кого и зачем; срок и масштаб.': 'Loyiha nima ishlab chiqaradi yoki sotadi, kim uchun va nima maqsadda; muddati va koʻlami.',
  'Выпуск таблетированных препаратов на арендованной площадке; 3 позиции, 1,2 млн упаковок в год':
    'Ijaradagi maydonda tabletka shaklidagi dori vositalarini ishlab chiqarish; 3 ta pozitsiya, yiliga 1,2 mln qadoq',
  'Кто реализует проект: компания, опыт, собственники, контакты ответственного.': 'Loyihani kim amalga oshiradi: kompaniya, tajriba, mulkdorlar, masʼul shaxs kontaktlari.',
  'ООО «…», 8 лет на фармацевтическом рынке, собственный склад и дистрибуция': '«…» MChJ, farmatsevtika bozorida 8 yil, oʻz ombori va distributsiyasi',
  'Цели на 3–5 лет: рост продаж, новые продукты и рынки, этапы развития.': '3–5 yillik maqsadlar: sotuvlar oʻsishi, yangi mahsulotlar va bozorlar, rivojlanish bosqichlari.',
  'Первый год — запуск 3 позиций, второй — экспорт в Казахстан': 'Birinchi yil — 3 ta pozitsiyani ishga tushirish, ikkinchi yil — Qozogʻistonga eksport',
  'Кто покупатели, объём рынка, конкуренты, цены и каналы сбыта.': 'Xaridorlar kimlar, bozor hajmi, raqobatchilar, narxlar va sotuv kanallari.',
  'Аптечные сети Ташкента и областей; основные конкуренты — импортные аналоги': 'Toshkent va viloyatlardagi dorixona tarmoqlari; asosiy raqobatchilar — import analoglari',
  'Какое сырьё и материалы нужны, где их закупать, сроки поставки.': 'Qanday xomashyo va materiallar kerak, ularni qayerdan xarid qilish, yetkazib berish muddatlari.',
  'Субстанция из Индии, упаковка — местный поставщик, поставка 45 дней': 'Substansiya — Hindistondan, qadoq — mahalliy yetkazib beruvchidan, yetkazib berish 45 kun',
  'Где будет работать проект: адрес, площадь, своя или аренда, коммуникации.': 'Loyiha qayerda ishlaydi: manzil, maydon, oʻziniki yoki ijara, kommunikatsiyalar.',
  'Ташкент, Сергелийский район, 1 200 м² в аренде, электричество и вода подведены': 'Toshkent, Sergeli tumani, 1 200 m² ijarada, elektr va suv ulangan',
  'Как устроено производство: этапы, оборудование, мощность, контроль качества.': 'Ishlab chiqarish qanday tashkil etilgan: bosqichlar, uskunalar, quvvat, sifat nazorati.',
  'Смешивание → таблетирование → блистеры; линия до 5 000 упаковок в смену': 'Aralashtirish → tabletkalash → blisterlash; liniya smenasiga 5 000 tagacha qadoq',
  'Как организована работа: структура управления, смены, подрядчики.': 'Ish qanday tashkil etilgan: boshqaruv tuzilmasi, smenalar, pudratchilar.',
  'Директор, производство, ОТК, склад; работа в 2 смены': 'Direktor, ishlab chiqarish, sifat nazorati boʻlimi, ombor; 2 smenada ishlash',
  'Сколько сотрудников, какие должности, зарплаты и обучение.': 'Xodimlar soni, lavozimlar, ish haqi va oʻqitish.',
  '25 человек: 15 на производстве, 4 в ОТК, 6 в управлении': '25 kishi: 15 nafari ishlab chiqarishda, 4 nafari sifat nazoratida, 6 nafari boshqaruvda',
  'На что пойдут деньги: оборудование, ремонт, оборотный капитал — с суммами.': 'Pul nimaga sarflanadi: uskunalar, taʼmirlash, aylanma kapital — summalari bilan.',
  'Пресс 180 000, ремонт цеха 40 000, оборотные средства 30 000': 'Press 180 000, sexni taʼmirlash 40 000, aylanma mablagʻlar 30 000',
  'Что и у кого страхуется: имущество, ответственность, груз.': 'Nima va qayerda sugʻurtalanadi: mol-mulk, javobgarlik, yuk.',
  'Оборудование и склад — страховая компания «…», ежегодно': 'Uskunalar va ombor — «…» sugʻurta kompaniyasi, har yili',
  'Основные риски проекта и как их снижать.': 'Loyihaning asosiy xavflari va ularni kamaytirish yoʻllari.',
  'Рост цен на сырьё — договоры на год; задержка поставки — запас на 45 дней': 'Xomashyo narxining oshishi — bir yillik shartnomalar; yetkazib berish kechikishi — 45 kunlik zaxira',

  // Choice of source files (prepareSources, sourceSize)
  'Служебный файл операционной системы': 'Operatsion tizimning xizmat fayli',
  '{path}: формат не поддерживается. Допустимы PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG и JPEG.':
    '{path}: format qoʻllab-quvvatlanmaydi. Ruxsat etilgan formatlar: PDF, XLSX, XLTX, DOCX, CSV, TXT, JSON, ZIP, PNG va JPEG.',
  '{name}: небезопасное имя или путь файла.': '{name}: fayl nomi yoki yoʻli xavfsiz emas.',
  '{path}: путь выбран дважды.': '{path}: yoʻl ikki marta tanlangan.',
  '{path}: файл должен быть непустым и не больше 20 МБ.': '{path}: fayl boʻsh boʻlmasligi va 20 MB dan oshmasligi kerak.',
  'В одной папке допускается не больше 100 исходных файлов.': 'Bitta papkada koʻpi bilan 100 ta manba fayl boʻlishi mumkin.',
  'Общий размер исходных файлов — не больше 100 МБ.': 'Manba fayllarning umumiy hajmi — 100 MB dan oshmasligi kerak.',
  'Выберите хотя бы один исходный файл.': 'Kamida bitta manba faylni tanlang.',
  '{n} МБ': '{n} MB',
  '{n} КБ': '{n} KB',

  // Project header checks (headerProblem, missingHeaderFields)
  'Укажите название проекта: до 160 символов.': 'Loyiha nomini kiriting: 160 belgigacha.',
  'Выберите первый месяц прогноза.': 'Prognozning birinchi oyini tanlang.',
  'Прогноз: от 1 до 60 месяцев.': 'Prognoz: 1 dan 60 oygacha.',
  'Весь прогноз должен заканчиваться не позже декабря 2100 года.': 'Butun prognoz 2100-yil dekabridan kechikmay tugashi kerak.',
  'Выберите USD, UZS или EUR.': 'USD, UZS yoki EUR ni tanlang.',
  'Прогноз, месяцев': 'Prognoz, oy',
  'Валюта модели': 'Model valyutasi',

  // Upload, saving and calculation of a project
  'Загрузка не завершена.': 'Yuklash yakunlanmadi.',
  '{message} Уже загруженные файлы сохранены. Повторная попытка продолжит этот проект.': '{message} Yuklangan fayllar saqlangan. Qayta urinish shu loyihani davom ettiradi.',
  '{message} Повторите загрузку: проверим файл и продолжим этот проект.': '{message} Yuklashni takrorlang: fayl tekshiriladi va shu loyiha davom ettiriladi.',
  'Сервер вернул проект другой компании. Обновите список.': 'Server boshqa kompaniyaning loyihasini qaytardi. Roʻyxatni yangilang.',
  'Раздел или компания изменены': 'Boʻlim yoki kompaniya oʻzgartirildi',
  'Форма относится к другому проекту или компании.': 'Shakl boshqa loyiha yoki kompaniyaga tegishli.',
  'Форма относится к другой компании.': 'Shakl boshqa kompaniyaga tegishli.',
  'Изменение этого проекта недоступно.': 'Bu loyihani oʻzgartirish mumkin emas.',
  'Изменение этой папки недоступно.': 'Bu papkani oʻzgartirish mumkin emas.',
  'Укажите название проекта.': 'Loyiha nomini kiriting.',
  'Создаём папку проекта…': 'Loyiha papkasi yaratilmoqda…',
  'Папка проекта создана': 'Loyiha papkasi yaratildi',
  'Загружаем {n} из {total}: {path}': 'Yuklanmoqda, {n}/{total}: {path}',
  'Файл загружен: {path}': 'Fayl yuklandi: {path}',
  'Проверяем источники и параметры отчёта…': 'Manbalar va hisobot parametrlari tekshirilmoqda…',
  'Источники проверены': 'Manbalar tekshirildi',

  // Source review (issueMessage, sourceLocation)
  'Проверьте исходный файл и параметры проекта.': 'Manba fayl va loyiha parametrlarini tekshiring.',
  'лист «{sheet}»': '«{sheet}» varagʻi',
  'стр. {page}': '{page}-bet',
};

// [RegExp over the whole Russian message, Uzbek replacement with $1…] for texts with numbers or names.
export const patterns = [
];
