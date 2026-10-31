from unittest.mock import patch

import pytest

from tests.test_tasks.test_currency_wars_settlement import load_currency_wars


def test_level_seven_uses_tight_crop_after_original_region_has_no_text():
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    war.current_level = 6
    # 实际故障截图：原区域无文本，紧裁等级行识别为“.7”。
    module.auto.get_single_line_text.side_effect = [None, '.7']
    assert war.get_level() and war.current_level == 7
    assert module.auto.get_single_line_text.call_args.kwargs['crop'] == (
        250 / 1920, 880 / 1080, 85 / 1920, 54 / 1080)
    module.auto.press_key.assert_not_called()


def test_level_transition_waits_and_takes_new_read_before_proceeding():
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    module.auto.get_single_line_text.side_effect = [None, 'Lv.', 'Lv.8']
    with patch.object(module.time, 'sleep') as sleep:
        assert war.get_level() and war.current_level == 8
    sleep.assert_called_once_with(.5)
    module.auto.press_key.assert_not_called()


@pytest.mark.parametrize('text', [None, 'Lv.', '2/52', 'Lv.17'])
def test_unconfirmed_level_is_bounded_and_does_not_overwrite_or_spend(text):
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    war.current_level = 6
    module.auto.get_single_line_text.return_value = text
    with patch.object(module.time, 'sleep'):
        assert not war.get_level()
    assert war.current_level == 6
    assert module.auto.get_single_line_text.call_count == 6
    module.auto.press_key.assert_not_called()


def test_other_strategies_keep_original_single_crop_behavior():
    module = load_currency_wars()
    war = module.CurrencyWars()
    module.auto.get_single_line_text.return_value = 'Lv.10'
    assert war.get_level() and war.current_level == 10
    assert module.auto.get_single_line_text.call_count == 1
