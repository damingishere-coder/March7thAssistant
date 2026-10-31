from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from tasks.weekly.currency_wars_bonds import BondTarget, character_stars, choose_bond_purchase
from tests.test_tasks.test_currency_wars_bond_runner import campaign


def character(name, level=1, cost=1):
    return SimpleNamespace(name=name, level=level, money=cost)


@pytest.mark.parametrize('name,count', [('one', 1), ('two', 2), ('three', 3)])
def test_real_star_glyphs_are_counted(name, count):
    path = Path(__file__).parents[1] / 'fixtures/currency_wars_stars' / (name + '.png')
    assert character_stars(Image.open(path)) == count


def test_star_recognition_rejects_blank_and_gold_background():
    assert character_stars(Image.new('RGB', (126, 25), '#101b22')) == 0
    assert character_stars(Image.new('RGB', (126, 25), '#ffdc80')) == 0


def test_missing_high_cost_member_beats_owned_upgrades():
    roster = [character('阿格莱雅', 2), character('阿格莱雅')]
    assert choose_bond_purchase({'云璃', '阿格莱雅'}, roster, {'云璃', '阿格莱雅'},
                                5, {'云璃': 5, '阿格莱雅': 1}, 7) == '云璃'


@pytest.mark.parametrize('stars', [[3], [0], [2, 1]])
def test_full_unknown_or_already_two_star_member_does_not_drain_missing_budget(stars):
    roster = [character('阿格莱雅', star) for star in stars]
    assert choose_bond_purchase({'阿格莱雅'}, roster, {'云璃', '阿格莱雅'}, 35,
                                {'云璃': 5, '阿格莱雅': 1}, 7, allow_upgrades=True) is None


def test_early_pair_can_finish_two_star_but_single_copy_cannot_start_chasing():
    args = ({'阿格莱雅', '云璃'}, 10, {'阿格莱雅': 1, '云璃': 5}, 5)
    assert choose_bond_purchase({'阿格莱雅'}, [character('阿格莱雅')], *args) is None
    assert choose_bond_purchase({'阿格莱雅'}, [character('阿格莱雅')] * 2, *args) == '阿格莱雅'


def prepare_economy(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    runner.target = BondTarget('style', '能量', 7)
    runner.members = {'云璃', '藿藿'}
    runner.member_costs = {'云璃': 5, '藿藿': 1}
    war.current_level = 7
    war.current_stage = '2-7'
    war.character_purchase_costs = {}
    war.forward_characters = []
    war.backward_characters = []
    war.prepare_characters = []
    war.get_level = Mock(return_value=True)
    return module, stubs, war, runner


def test_35_gold_is_invested_partially_before_reroll_and_keeps_missing_card_money(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    state = {'money': 35, 'experience': 8}
    runner.read_money = Mock(side_effect=lambda: state['money'])
    module.auto.get_single_line_text.side_effect = lambda *args, **kwargs: f"{state['experience']}/52"
    def press(key):
        assert key == 'f', '人口不足时不应先刷新商店'
        state['money'] -= 4
        state['experience'] += 4
    module.auto.press_key.side_effect = press
    with patch.object(module.time, 'sleep'):
        runner.invest_experience()
    assert state == {'money': 7, 'experience': 36}
    assert module.auto.press_key.call_count == 7


def test_experience_input_without_balance_change_stops_instead_of_retrying(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    runner.read_money = Mock(return_value=35)
    module.auto.get_single_line_text.return_value = '8/52'
    with patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='金币未减少'):
        runner.invest_experience()
    assert module.auto.press_key.call_count == 7


def test_unconfirmed_level_after_purchase_stops_before_any_additional_experience(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    war.get_level.side_effect = [True, False]
    runner.read_money = Mock(side_effect=[15, 11])
    module.auto.get_single_line_text.return_value = '26/28'
    with patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='未确认小队等级'):
        runner.invest_experience()
    assert module.auto.press_key.call_count == 1


def test_late_game_search_is_allowed_at_sufficient_population(tmp_path):
    _, _, war, runner = prepare_economy(tmp_path)
    war.current_level = 8
    assert not runner.late_search()
    war.current_stage = '3-5'
    assert runner.late_search()
    war.current_level = 6
    assert not runner.late_search()


def test_shop_buys_last_member_without_rerolling_before_population_goal(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    war.forward_characters = [character('藿藿', 3)]
    war._get_ocr_texts_in_crop = Mock(return_value=['云璃', '藿藿'])
    def refresh():
        war.prepare_characters = [character('云璃', 1, 5)]
    war.check_character_status = Mock(side_effect=refresh)
    runner.read_money = Mock(side_effect=[12, 7, 7])
    module.auto.find_element.return_value = True
    module.auto.click_element.return_value = True
    with patch.object(module.time, 'sleep'):
        runner.buy_bond_characters(reroll=False)
    clicked = [call.args[0] for call in module.auto.click_element.call_args_list]
    assert clicked.count('云璃') == 1
    assert '藿藿' not in clicked
    war.check_character_status.assert_called_once()
    module.auto.press_key.assert_not_called()


def test_merge_to_three_stars_is_reread_before_any_second_purchase(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    runner.members = {'藿藿'}
    war.current_level = 9
    war.prepare_characters = [character('藿藿', 2), character('藿藿', 2),
                              character('藿藿'), character('藿藿')]
    war._get_ocr_texts_in_crop = Mock(return_value=['藿藿'])
    def refresh():
        war.prepare_characters = [character('藿藿', 3)]
    war.check_character_status = Mock(side_effect=refresh)
    runner.read_money = Mock(side_effect=[20, 19, 19])
    module.auto.find_element.return_value = True
    module.auto.click_element.return_value = True
    with patch.object(module.time, 'sleep'):
        runner.buy_bond_characters(reroll=True)
    assert sum(call.args[0] == '藿藿' for call in module.auto.click_element.call_args_list) == 1
    module.auto.press_key.assert_not_called()


def test_unavailable_shop_never_receives_refresh_or_close_inputs(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    module.auto.find_element.side_effect = [False, True]
    runner.read_money = Mock()
    with patch.object(module.time, 'sleep'):
        runner.buy_bond_characters(reroll=True)
    runner.read_money.assert_not_called()
    assert module.auto.click_element.call_count == 1
    module.auto.press_key.assert_not_called()


def test_invitation_and_description_prefer_missing_member_over_owned_member(tmp_path):
    module, _, war, runner = prepare_economy(tmp_path)
    war.forward_characters = [character('藿藿', 2)]
    war._get_ocr_texts_in_crop = Mock(return_value=['藿藿', '云璃'])
    module.auto.click_element.return_value = True
    assert runner.choose_invitation((0, 0, 1, 1))
    assert module.auto.click_element.call_args.args[0] == '云璃'
    assert runner.choose_text_option([['能量', '获得藿藿'], ['获得云璃']]) == 1


def test_unknown_balance_is_not_interpreted_as_a_successful_purchase(tmp_path):
    module, _, _, runner = prepare_economy(tmp_path)
    module.auto.get_single_line_text.return_value = None
    with pytest.raises(RuntimeError, match='未确认金币余额'):
        runner.read_money()
