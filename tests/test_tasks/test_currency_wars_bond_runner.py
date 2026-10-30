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
    module.screen.check_screen.return_value = False
    module.auto.find_element.return_value = None
    with patch.dict(sys.modules, stubs), pytest.raises(RuntimeError, match='上次羁绊对局中断'):
        runner.run()
    runner.scan_rewards.assert_not_called()
    war.start_war.assert_not_called()


def test_completed_interrupted_target_reconciles_before_next_game(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    runner.progress.data.update(in_flight=True, target='style:追击', failures=1)
    module.screen.check_screen.return_value = True
    rows = [BondReward('style', '追击', 7, True, True)]
    runner.scan_rewards = Mock(return_value=(rows, 20, 20))
    with patch.dict(sys.modules, stubs):
        assert runner.run() is True
    assert runner.progress.data['in_flight'] is False
    assert runner.progress.data['failures'] == 0
    assert runner.progress.data['history'][-1]['verified'] is True
    war.start_war.assert_not_called()


def test_partial_reward_evidence_does_not_clear_interrupted_run(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    runner.progress.data.update(in_flight=True, target='style:追击', failures=1)
    module.screen.check_screen.return_value = True
    runner.scan_rewards = Mock(return_value=([
        BondReward('style', '追击', 5, False, True),
        BondReward('style', '追击', 7, True, False)], 20, 40))
    with patch.dict(sys.modules, stubs), pytest.raises(RuntimeError, match='尚未全部确认'):
        runner.run()
    assert runner.progress.data['in_flight'] is True
    assert runner.progress.data['failures'] == 1
    war.start_war.assert_not_called()


def prepare_resume(runner, module, rank='紫金1', stage='1-7', selected='标准博弈'):
    runner.progress.data.update(in_flight=True, target='style:能量', failures=2,
                                members=['角色' + str(index) for index in range(7)])
    module.screen.check_screen.return_value = True
    module.auto.click_element.return_value = True
    module.auto.find_element.return_value = True
    def line(text, x, y):
        return ([[x, y], [x + 100, y], [x + 100, y + 25], [x, y + 25]], (text, .99))
    runner.capture = Mock(return_value=(SimpleNamespace(width=1920, height=1080), [
        line(selected, 650, 150), line('对局难度' + rank, 1300, 870),
        line('当前进度' + stage, 1550, 870), line('结束并结算', 1300, 975),
        line('继续进度', 1580, 975)]))
    return [BondReward('style', '能量', 7, True, False)]


def test_resume_preserves_target_members_and_failures_until_verified_settlement(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    partial = prepare_resume(runner, module)
    complete = [BondReward('style', '能量', 7, True, True)]
    runner.scan_rewards = Mock(side_effect=[(partial, 0, 20), (complete, 20, 20), (complete, 20, 20)])
    def check_live_state():
        assert runner.target == BondTarget('style', '能量', 7)
        assert runner.members == set(runner.progress.data['members'])
        assert runner.progress.data['failures'] == 2
        assert runner.progress.data['in_flight'] is True
        assert runner.progress.data['history'] == []
    war.loop.side_effect = check_live_state
    war.result = True
    with patch.dict(sys.modules, stubs), patch.object(module.time, 'sleep'):
        assert runner.run() is True
    war.start_war.assert_not_called()
    war.loop.assert_called_once()
    assert runner.progress.data['failures'] == 0
    assert len(runner.progress.data['history']) == 1
    assert runner.progress.data['history'][0]['verified'] is True


@pytest.mark.parametrize('rank,stage,selected', [
    ('紫金2', '1-7', '标准博弈'), ('紫金11', '1-7', '标准博弈'),
    ('紫金1', '未知', '标准博弈'), ('紫金1', '1-7', '超频博弈')])
def test_resume_rejects_unverified_old_run_without_reset_or_abandon(tmp_path, rank, stage, selected):
    module, stubs, war, runner = campaign(tmp_path)
    partial = prepare_resume(runner, module, rank, stage, selected)
    runner.scan_rewards = Mock(return_value=(partial, 0, 20))
    with patch.dict(sys.modules, stubs), patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='未确认可接续'):
        runner.run()
    assert runner.progress.data['failures'] == 2
    assert runner.progress.data['in_flight'] is True
    assert runner.progress.data['history'] == []
    war.start_war.assert_not_called()
    war.loop.assert_not_called()
    assert [call.args[0] for call in module.auto.click_element.call_args_list] == ['标准博弈']


def test_resumed_run_still_stops_on_third_failed_attempt(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    partial = prepare_resume(runner, module)
    runner.scan_rewards = Mock(return_value=(partial, 0, 20))
    war.result = False
    with patch.dict(sys.modules, stubs), patch.object(module.time, 'sleep'):
        assert runner.run() is False
    assert runner.progress.data['failures'] == 3
    assert runner.progress.data['paused'] is True
    assert len(runner.progress.data['history']) == 1
    war.start_war.assert_not_called()


def test_unknown_resumed_result_keeps_in_flight_without_counting_failure(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    partial = prepare_resume(runner, module)
    runner.scan_rewards = Mock(return_value=(partial, 0, 20))
    war.result = None
    with patch.dict(sys.modules, stubs), patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='结果未确认'):
        runner.run()
    assert runner.progress.data['failures'] == 2
    assert runner.progress.data['in_flight'] is True
    assert runner.progress.data['history'] == []
    assert runner.scan_rewards.call_count == 1
    war.start_war.assert_not_called()


def test_resume_entry_timeout_preserves_unknown_run(tmp_path):
    module, stubs, war, runner = campaign(tmp_path)
    partial = prepare_resume(runner, module)
    runner.scan_rewards = Mock(return_value=(partial, 0, 20))
    module.auto.find_element.return_value = False
    with patch.dict(sys.modules, stubs), patch.object(module.time, 'sleep'), pytest.raises(RuntimeError, match='未确认进入原局'):
        runner.run()
    assert runner.progress.data['in_flight'] is True
    assert runner.progress.data['failures'] == 2
    war.loop.assert_not_called()
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


def test_missed_entry_click_retries_only_after_homepage_is_confirmed(tmp_path):
    module, _, _, runner = campaign(tmp_path)
    module.auto.find_element.side_effect = [False] + [False] * 6 + [True, True]
    module.screen.check_screen.return_value = True
    with patch.object(module.time, 'sleep'):
        runner.open_rewards()
    assert module.auto.click_element.call_count == 2
    assert module.auto.click_element.call_args.kwargs['press_duration'] == .10
    module.screen.check_screen.assert_called_once_with('currency_wars_homepage')
