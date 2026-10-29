import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tasks.weekly.currency_wars_bonds import BondProgress, BondReward, BondTarget


def load_runner():
    stubs = {}
    for name, attributes in {
        'module.automation': {'auto': Mock()},
        'module.config': {'cfg': Mock()},
        'module.logger': {'log': Mock()},
        'module.screen': {'screen': Mock()},
        'tasks.base.base': {'Base': Mock()},
        'module.notification.notification': {'NotificationLevel': Mock()},
    }.items():
        stub = ModuleType(name)
        stub.__dict__.update(attributes)
        stubs[name] = stub
    path = Path(__file__).parents[2] / 'tasks/weekly/currency_wars_bond_runner.py'
    spec = importlib.util.spec_from_file_location('bond_runner_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module, stubs


def campaign(tmp_path):
    module, stubs = load_runner()
    war = SimpleNamespace(result=False, start_war=Mock(return_value=True), loop=Mock())
    with patch.object(module, 'BondProgress', return_value=BondProgress(tmp_path / 'progress.json')):
        runner = module.BondCampaign(war)
    runner.load_members = Mock()
    return module, stubs, war, runner


def test_complete_campaign_does_not_start_a_game(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    runner.scan_rewards = Mock(return_value=([BondReward('style', '追击', 7, True, True)], 20, 20))
    with patch.dict(sys.modules, stubs):
        assert runner.run() is True
    war.start_war.assert_not_called()
    runner.load_members.assert_not_called()


def test_victory_without_target_reward_stops_at_three_not_infinite_loop(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    war.result = True
    runner.scan_rewards = Mock(return_value=([BondReward('style', '追击', 7, True, False)], 0, 20))
    with patch.dict(sys.modules, stubs):
        assert runner.run() is False
    assert war.start_war.call_count == 3
    assert runner.progress.data['paused'] is True
    assert runner.progress.data['target'] == 'style:追击'


def test_unconfirmed_settlement_never_restarts(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    war.result = None
    runner.scan_rewards = Mock(return_value=([BondReward('style', '追击', 7, True, False)], 0, 20))
    with patch.dict(sys.modules, stubs), pytest.raises(RuntimeError, match='结果未确认'):
        runner.run()
    assert war.start_war.call_count == 1
    assert runner.progress.data['in_flight'] is True
    assert runner.progress.data['failures'] == 0


def test_interrupted_game_is_not_abandoned_on_resume(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    runner.progress.data['in_flight'] = True
    runner.scan_rewards = Mock()
    with patch.dict(sys.modules, stubs), pytest.raises(RuntimeError, match='上次羁绊对局中断'):
        runner.run()
    runner.scan_rewards.assert_not_called()
    war.start_war.assert_not_called()


def test_purple_one_is_verified_without_touching_rank_arrows(tmp_path):
    module, _, war, runner = campaign(tmp_path)
    runner.read_rank = Mock(return_value=('A5紫金', 1))
    with patch.object(module.time, 'sleep'):
        assert runner.choose_level()
    module.auto.click_element.assert_not_called()


def test_unknown_rank_pauses_instead_of_clicking_blindly(tmp_path):
    module, _, war, runner = campaign(tmp_path)
    runner.read_rank = Mock(return_value=('不可识别', None))
    with patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='无法读取当前职级'):
        runner.choose_level()
    module.auto.click_element.assert_not_called()


def test_purple_four_is_not_mistaken_for_purple_one(tmp_path):
    module, _, war, runner = campaign(tmp_path)
    runner.read_rank = Mock(side_effect=[('A5紫金', 4), ('A5紫金', 1)])
    with patch.object(module.time, 'sleep'):
        assert runner.choose_level()
    assert module.auto.click_element.call_count == 1


def test_matching_bond_does_not_override_reset_board_blacklist(tmp_path):
    _, _, _, runner = campaign(tmp_path)
    runner.target = BondTarget('style', '追击', 7)
    runner.members = {'飞霄'}
    assert runner.choose_text_option([['大裁员', '追击'], ['追击邀请'], ['普通选项']]) == 1
