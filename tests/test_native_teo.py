from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.native_teo import NativeTeoError, build_teo_pdf, quality_notes


def synthetic_values():
    """Fixed-shape native address fixture with invented test amounts only."""
    values = {sheet: {} for sheet in ['Стоим_проекта', 'РКЛ', 'ВНД', 'Приб_Убыт', 'Поток_нал',
                                      'Производ. с учетом загрузки', 'амортизац', 'Баланс']}
    values['Стоим_проекта'].update(F34='1200', B34='600', D34='400', E34='200', B27='300', B28='250',
                                 D28='400', B30='20', B31='10', B33='20', B38='2')
    values['ВНД'].update(D6='450', E6='0.3', C3='0.09', W41='1')
    values['Поток_нал']['M83'] = '7200'
    values['Баланс']['M59'] = '0'
    for month in range(36):
        year, local = divmod(month, 12)
        col = chr(66 + local)
        cf = chr((67 if year == 0 else 66) + local)
        # Variation makes accidental reuse of an annual or first-month value visible.
        revenue = Decimal(100 + month)
        profit = Decimal(30 + month)
        cash = Decimal(50 + month)
        values['Производ. с учетом загрузки'][col + str([53, 92, 129][year])] = str(revenue * Decimal('1.1'))
        for row, val in [(4, revenue), (9, 40), (11, 5), (13, 2), (16, 1), (17, 3), (18, profit)]:
            values['Приб_Убыт'][col + str(row + 23 * year)] = str(val)
        values['Поток_нал'][cf + str([27, 55, 80][year])] = str(cash)
        values['Поток_нал'][cf + str([25, 53, 78][year])] = '0'
        values['Поток_нал'][cf + str([26, 54, 79][year])] = '1'
        if month == 0:
            values['РКЛ']['AA14'] = '400'
    for year in range(3):
        start = year * 12
        values['Приб_Убыт']['N' + str(4 + 23 * year)] = str(sum(Decimal(100 + m) for m in range(start, start + 12)))
        values['Приб_Убыт']['N' + str(18 + 23 * year)] = str(sum(Decimal(30 + m) for m in range(start, start + 12)))
        values['Поток_нал'][['O27', 'N55', 'N80'][year]] = str(sum(Decimal(50 + m) for m in range(start, start + 12)))
    return values


def profile():
    return {'title': 'Синтетический проект', 'parameters': [
        {'sheet': 'Стоим_проекта', 'cell': 'B38', 'label': 'Тестовый курс', 'unit': 'UZS/USD', 'value': '999'},
        {'sheet': 'ВНД', 'cell': 'C3', 'label': 'Тестовая ставка', 'unit': 'доля', 'value': '0.8'},
    ], 'metrics': [{'sheet': 'Приб_Убыт', 'cell': 'B4', 'original': 999999}]}


def test_report_reads_all36_fresh_results_and_preserves_method_and_provenance():
    values = synthetic_values()
    raw = build_teo_pdf(values, profile(), [{'filename': 'образец.xlsx', 'sha256': 'a' * 64}])
    reader = PdfReader(BytesIO(raw))
    assert 4 <= len(reader.pages) <= 6
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Синтетический проект' in text
    assert '1 200.00' in text and '450.00' in text
    assert '148.50' in text and '135.00' in text and '65.00' in text and '85.00' in text
    assert '9.00%' in text and '2.00 UZS/USD' in text
    assert '999' not in text  # no stale profile original or parameter fallback
    assert 'образец.xlsx' in text and 'SHA-256' in text
    assert 'момент 0' in text and 'лет 4-8' in text
    assert 'ВНД!B3:B6' in text
    assert 'и три годовых денежных потока после погашения кредитов' not in ' '.join(text.split())
    assert 'Нет записи' in text
    assert 'относительные месяцы 1-36' in text
    for page in reader.pages:
        assert 'Страница' in page.extract_text()


def test_zuma_unfilled_own_working_capital_is_displayed_as_absence_not_zero():
    values = synthetic_values()
    del values['Стоим_проекта']['B28']
    meta = {**profile(), 'profile_id': 'zuma-36m-usd', 'title': 'ZUMA — синтетический пример'}
    reader = PdfReader(BytesIO(build_teo_pdf(values, meta, [])))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Нет записи в исходной модели' in text
    assert 'не объявлено подтверждённым нулём' in ' '.join(text.split())
    with pytest.raises(NativeTeoError, match='B28'):
        build_teo_pdf(values, profile(), [])
    values['Стоим_проекта']['B28'] = '#REF!'
    with pytest.raises(NativeTeoError, match='B28'):
        build_teo_pdf(values, meta, [])


def test_report_records_selected_template_updates_with_provenance():
    meta = profile()
    meta.update(input_review={'items': [
        {'key': 'price:test', 'label': 'Цена синтетического продукта', 'unit': 'UZS',
         'proposals': [{'id': 'proposal-test', 'source': 'prices.xlsx', 'sheet': 'Prices', 'cell': 'B2'}]},
        {'key': 'volume:test', 'label': 'Объём синтетического продукта', 'unit': 'шт', 'proposals': []},
    ]}, data_updates={'price:test': 125, 'volume:test': 80},
        review_decisions={'price:test': {'choice': 'source', 'proposal_id': 'proposal-test'},
                          'volume:test': {'choice': 'manual'}},
        quality_notes=['Контрольная заметка исходного шаблона.'])
    reader = PdfReader(BytesIO(build_teo_pdf(synthetic_values(), meta, [])))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Согласованные новые данные шаблона' in text
    assert '125.00 UZS' in text and '80.00 шт' in text
    assert 'prices.xlsx' in text and 'Prices!B2' in text
    assert 'Введено пользователем' in text
    assert 'Контрольная заметка исходного шаблона.' in text


@pytest.mark.parametrize('bad', [None, '', '#REF!', '#DIV/0!', 'NaN', 'Infinity', True])
def test_required_error_never_falls_back_to_original_metric_or_zero(bad):
    values = synthetic_values()
    values['Приб_Убыт']['B4'] = bad
    with pytest.raises(NativeTeoError, match='Приб_Убыт!B4'):
        build_teo_pdf(values, profile(), [])


def test_missing_required_cell_is_not_silently_defaulted_and_annual_sum_is_checked():
    values = synthetic_values()
    del values['Поток_нал']['M80']
    with pytest.raises(NativeTeoError, match='Поток_нал!M80'):
        build_teo_pdf(values, profile(), [])
    values = synthetic_values()
    values['Приб_Убыт']['N27'] = '1'
    with pytest.raises(NativeTeoError, match='N27 не совпадают'):
        build_teo_pdf(values, profile(), [])


def test_notes_follow_current_values_and_disappear_after_pattern_is_repaired():
    values = synthetic_values()
    values['Баланс']['M59'] = '-4'
    values['РКЛ']['AA32'] = '40'
    values['Поток_нал'].update(H55='60', H56='60')
    values['амортизац'].update(D33='3', D42='8', **{f'D{row}': '1' for row in range(34, 42)})
    values['ВНД']['W41'] = '2'
    notes = quality_notes(values)
    assert len(notes) == 5
    assert any('Баланс!M59' in note for note in notes)
    assert any('19-м месяце' in note for note in notes)
    assert any('D33 и D42' in note for note in notes)
    assert any('W41' in note for note in notes)
    values['Баланс']['M59'] = '4'
    values['Поток_нал']['H56'] = '100'
    values['амортизац']['D42'] = '11'
    values['ВНД']['W41'] = '1'
    assert len(quality_notes(values)) == 1  # period interpretation remains explicit
    assert quality_notes({}) == []


def test_amortization_diagnostic_ignores_sparse_excel_sum_blanks_but_never_errors():
    values = synthetic_values()
    values['амортизац'].update(D33='3', D38='8', D42='8')
    assert any('D33 и D42' in note for note in quality_notes(values))
    values['амортизац']['D35'] = '#REF!'
    assert not any('D33 и D42' in note for note in quality_notes(values))


def test_present_loan_draw_error_is_not_presented_as_no_entry():
    values = synthetic_values()
    values['РКЛ']['AA32'] = '#REF!'
    with pytest.raises(NativeTeoError, match='РКЛ!AA32'):
        build_teo_pdf(values, profile(), [])


def test_calendar_metadata_labels_report_without_changing_financial_results():
    values = synthetic_values()
    reader = PdfReader(BytesIO(build_teo_pdf(values, profile(), [], start_metadata={
        'confirmed': True, 'label': 'Период, подтверждённый владельцем'})))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Период, подтверждённый владельцем' in text
    assert 'Календарное начало не подтверждено' not in text
    assert 'не переносит автоматически' in ' '.join(text.split())
    assert '1 200.00' in text


def test_archive_period_never_claims_confirmed_calendar_start_or_changes_results():
    values = synthetic_values()
    reader = PdfReader(BytesIO(build_teo_pdf(values, profile(), [], start_metadata={
        'kind': 'archive_period', 'confirmed': True, 'label': 'Период архива с 2026-10-01'})))
    text = ' '.join('\n'.join(page.extract_text() for page in reader.pages).split())
    assert 'Период архива с 2026-10-01' in text
    assert 'Дата архива не меняет календарные даты исходной модели.' in text
    assert 'Подтверждённый календарный период' not in text
    assert 'тестового генератора' not in text
    assert '1 200.00' in text
