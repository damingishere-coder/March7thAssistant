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
