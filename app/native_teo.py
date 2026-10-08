"""Source-based TEO for the fixed 36-month native workbook.

Financial results must be the recalculator's fresh sheet/cell map. No cached
result, generic model input, missing value or Excel error is substituted.
"""
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from .business_exports import _font


class NativeTeoError(ValueError):
    """A required freshly calculated source result is unavailable."""


def _optional(values, sheet, cell):
    raw = values.get(sheet, {}).get(cell) if isinstance(values, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and abs(number) <= Decimal('1e30') else None


def _number(values, sheet, cell):
    number = _optional(values, sheet, cell)
    if number is None:
        raise NativeTeoError(f'Не рассчитана обязательная ячейка {sheet}!{cell}. ТЭО не сформировано.')
    return number


def _close(left, right):
    return abs(left - right) <= max(Decimal('0.000001'), abs(right) * Decimal('0.000000001'))


def quality_notes(values):
    """Explain retained source policies only when their numerical pattern exists."""
    notes = []
    closing_debt = _optional(values, 'Баланс', 'M59')
    if closing_debt is not None and closing_debt < 0:
        notes.append('В исходной методике остаток кредита в конце 36-го месяца отрицательный '
                     '(Баланс!M59). Перед использованием отчёта для решения о финансировании '
                     'нужно согласовать учёт выдач и погашений.')
    renewal = _optional(values, 'РКЛ', 'AA32')
    operating = _optional(values, 'Поток_нал', 'H55')
    funded = _optional(values, 'Поток_нал', 'H56')
    if (renewal is not None and renewal > 0 and operating is not None and funded is not None
            and funded - operating < renewal and not _close(funded - operating, renewal)):
        notes.append('В графике РКЛ есть повторная выдача в 19-м месяце (РКЛ!AA32), '
                     'а денежный поток этого месяца её не включает (Поток_нал!H55:H56). '
                     'Сохранена исходная методика; повторная выдача не добавлена автоматически.')
    building = _optional(values, 'амортизац', 'D33')
    total = _optional(values, 'амортизац', 'D42')
    # The fresh map is sparse. Empty cells inside Excel SUM are ignored for
    # this diagnostic only; an error/nonblank invalid cell is never ignored.
    asset_values = values.get('амортизац', {}) if isinstance(values, dict) else {}
    item_cells = [f'D{row}' for row in range(34, 42)]
    items = [_optional(values, 'амортизац', cell) for cell in item_cells if asset_values.get(cell) is not None]
    if (building is not None and building > 0 and total is not None
            and all(item is not None for item in items) and _close(total, sum(items, Decimal(0)))):
        notes.append('Итог амортизации третьего года не включает здания '
                     '(амортизац!D33 и D42; аналогично по месяцам). '
                     'Это влияет на прибыль и налог третьего года; исправление требует отдельной версии расчёта.')
    factor = _optional(values, 'ВНД', 'W41')
    if factor is not None and factor != 1:
        notes.append('Цены реализации в исходной книге содержат коэффициент ВНД!W41. '
                     'Он сохранён в расчёте; перед изменением цен нужно подтвердить его назначение.')
    if _optional(values, 'Приб_Убыт', 'B4') is not None:
        notes.append('Расчёт охватывает относительные месяцы 1-36 и годы 1-3. '
                     'Выбранная дата архива является подписью отчёта и не переносит автоматически '
                     'календарь платежей прежних кредитов в исходной книге.')
    return notes


def _col(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _amount(number):
    return format(number, ',.2f').replace(',', ' ')


def _percent(number):
    return format(number * 100, '.2f') + '%'


def _text(value, limit=600):
    return str(value or '').replace('\x00', '')[:limit]


def _financial_rows(values):
    monthly = []
    for month in range(36):
        year, local = divmod(month, 12)
        col = _col(local + 2)
        cf_col = _col(local + (3 if year == 0 else 2))
        monthly.append({
            'month': month + 1, 'year': year + 1,
            'gross_sales': _number(values, 'Производ. с учетом загрузки', col + str([53, 92, 129][year])),
            'revenue': _number(values, 'Приб_Убыт', col + str(4 + 23 * year)),
            'cogs': _number(values, 'Приб_Убыт', col + str(9 + 23 * year)),
            'period_costs': _number(values, 'Приб_Убыт', col + str(11 + 23 * year)),
            'interest': _number(values, 'Приб_Убыт', col + str(13 + 23 * year)),
            'property_tax': _number(values, 'Приб_Убыт', col + str(16 + 23 * year)),
            'profit_tax': _number(values, 'Приб_Убыт', col + str(17 + 23 * year)),
            'profit': _number(values, 'Приб_Убыт', col + str(18 + 23 * year)),
            'cash': _number(values, 'Поток_нал', cf_col + str([27, 55, 80][year])),
            'new_draw': _optional_draw(values, 'AA' + str(14 + month)),
            'new_principal': _number(values, 'Поток_нал', cf_col + str([25, 53, 78][year])),
            'old_principal': _number(values, 'Поток_нал', cf_col + str([26, 54, 79][year])),
        })
    return monthly


def _optional_draw(values, cell):
    raw = values.get('РКЛ', {}).get(cell)
    if raw is None:
        return None  # sparse empty source cell, displayed as absence of entry
    return _number(values, 'РКЛ', cell)  # present error cannot masquerade as blank


def build_teo_pdf(values, profile, source_manifest, parameters=None, start_metadata=None):
    """Build a five-page A4 report from required fresh native output addresses.

    Optional metadata labels the report only. It never overrides financial
    results. Unrelated errors elsewhere in the workbook stay source issues;
    an error in any report cell prevents this PDF from being presented as ready.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

    profile = profile if isinstance(profile, dict) else {}
    months = _financial_rows(values)
    annual = []
    fields = ['gross_sales', 'revenue', 'cogs', 'period_costs', 'interest', 'property_tax', 'profit_tax',
              'profit', 'cash', 'new_principal', 'old_principal']
    for year in range(3):
        annual.append({field: sum((row[field] for row in months[year * 12:(year + 1) * 12]), Decimal(0)) for field in fields})
    investment = {key: _number(values, 'Стоим_проекта', cell) for key, cell in {
        'total': 'F34', 'own': 'B34', 'new_loan': 'D34', 'old_debt': 'E34',
        'assets': 'B27', 'own_working': 'B28', 'new_working': 'D28',
        'insurance': 'B30', 'commission': 'B31', 'other': 'B33'}.items()}
    npv = _number(values, 'ВНД', 'D6')
    irr = _number(values, 'ВНД', 'E6')
    discount = _number(values, 'ВНД', 'C3')
    cash36 = _number(values, 'Поток_нал', 'M83')
    # These sums must describe the same source formula revision.
    for year, anchors in enumerate([('N4', 'N18', 'O27'), ('N27', 'N41', 'N55'), ('N50', 'N64', 'N80')]):
        for field, sheet, cell in [('revenue', 'Приб_Убыт', anchors[0]), ('profit', 'Приб_Убыт', anchors[1]), ('cash', 'Поток_нал', anchors[2])]:
            if not _close(annual[year][field], _number(values, sheet, cell)):
                raise NativeTeoError(f'Месячные и годовые результаты {sheet}!{cell} не совпадают. ТЭО не сформировано.')

    font = _font()
    blue = colors.HexColor('#23384D')
    pale = colors.HexColor('#EAF0F5')
    grey = colors.HexColor('#64748B')
    width = A4[0] - 72
    styles = {
        'title': ParagraphStyle('native-teo-title', fontName=font, fontSize=19, leading=25, textColor=blue, spaceAfter=12),
        'heading': ParagraphStyle('native-teo-heading', fontName=font, fontSize=12, leading=16, textColor=blue, spaceAfter=8, spaceBefore=7, keepWithNext=True),
        'body': ParagraphStyle('native-teo-body', fontName=font, fontSize=9, leading=13, spaceAfter=8),
        'small': ParagraphStyle('native-teo-small', fontName=font, fontSize=7.7, leading=10.5, textColor=grey, spaceAfter=7),
        'cell': ParagraphStyle('native-teo-cell', fontName=font, fontSize=8, leading=10.5, wordWrap='CJK'),
        'compact': ParagraphStyle('native-teo-compact', fontName=font, fontSize=7.3, leading=9.2, wordWrap='CJK'),
        'number': ParagraphStyle('native-teo-number', fontName=font, fontSize=7.6, leading=9.5, alignment=TA_RIGHT),
    }
    parts = []

    def para(text, style='body'):
        return Paragraph(escape(_text(text, 10000)).replace('\n', '<br/>'), styles[style])

    def body(text, style='body'):
        parts.append(para(text, style))

    def heading(text):
        body(text, 'heading')

    def table(headers, rows, widths, compact=False):
        data = [[para(item, 'compact' if compact else 'cell') for item in headers]]
        for row in rows:
            data.append([para(_amount(item), 'number') if isinstance(item, Decimal) else para(item, 'compact' if compact else 'cell') for item in row])
        item = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        padding = 2.6 if compact else 5
        item.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), pale), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                  ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                                  ('TOPPADDING', (0, 0), (-1, -1), padding), ('BOTTOMPADDING', (0, 0), (-1, -1), padding),
                                  ('LINEBELOW', (0, 0), (-1, 0), .6, colors.HexColor('#94A3B8')),
                                  ('LINEBELOW', (0, 1), (-1, -1), .2, colors.HexColor('#DCE3EA'))]))
        parts.extend([item, Spacer(1, 7)])

    title = _text(profile.get('title') or 'Проект по исходной модели', 240)
    body('Технико-экономическое обоснование', 'title')
    body(title, 'heading')
    body('Горизонт: месяцы 1-36, годы 1-3. Валюта расчётов: доллары США (USD). '
         'Суммы приведены в долларах, без деления на тысячи или миллионы.')
    if isinstance(start_metadata, dict) and start_metadata.get('confirmed') and start_metadata.get('label'):
        body('Подтверждённый календарный период: ' + _text(start_metadata['label'], 160), 'small')
    else:
        body('Календарное начало не подтверждено: даты графика прежних кредитов и подпись начального остатка расходятся. '
             'Использованы относительные месяцы исходной модели.', 'small')
    body('Расчёты выполнены заново по формулам загруженной книги. Сохранённые результаты Excel '
         'и условные параметры тестового генератора не использованы для заполнения этого ТЭО.')
    heading('1. Инвестиции и источники финансирования')
    table(['Показатель', 'Сумма, USD', 'Источник'], [
        ['Стоимость проекта', investment['total'], 'Стоим_проекта!F34'],
        ['Собственное участие', investment['own'], 'Стоим_проекта!B34'],
        ['Новый банковский кредит', investment['new_loan'], 'Стоим_проекта!D34'],
        ['Ранее полученные кредиты', investment['old_debt'], 'Стоим_проекта!E34'],
    ], [width * .48, width * .26, width * .26])
    heading('2. Результат за три года')
    table(['Показатель', 'Значение'], [
        ['Выручка без НДС за 36 месяцев', sum((x['revenue'] for x in annual), Decimal(0))],
        ['Чистая прибыль за 36 месяцев', sum((x['profit'] for x in annual), Decimal(0))],
        ['Накопленный денежный поток на 36-й месяц', cash36],
        ['NPV за три года · ВНД!D6', npv],
        ['IRR годовая · ВНД!E6', _percent(irr)],
        ['Годовая ставка дисконтирования · ВНД!C3', _percent(discount)],
    ], [width * .64, width * .36])
    body('NPV и IRR воспроизводят исходную методику: первоначальная стоимость проекта в момент 0 '
         'и три годовых денежных потока после погашения кредитов. Показатели лет 4-8 из книги '
         'не используются в отчёте за 36 месяцев.', 'small')
    parts.append(PageBreak())

    heading('3. Состав первоначальной стоимости')
    table(['Статья', 'Сумма, USD', 'Источник'], [
        ['Основные средства по стоимости проекта', investment['assets'], 'Стоим_проекта!B27'],
        ['Собственный оборотный капитал', investment['own_working'], 'Стоим_проекта!B28'],
        ['Новый кредит на оборотный капитал', investment['new_working'], 'Стоим_проекта!D28'],
        ['Остаток прежних кредитов', investment['old_debt'], 'Стоим_проекта!E34'],
        ['Страхование залога', investment['insurance'], 'Стоим_проекта!B30'],
        ['Банковская комиссия', investment['commission'], 'Стоим_проекта!B31'],
        ['Прочие затраты проекта', investment['other'], 'Стоим_проекта!B33'],
    ], [width * .48, width * .26, width * .26])
    body('Остаток прежних кредитов входит в исходную стоимость проекта. Стоимость основных средств '
         'в расчёте проекта и база амортизации различаются в оригинале; они не сложены повторно.', 'small')
    heading('4. Годовая выручка, затраты и прибыль')
    labels = [('gross_sales', 'Реализация с НДС'), ('revenue', 'Выручка без НДС'), ('cogs', 'Производственная себестоимость'),
              ('period_costs', 'Расходы периода'), ('interest', 'Проценты по всем кредитам'), ('property_tax', 'Налог на имущество по модели'),
              ('profit_tax', 'Налог на прибыль по модели'), ('profit', 'Чистая прибыль'), ('cash', 'Поток после погашения кредитов')]
    table(['Показатель, USD', 'Год 1', 'Год 2', 'Год 3'], [[label] + [x[field] for x in annual] for field, label in labels],
          [width * .40, width * .20, width * .20, width * .20])
    body('Источники: Приб_Убыт!B4:M18, B27:M41, B50:M64; Производ. с учетом загрузки!B53:M53, '
         'B92:M92, B129:M129; Поток_нал!C27:N27, B55:M55, B80:M80. '
         'Годовые суммы проверены против итоговых ячеек исходной книги.', 'small')
    parts.append(PageBreak())

    heading('5. Помесячные результаты за 36 месяцев')
    body('Выручка без НДС отделена от реализации с НДС. Денежный поток приведён после уплаты '
         'процентов, налогов и погашения основного долга по исходным формулам.', 'small')
    table(['Месяц', 'Реализация с НДС', 'Выручка без НДС', 'Чистая прибыль', 'Денежный поток'],
          [[str(x['month']), x['gross_sales'], x['revenue'], x['profit'], x['cash']] for x in months],
          [width * .08] + [width * .23] * 4, compact=True)
    body('Все суммы в USD. Реализация учитывает изменение запаса готовой продукции, '
         'поэтому выпуск и продажи со второго месяца могут различаться.', 'small')
    parts.append(PageBreak())

    heading('6. Погашение кредитов и денежный поток')
    body('Получение кредита не является выручкой. Погашение основного долга не является расходом '
         'при расчёте прибыли; оно уменьшает денежный поток. Ниже показаны исходные отдельные графики.', 'small')
    table(['Месяц', 'Выдача новой линии', 'Погашение новой линии', 'Погашение прежних кредитов', 'Проценты всего'],
          [[str(x['month']), x['new_draw'] if x['new_draw'] is not None else 'Нет записи', x['new_principal'],
            x['old_principal'], x['interest']] for x in months], [width * .08] + [width * .23] * 4, compact=True)
    body('Источники: РКЛ!AA14:AA49; Поток_нал!C25:N26, B53:M54, B78:M79; '
         'Приб_Убыт!B13:M13, B36:M36, B59:M59. Пустая ячейка выдачи показана как отсутствие записи, '
         'а не как подтверждённый денежный ноль.', 'small')
    parts.append(PageBreak())

    heading('7. Замечания к исходной методике')
    notes = quality_notes(values)
    if notes:
        for index, note in enumerate(notes, 1): body(f'{index}. {note}')
    else:
        body('Числовые признаки проверяемых расхождений в этой версии результатов не обнаружены. '
             'Это не заменяет проверку остальных исходных формул и документов.')
    body('Ошибки Excel в неиспользуемых категориях и периодах не заменяются нулями. '
         'Готовность этого ТЭО означает наличие свежих числовых результатов именно для показанных '
         '36 месяцев; она не подтверждает все формулы остальных разделов книги.', 'small')
    heading('8. Исходные параметры расчёта')
    controls = profile.get('parameters') if isinstance(profile.get('parameters'), list) else []
    control_rows = []
    for control in controls[:24]:
        if not isinstance(control, dict): continue
        sheet, cell = control.get('sheet'), control.get('cell')
        fresh = _optional(values, sheet, cell)
        if fresh is None: continue
        unit = _text(control.get('unit'), 40)
        formatted = _percent(fresh) if unit == 'доля' else _amount(fresh)
        control_rows.append([_text(control.get('label'), 160), formatted + (' ' + unit if unit != 'доля' else ''), f'{sheet}!{cell}'])
    if control_rows:
        table(['Параметр', 'Значение', 'Ячейка'], control_rows, [width * .49, width * .23, width * .28], compact=True)
    heading('9. Источники и границы отчёта')
    manifest = source_manifest if isinstance(source_manifest, list) else [source_manifest] if isinstance(source_manifest, dict) else []
    for source in manifest[:12]:
        if not isinstance(source, dict): continue
        name = source.get('filename') or source.get('name') or 'Загруженный исходный файл'
        digest = source.get('sha256')
        body(_text(name, 220) + (f' · SHA-256: {_text(digest, 64)}' if digest else ''), 'small')
    body('Это отдельное ТЭО на расчётах исходной Excel-модели. Полный бизнес-план '
         'сохраняет структуру исходного образца. Excel-версия расчёта сохраняет листы и оформление '
         'загруженной книги. Налоговые ставки и условия кредита в отчёте являются параметрами '
         'источника, а не новой финансовой или правовой рекомендацией.', 'small')

    stream = BytesIO()
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 7.3)
        canvas.setFillColor(grey)
        canvas.drawString(36, 20, 'ТЭО · исходная модель · 36 месяцев · USD')
        canvas.drawRightString(A4[0] - 36, 20, f'Страница {document.page}')
        canvas.restoreState()
    SimpleDocTemplate(stream, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=32, bottomMargin=35,
                      title='ТЭО: ' + title, author='Zuma Cash Flow').build(parts, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
