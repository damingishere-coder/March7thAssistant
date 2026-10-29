"""羁绊链路的识别、目标规划和持久化；不依赖桌面输入或 GUI。"""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager


STRATEGY = "bond_chain"
STATE_PATH = Path("config/currencywars_bonds.json")


@contextmanager
def campaign_lock(path=STATE_PATH):
    """跨进程互斥：日志任务、循环任务及 GUI 继续操作不能同时修改进度。"""
    lock_path = Path(path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("羁绊任务正在运行，请先在日志页停止任务") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class BondReward:
    category: str
    name: str
    count: int
    purple: bool
    claimed: bool
    amount: int = 20

    @property
    def key(self):
        return f"{self.category}:{self.name}:{self.count}:{int(self.purple)}"


@dataclass(frozen=True)
class BondTarget:
    category: str
    name: str
    count: int

    @property
    def key(self):
        return f"{self.category}:{self.name}"


def next_target(rewards, preferred=None):
    """同一羁绊全部档位领取后才轮换，流派优先，保持画面中的排列顺序。"""
    groups = {}
    for reward in rewards:
        if not reward.claimed:
            key = f"{reward.category}:{reward.name}"
            groups.setdefault(key, []).append(reward)
    if not groups:
        return None
    key = preferred if preferred in groups else min(
        groups, key=lambda item: (0 if item.startswith("style:") else 1,
                                  list(groups).index(item)))
    rows = groups[key]
    return BondTarget(rows[0].category, rows[0].name, max(row.count for row in rows))


class BondProgress:
    def __init__(self, path=STATE_PATH):
        self.path = Path(path)
        self.data = {"version": 1, "target": None, "failures": 0,
                     "paused": False, "in_flight": False, "history": []}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if saved.get("version") != 1 or not isinstance(saved.get("history"), list):
                    raise ValueError("进度格式错误")
                self.data.update(saved)
            except (ValueError, TypeError) as error:
                raise RuntimeError("羁绊进度文件无法读取，已保留原文件，请检查后再运行") from error

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        fd, name = tempfile.mkstemp(prefix="bonds-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(self.data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def observe(self, rewards, received, total):
        # 每次以游戏当前画面重新核对，历史记录绝不替代游戏证据。
        self.data["received"] = received
        self.data["total"] = total
        self.data["rewards"] = [asdict(reward) for reward in rewards]
        target = next_target(rewards, self.data["target"])
        if target is None or target.key != self.data["target"]:
            self.data["target"] = target.key if target else None
            self.data["failures"] = 0
            self.data["paused"] = False
        self.save()
        return target

    def begin(self, target):
        if self.data["paused"]:
            raise RuntimeError("当前羁绊已连续失败3局；请在货币战争设置中点击“继续当前羁绊”")
        self.data.update(target=target.key, in_flight=True)
        self.save()

    def finish(self, target, cleared, verified):
        if cleared is None:
            # UNKNOWN 不计作失败，也不能开启新一局。
            self.save()
            raise RuntimeError("上局结果未确认，保留当前目标与进度，停止自动重开")
        self.data["in_flight"] = False
        success = cleared is True and verified is True
        self.data["failures"] = 0 if success else self.data["failures"] + 1
        self.data["paused"] = self.data["failures"] >= 3
        self.data["history"].append({"target": target.key, "cleared": cleared,
                                     "verified": verified, "time": time.time()})
        self.data["history"] = self.data["history"][-200:]
        self.save()
        return success


def plan_deployment(items, forward_slots, backward_slots, limit, members):
    """按不同角色计数，先最大化目标羁绊人数，再考虑星级、站位和战力。"""
    unique = {}
    for item in items:
        character = item["c"]
        previous = unique.get(character.name)
        priority = (character.level, character.money, item["zone"] != "prepare")
        if previous is None or priority > previous[0]:
            unique[character.name] = (priority, item)
    # DP 保留每种前后台人数的最优组合，允许前台角色在后台补羁绊。
    states = {(0, 0): ((0, 0, 0), {"forward": [], "backward": []})}
    for _, item in unique.values():
        character = item["c"]
        updated = dict(states)
        for (front, back), (score, assigned) in states.items():
            if front + back >= limit:
                continue
            for zone, count, capacity in (("forward", front, forward_slots),
                                           ("backward", back, backward_slots)):
                if count >= capacity:
                    continue
                key = (front + (zone == "forward"), back + (zone == "backward"))
                native = character.pos in (zone, "all")
                new_score = (score[0] + (character.name in members),
                             score[1] + (character.money + character.level * 2) * (3 if native else 1),
                             score[2] + 1)
                if key not in updated or new_score > updated[key][0]:
                    new_assignment = {name: list(values) for name, values in assigned.items()}
                    new_assignment[zone].append(item)
                    updated[key] = (new_score, new_assignment)
        states = updated
    viable = [(key, value) for key, value in states.items() if key[0] > 0]
    if not viable:
        raise RuntimeError("没有可部署的前台角色，停止当前羁绊对局")
    (front, back), (_, assigned) = max(viable, key=lambda pair: (pair[1][0], pair[0][0]))
    selected = {id(item) for values in assigned.values() for item in values}
    assigned["prepare"] = [item for item in items if id(item) not in selected]
    return items, assigned, front + back


def parse_progress(texts):
    for text in texts:
        match = re.search(r"(?:已获取[:：]?)?(\d{2,})/(\d{2,})", compact(text))
        if match:
            received, total = map(int, match.groups())
            if 0 <= received <= total and total % 20 == 0:
                return received, total
    raise RuntimeError("未确认羁绊链路总奖励进度，停止自动运行")


def claimed_stamp(image):
    """识别已领取图标上的白色圆环和勾，不能用“图标变灰”代替。"""
    import cv2
    import numpy as np
    pixels = np.asarray(image.convert("RGB"))
    if pixels.size == 0:
        return False
    pixels = cv2.resize(pixels, (64, 64))
    # 已完成卡片会整体压暗，白色勾号仍是本图标内的中性色高光。
    low, high = pixels.min(axis=2), pixels.max(axis=2)
    threshold = max(90, float(np.quantile(low, .99)) * .80)
    white = (low > threshold) & ((high.astype(int) - low) < 35)
    circles = cv2.HoughCircles(white.astype("uint8") * 255, cv2.HOUGH_GRADIENT,
                               1, 18, param1=80, param2=12, minRadius=12, maxRadius=28)
    if circles is not None:
        angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        yy, xx = np.ogrid[:64, :64]
        for x, y, radius in circles[0]:
            # 圆周须覆盖绝大多数角度，且圆内还要有勾；水晶高光不能满足这两个条件。
            coverage = np.zeros(72, dtype=bool)
            for offset in (-2, -1, 0, 1, 2):
                xs = np.clip(np.rint(x + (radius + offset) * np.cos(angles)).astype(int), 0, 63)
                ys = np.clip(np.rint(y + (radius + offset) * np.sin(angles)).astype(int), 0, 63)
                coverage |= white[ys, xs]
            inner = (xx - x) ** 2 + (yy - y) ** 2 < (radius * .65) ** 2
            if coverage.mean() > .75 and white[inner].mean() > .10:
                return True
    return False


def parse_reward_page(image, ocr_result, category, recognize_amount=None):
    """从完整截图解析卡片，坐标随窗口尺寸缩放；返回奖励及点击位置。"""
    width, height = image.size
    lines = []
    for box, (text, confidence) in ocr_result:
        if confidence < 0.6:
            continue
        xs, ys = [point[0] for point in box], [point[1] for point in box]
        lines.append((compact(text), (min(xs) + max(xs)) / 2,
                      (min(ys) + max(ys)) / 2))
    task_pattern = re.compile(r"(?:激活|活)(\d+)([\u4e00-\u9fff·]+)")
    task_names = {match.group(2) for text, _, _ in lines
                  if (match := task_pattern.search(text))}
    headers = [(text, x, y) for text, x, y in lines
               if text in task_names and 0.39 * height < y < 0.56 * height]
    headers.sort(key=lambda item: item[1])
    if not headers:
        raise RuntimeError("未识别到完整羁绊卡片，保存截图后暂停")
    spacing = min((headers[index + 1][1] - headers[index][1]
                   for index in range(len(headers) - 1)), default=width * 0.217)
    rewards, clicks = [], {}
    for name, center, _ in headers:
        left, right = center - spacing / 2, center + spacing / 2
        if left < 0 or right > width:
            continue  # 屏幕两侧的半张卡片不参与识别或点击。
        column = [(text, x, y) for text, x, y in lines if left <= x <= right]
        boundary = min((y for text, _, y in column if "标准博弈限定" in text), default=height * 0.70)
        for text, x, y in sorted(column, key=lambda line: line[2]):
            match = task_pattern.search(text)
            if not match or match.group(2) != name or y < height * 0.50:
                continue
            # 第三行说明可能换行，用任务文字末行定位奖励行。
            icon_x = left + spacing * 0.64
            icon_y = y - (height * 0.008 if y > boundary else 0)
            half = spacing * 0.075
            first = image.crop((icon_x - half, icon_y - half, icon_x + half, icon_y + half))
            second_x = left + spacing * 0.82
            second = image.crop((second_x - half, icon_y - half, second_x + half, icon_y + half))
            # 治疗、护盾等档位可能只有10星琼，不能把每档硬算为20。
            amount = 0
            for value, value_x, value_y in column:
                if value in {"10", "20"} and abs(value_x - icon_x) < spacing * .07 and 0 < value_y - icon_y < height * .045:
                    amount = int(value)
            if not amount and recognize_amount is not None:
                # 勾号紧邻数字，使用几种紧裁剪避免勾号被当作数字的一部分。
                for top_offset in (.012, .016, .008):
                    amount_image = image.crop((icon_x - spacing * .07, icon_y + height * top_offset,
                                               icon_x + spacing * .07, icon_y + height * .045))
                    value = compact(recognize_amount(amount_image) or "")
                    if value in {"10", "20"}:
                        amount = int(value)
                        break
            reward = BondReward(category, name, int(match.group(1)), y > boundary,
                                claimed_stamp(first) and claimed_stamp(second), amount)
            rewards.append(reward)
            clicks[reward.key] = (icon_x / width, icon_y / height)
    return rewards, clicks
