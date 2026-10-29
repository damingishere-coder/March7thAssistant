from types import SimpleNamespace
import json

import pytest

from tasks.weekly.currency_wars_bonds import (
    BondProgress, BondReward, BondTarget, campaign_lock, next_target, parse_progress, plan_deployment,
    claimed_stamp, parse_reward_page,
)


def reward(name, count=7, claimed=False, category="style", purple=True):
    return BondReward(category, name, count, purple, claimed)


def test_style_first_and_skip_claimed():
    rows = [reward("夜之半神", category="faction"), reward("追击", claimed=True), reward("能量")]
    assert next_target(rows) == BondTarget("style", "能量", 7)


def test_target_stays_until_all_tiers_claimed():
    rows = [reward("追击"), reward("能量", 5, True, purple=False), reward("能量", 7)]
    assert next_target(rows, "style:能量").name == "能量"
    assert next_target([reward("能量", claimed=True)]) is None


def test_three_failures_are_durable_and_stop_fourth_run(tmp_path):
    path = tmp_path / "progress.json"
    progress = BondProgress(path)
    target = progress.observe([reward("追击")], 0, 20)
    for _ in range(3):
        progress.begin(target)
        progress.finish(target, False, False)
        progress = BondProgress(path)
    assert progress.data["paused"]
    with pytest.raises(RuntimeError, match="连续失败3局"):
        progress.begin(target)


def test_win_without_reward_evidence_is_still_failure(tmp_path):
    progress = BondProgress(tmp_path / "progress.json")
    target = progress.observe([reward("追击")], 0, 20)
    progress.begin(target)
    assert not progress.finish(target, True, False)
    assert progress.data["failures"] == 1


def test_unknown_result_preserves_in_flight_and_does_not_invent_loss(tmp_path):
    progress = BondProgress(tmp_path / "progress.json")
    target = progress.observe([reward("追击")], 0, 20)
    progress.begin(target)
    with pytest.raises(RuntimeError, match="结果未确认"):
        progress.finish(target, None, False)
    saved = BondProgress(progress.path).data
    assert saved["in_flight"] and saved["failures"] == 0


def test_progress_always_reconciles_with_game_and_keeps_partial_tiers(tmp_path):
    progress = BondProgress(tmp_path / "progress.json")
    progress.observe([reward("追击")], 0, 20)
    progress.data["failures"] = 2
    progress.observe([reward("追击", 5, True, purple=False), reward("追击", 7)], 20, 40)
    assert progress.data["failures"] == 2
    assert progress.data["target"] == "style:追击"
    progress.observe([reward("追击", claimed=True), reward("夜之半神", category="faction")], 20, 40)
    assert progress.data["target"] == "faction:夜之半神"
    assert progress.data["failures"] == 0


def test_corrupt_checkpoint_is_preserved(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="保留原文件"):
        BondProgress(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_parse_progress_accepts_split_label_but_not_unrelated_text():
    assert parse_progress(["已获取：", "860 / 1320"]) == (860, 1320)
    with pytest.raises(RuntimeError):
        parse_progress(["860/1321"])


def item(name, money=1, pos="forward", zone="prepare", level=1):
    return {"c": SimpleNamespace(name=name, money=money, pos=pos, level=level),
            "zone": zone, "idx": 0, "pos_ref": (0, 0, 1, 1)}


def test_seven_bond_members_cannot_be_displaced_by_expensive_fillers():
    members = {f"目标{number}" for number in range(7)}
    items = [item(name) for name in sorted(members)] + [item(f"补位{n}", money=10) for n in range(5)]
    _, assigned, used = plan_deployment(items, 4, 6, 9, members)
    deployed = assigned["forward"] + assigned["backward"]
    assert members <= {row["c"].name for row in deployed}
    assert used == 9 and len(assigned["forward"]) <= 4 and len(assigned["backward"]) <= 6


def test_duplicate_copies_do_not_count_twice_and_stronger_copy_is_used():
    weak, strong = item("目标", level=1), item("目标", level=2)
    _, assigned, used = plan_deployment([weak, strong, item("辅助", pos="backward")], 4, 6, 10, {"目标"})
    deployed = assigned["forward"] + assigned["backward"]
    assert used == 2
    assert strong in deployed and weak in assigned["prepare"]


def test_board_needs_at_least_one_front_unit_and_respects_population():
    _, assigned, used = plan_deployment([item(str(n), pos="backward") for n in range(9)], 4, 6, 3, set())
    assert assigned["forward"] and used == 3


def test_atomic_progress_file_is_valid_json_after_update(tmp_path):
    progress = BondProgress(tmp_path / "progress.json")
    progress.observe([reward("追击")], 0, 20)
    assert json.loads(progress.path.read_text(encoding="utf-8"))["target"] == "style:追击"


def test_campaign_cannot_run_twice_or_resume_while_running(tmp_path):
    path = tmp_path / "progress.json"
    with campaign_lock(path):
        with pytest.raises(RuntimeError, match="正在运行"):
            with campaign_lock(path):
                pytest.fail("Duplicate campaign entered")
    with campaign_lock(path):
        pass


@pytest.mark.parametrize('brightness', [250, 135])
def test_claimed_ring_and_check_are_recognized_when_card_is_dimmed(brightness):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 64), (35, 30, 50))
    draw = ImageDraw.Draw(image)
    color = (brightness,) * 3
    draw.ellipse((12, 12, 52, 52), outline=color, width=3)
    draw.line(((21, 31), (29, 39), (44, 23)), fill=color, width=5)
    assert claimed_stamp(image)
    gold = Image.new('RGB', (64, 64), (35, 30, 50))
    draw = ImageDraw.Draw(gold)
    draw.ellipse((12, 12, 52, 52), outline=(240, 180, 70), width=3)
    assert not claimed_stamp(gold)


def test_reward_amount_is_read_per_tier_and_two_purple_tiers_remain_distinct():
    from PIL import Image
    def line(text, x, y):
        return ([[x-20,y-10],[x+20,y-10],[x+20,y+10],[x-20,y+10]], (text,.99))
    ocr = [line('治疗',250,494), line('战技点',665,494),
           line('激活4治疗',140,575), line('标准博弈限定',250,640),
           line('激活4治疗',140,720), line('10',308,735),
           line('激活4战技点',560,575), line('标准博弈限定',665,640),
           line('激活4战技点',560,720), line('激活6战技点',560,800)]
    rows,_ = parse_reward_page(Image.new('RGB',(1920,1080)), ocr, 'style', lambda _: '20')
    assert len(rows) == 5
    assert next(row for row in rows if row.name=='治疗' and row.purple).amount == 10
    assert {(row.count,row.purple) for row in rows if row.name=='战技点'} == {(4,False),(4,True),(6,True)}
