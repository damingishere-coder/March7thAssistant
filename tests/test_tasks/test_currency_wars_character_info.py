from unittest.mock import Mock, patch

import pytest

from tests.test_tasks.test_currency_wars_settlement import load_currency_wars


@pytest.mark.parametrize('shifted', [False, True])
def test_character_panel_fields_follow_confirmed_layout(shifted):
    module = load_currency_wars()
    war = module.CurrencyWars()
    module.cfg.currencywars_strategy = 'bond_chain'
    values = [None, '藿藿', '1'] if shifted else ['藿藿', '1']
    module.auto.get_single_line_text.side_effect = values
    module.auto.find_element.side_effect = [True, False, True] if shifted else [False, True]
    war.click_origin = Mock()
    with patch.object(module.time, 'sleep'):
        character = war.check_character_info(war.forward_pos[0])
    assert (character.name, character.pos, character.money) == ('藿藿', 'backward', 1)
    crops = [call.kwargs['crop'] for call in module.auto.get_single_line_text.call_args_list]
    assert crops[-1][1] == (228 if shifted else 188) / 1080
    assert war.character_info_cache['藿藿'] is character


def test_empty_slot_without_role_marker_is_not_treated_as_shifted_character():
    module = load_currency_wars()
    war = module.CurrencyWars()
    module.auto.get_single_line_text.return_value = None
    module.auto.find_element.return_value = None
    war.click_origin = Mock()
    with patch.object(module.time, 'sleep'):
        character = war.check_character_info(war.forward_pos[0])
    assert character.name is None
    assert not war.character_info_cache
    assert module.auto.get_single_line_text.call_count == 1


def test_bond_star_levels_are_fresh_per_copy_instead_of_shared_name_cache():
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    war.character_info_cache['藿藿'] = module.CurrencyWarsCharacter('藿藿', 'all', level=1)
    module.auto.get_single_line_text.return_value = '藿藿'
    war.read_character_stars = Mock(side_effect=[3, 1])
    war.click_origin = Mock()
    with patch.object(module.time, 'sleep'):
        strong = war.check_character_info(war.forward_pos[0])
        spare = war.check_character_info(war.prepare_pos[0])
    assert (strong.level, spare.level) == (3, 1)
    assert strong is not spare


def test_shifted_panel_uses_shifted_star_band():
    module = load_currency_wars()
    war = module.CurrencyWars()
    war.bond_campaign = object()
    war.read_character_stars = Mock(return_value=3)
    module.auto.get_single_line_text.side_effect = [None, '藿藿', '1']
    module.auto.find_element.side_effect = [True, False, True]
    war.click_origin = Mock()
    with patch.object(module.time, 'sleep'):
        result = war.check_character_info(war.forward_pos[0])
    assert result.level == 3
    war.read_character_stars.assert_called_once_with((1566 / 1920, 201 / 1080, 126 / 1920, 25 / 1080))
