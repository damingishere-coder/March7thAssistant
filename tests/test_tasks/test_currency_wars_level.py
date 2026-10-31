import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from tasks.weekly.currency_wars_bonds import prepare_level_image
from tests.test_tasks.test_currency_wars_settlement import load_currency_wars


def test_level_seven_uses_tight_crop_after_original_region_has_no_text():
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    war.read_level_without_highlight = Mock(return_value=None)
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
    war.read_level_without_highlight = Mock(return_value=None)
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
    war.read_level_without_highlight = Mock(return_value=None)
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


def test_actual_level_glyph_is_preserved_across_experience_highlight():
    root = Path(__file__).parents[1] / 'fixtures/currency_wars_levels'
    masks = [np.asarray(prepare_level_image(Image.open(root / f'seven_{kind}.png')))[:, :, 0] == 0
             for kind in ('plain', 'highlight')]
    assert min(mask.sum() for mask in masks) > 500
    assert np.logical_and(*masks).sum() / np.logical_or(*masks).sum() > .95


@pytest.mark.parametrize('result,expected', [
    (('Lv.7', .93), 7), (('Lv.10', .96), 10),
    (('Lv.7', .60), None), (('Lv.17', .98), None), (None, None),
])
def test_filtered_level_requires_valid_range_and_confidence(result, expected):
    module = load_currency_wars()
    war = module.CurrencyWars()
    module.auto.take_screenshot.return_value = True
    module.auto.screenshot = Image.open(Path(__file__).parents[1] /
                                       'fixtures/currency_wars_levels/seven_highlight.png')
    reader = Mock()
    reader.recognize_single_line.return_value = result
    with patch.dict(sys.modules, {'module.ocr': SimpleNamespace(ocr=reader)}):
        assert war.read_level_without_highlight() == expected
    module.auto.press_key.assert_not_called()


@pytest.mark.parametrize('digit,expected', [(('7', .999), 7), (('7', .90), None), (('1', .999), None)])
def test_flashing_level_row_can_recover_from_independent_numeric_crop(digit, expected):
    module = load_currency_wars()
    war = module.CurrencyWars()
    module.auto.take_screenshot.return_value = True
    module.auto.screenshot = Image.open(Path(__file__).parents[1] /
                                       'fixtures/currency_wars_levels/seven_flash.png')
    reader = Mock()
    reader.recognize_single_line.side_effect = [None, digit]
    with patch.dict(sys.modules, {'module.ocr': SimpleNamespace(ocr=reader)}):
        assert war.read_level_without_highlight() == expected
    assert reader.recognize_single_line.call_args.args[0].size == (128, 144)
    module.auto.press_key.assert_not_called()
