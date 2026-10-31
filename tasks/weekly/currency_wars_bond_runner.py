"""货币战争羁绊补全：游戏画面 -> 单局目标 -> 通关 -> 奖励画面复核。"""
import re
import time
from dataclasses import replace

from module.automation import auto
from module.config import cfg
from module.logger import log
from module.screen import screen
from tasks.weekly.currency_wars_bonds import (
    BondProgress, BondTarget, STRATEGY, campaign_lock, compact, parse_progress, parse_reward_page,
    choose_bond_purchase,
)


def enabled():
    return cfg.get_value("currencywars_strategy", "default") == STRATEGY


class BondCampaign:
    def __init__(self, war):
        self.war = war
        self.progress = BondProgress()
        self.target = None
        self.members = set()
        self.member_costs = {}
        self.all_rewards = []

    @staticmethod
    def click(x, y):
        return auto.click_element((x - .002, y - .002, .004, .004), "crop", press_duration=.10)

    @staticmethod
    def capture():
        auto.take_screenshot()
        if auto.screenshot is None:
            raise RuntimeError("无法读取游戏画面，暂停羁绊任务")
        auto.perform_ocr()
        return auto.screenshot.copy(), list(auto.ocr_result)

    @staticmethod
    def texts(result):
        return [compact(text) for _, (text, confidence) in result if confidence >= .6]

    @staticmethod
    def track(image):
        import cv2
        import numpy as np
        width, height = image.size
        top = int(height * .82)
        pixels = np.asarray(image.convert("RGB"))[top:int(height * .96)]
        edges = cv2.Canny(cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY), 15, 45)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), dtype="uint8"))
        candidates = []
        for contour in cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]:
            x, y, w, h = cv2.boundingRect(contour)
            if .16 * width < w < .28 * width and .006 * height < h < .04 * height:
                if .40 * width < x + w / 2 < .60 * width and w > h * 10:
                    candidates.append((x, y + top, w, h))
        if not candidates:
            raise RuntimeError("未确认羁绊列表滚动条，停止自动翻页")
        x, y, w, h = max(candidates, key=lambda item: item[2])
        return x / width, (y + h / 2) / height, w / width

    def open_rewards(self):
        if auto.find_element(("羁绊链路", "羁绊图鉴"), "text", crop=(0, 0, .22, .13)):
            self.return_home()
        screen.change_to("currency_wars_homepage")
        for _ in range(3):
            if not auto.click_element("羁绊链路", "text", include=True, max_retries=3, press_duration=.10):
                raise RuntimeError("未找到“羁绊链路”入口，请先确认活动页面可用")
            for _ in range(6):
                time.sleep(.5)
                if auto.find_element("流派羁绊", "text") and auto.find_element("阵营羁绊", "text"):
                    return
            # 点击成功只表示输入已发送。仍在首页才重新定位入口再试，不能在未知页面盲点。
            if not screen.check_screen("currency_wars_homepage"):
                break
        raise RuntimeError("未确认进入羁绊链路，停止自动运行")

    @staticmethod
    def return_home():
        # 页面切换有动画，每次确认后再按一次返回，不能连续盲按 ESC。
        for _ in range(6):
            if screen.check_screen("currency_wars_homepage"):
                return
            auto.press_key("esc")
            time.sleep(.8)
        raise RuntimeError("未确认返回货币战争首页，暂停任务")

    @staticmethod
    def scroll_cards(direction, x=.50):
        # 实机确认：下方横条是位置指示，卡片区域的纵向滚轮才会横向翻页。
        auto.click_element((x, .45, .004, .004), "crop", action="move")
        auto.mouse_scroll(30, direction=direction, pause=False)
        time.sleep(.7)

    def close_reward_preview(self, label):
        # 未达成的奖励不一定弹出物品详情；Esc 会把整个羁绊页面关闭。
        # 点击卡片上方的空白处关闭详情，没有弹窗时也不会离开奖励页。
        self.click(.50, .22)
        for _ in range(6):
            if auto.find_element(label, "text"):
                return
            time.sleep(.3)
        raise RuntimeError("领取奖励后未确认返回羁绊页面，暂停并保留进度")

    def scan_rewards(self, claim=True):
        from module.ocr import ocr
        import numpy as np
        def recognize_amount(crop):
            value = ocr.recognize_single_line(np.asarray(crop.resize((crop.width * 4, crop.height * 4))))
            return value[0] if value else ""
        def parse(image, result, category):
            return parse_reward_page(image, result, category, recognize_amount)
        self.open_rewards()
        merged = {}
        received = total = None
        for category, label in (("style", "流派羁绊"), ("faction", "阵营羁绊")):
            if not auto.click_element(label, "text"):
                raise RuntimeError(f"未找到{label}页签")
            time.sleep(.5)
            previous = None
            for _ in range(12):
                image, result = self.capture()
                rows, _ = parse_reward_page(image, result, category)
                signature = tuple(row.key for row in rows)
                if signature == previous:
                    break
                previous = signature
                self.scroll_cards(1)
            previous = None
            for step in range(16):
                image, result = self.capture()
                received, total = parse_progress(self.texts(result))
                rows, positions = parse(image, result, category)
                if claim:
                    for row in rows:
                        if row.claimed:
                            continue
                        # 每次领取后重新定位；未达成任务只会打开说明，不记作完成。
                        current_image, current_result = self.capture()
                        current_rows, current_positions = parse(current_image, current_result, category)
                        current = next((item for item in current_rows if item.key == row.key), None)
                        if current is None or current.claimed:
                            continue
                        self.click(*current_positions[row.key])
                        time.sleep(.5)
                        self.close_reward_preview(label)
                    image, result = self.capture()
                    received, total = parse_progress(self.texts(result))
                    rows, positions = parse(image, result, category)
                for row in rows:
                    existing = merged.get(row.key)
                    if existing is not None:
                        if existing.amount and row.amount and existing.amount != row.amount:
                            raise RuntimeError(f"{row.name}奖励金额跨页识别不一致，暂停核对")
                        row = replace(row, claimed=row.claimed or existing.claimed,
                                      amount=row.amount or existing.amount)
                    merged[row.key] = row
                signature = tuple(row.key for row in rows)
                # 页面边界连续两次相同才停止，跨页重复项目按唯一键合并。
                if signature == previous:
                    break
                previous = signature
                self.scroll_cards(-1)
            log.info(f"{label}扫描完成，累计识别{len(merged)}档奖励")
        if claim:
            # 领取/查看物品会留下选中高光；退出后重新只读扫描，不能把交互中的
            # 临时图标状态或跨页旧帧当作最终领奖证据。
            self.return_home()
            return self.scan_rewards(claim=False)
        rewards = list(merged.values())
        self.last_scan = {"rewards": rewards, "received": received, "total": total}
        if not rewards or any(not row.amount for row in rewards) or sum(row.amount for row in rewards) != total:
            raise RuntimeError(f"羁绊卡片未扫描完整（识别{len(rewards)}档，总奖励{total}），暂停运行")
        observed = sum(row.amount for row in rewards if row.claimed)
        if observed != received:
            raise RuntimeError(f"奖励勾选状态与总进度不一致（勾选{observed}，界面{received}），暂停运行，避免跳过未完成目标")
        self.return_home()
        return rewards, received, total

    def load_members(self):
        self.open_rewards()
        if not auto.click_element("羁绊图鉴", "text", include=True):
            raise RuntimeError("未找到羁绊图鉴，无法确认目标角色")
        time.sleep(1)
        label = "流派羁绊" if self.target.category == "style" else "阵营羁绊"
        auto.click_element(label, "text")
        time.sleep(.5)
        def is_target_detail():
            header = {compact(text) for text in self.war._get_ocr_texts_in_crop((.24, .08, .72, .20))}
            return self.target.name in header and bool(auto.find_element("简略描述", "text", crop=(.75, .08, .24, .20)))
        selected = is_target_detail()
        detail = bool(auto.find_element("简略描述", "text", crop=(.75, .08, .24, .20)))
        previous = None
        for _ in range(12):
            if selected:
                break
            image, result = self.capture()
            signature = tuple(compact(text) for box, (text, confidence) in result
                              if confidence >= .7 and image.height * .55 < box[0][1] < image.height * .80)
            if signature == previous:
                break
            previous = signature
            self.scroll_cards(1, x=.12 if detail else .50)
        # 图鉴先展示横向卡片；第一次点击居中，第二次点击才进入详情。
        for page in range(16):
            # 切入详情的动画可能比一次 OCR 更慢，下一轮先检查页面状态。
            if is_target_detail():
                selected = True
            detail = bool(auto.find_element("简略描述", "text", crop=(.75, .08, .24, .20)))
            if selected:
                break
            for _ in range(2):
                crop = (.02, .17, .20, .76) if detail else (.02, .23, .96, .56)
                if auto.click_element(self.target.name, "text", crop=crop):
                    time.sleep(1.2)
                    if is_target_detail():
                        selected = True
                        break
            if selected:
                break
            self.scroll_cards(-1, x=.12 if detail else .50)
        if not selected:
            raise RuntimeError(f"图鉴中未找到{self.target.name}，停止自动选人")
        time.sleep(.5)
        # 正文必须再次显示目标名称，防止仅点到列表同名文字。
        detail_texts = self.war._get_ocr_texts_in_crop((.24, .08, .72, .20))
        if self.target.name not in {compact(text) for text in detail_texts}:
            raise RuntimeError(f"图鉴详情未确认选中{self.target.name}，暂停运行")
        marker = None
        for _ in range(7):
            image, result = self.capture()
            marker = next((box for box, (text, confidence) in result
                           if compact(text) == "羁绊内队员" and confidence >= .7), None)
            if marker is not None and max(point[1] for point in marker) < image.height * .90:
                break
            auto.drag_mouse((.90, .78), (.90, .38), .5)
            time.sleep(.4)
        if marker is None:
            raise RuntimeError("未找到羁绊内队员头像栏，暂停角色识别")
        self.members = set()
        self.member_costs = {}
        def read_row(marker, image):
            start_x = min(point[0] for point in marker) / image.width + .023
            center_y = max(point[1] for point in marker) / image.height + .060
            if center_y >= .96:
                raise RuntimeError("角色头像栏尚未完整显示，暂停角色识别")
            for index in range(14):
                center_x = start_x + index * .0505
                if center_x >= .96:
                    break
                self.click(center_x, center_y)
                time.sleep(.7)
                popup = [compact(text) for text in self.war._get_ocr_texts_in_crop((.70, .23, .29, .24))]
                is_member = any(self.target.name in text for text in popup)
                is_role = any("前台" in text or "后台" in text for text in popup)
                # 姓名与标签必须来自同一弹窗 OCR，紧裁单行会把“不死途”等字认错。
                # 弹窗首行可能是费用；双定位角色的布局与单定位角色不同。
                name = next((text for text in popup[:2] if not text.isdigit()), None)
                if name and is_member and is_role:
                    name = compact(name)
                    if re.fullmatch(r"[\w\u4e00-\u9fff·&\-]{1,24}", name) and name not in {"前台", "后台", "前后台", "输出", "辅助", "治疗", "护盾", self.target.name}:
                        self.members.add(name)
                        cost = next((int(text) for text in popup[:2] if text in {"1", "2", "3", "4", "5"}), None)
                        if cost is not None:
                            self.member_costs[name] = cost
        read_row(marker, image)
        # 部分羁绊还包含可通过专家邀请获得的角色，人数不足时继续读取顾问栏。
        if len(self.members) < self.target.count:
            self.click(.40, .16)
            for _ in range(7):
                self.scroll_cards(-1, x=.85)
                image, result = self.capture()
                adviser = next((box for box, (text, confidence) in result
                                if compact(text) == "专家顾问" and confidence >= .7), None)
                if adviser is not None and max(point[1] for point in adviser) < image.height * .88:
                    read_row(adviser, image)
                    break
        self.click(.40, .16)
        if len(self.members) < self.target.count:
            raise RuntimeError(f"{self.target.name}需{self.target.count}名不同角色，图鉴仅确认{len(self.members)}名；停止并保留现场")
        log.info(f"当前目标：{self.target.name}（{self.target.count}人），已从图鉴确认角色：{', '.join(sorted(self.members))}")
        self.progress.data["members"] = sorted(self.members)
        self.progress.data["member_costs"] = self.member_costs
        self.progress.save()
        self.return_home()

    def read_rank(self):
        from module.ocr import ocr
        import numpy as np
        heading = compact("".join(self.war._get_ocr_texts_in_crop((.035, .27, .20, .06))))
        # 大号数字叠在职级纹章上；提取白色笔画后再 OCR，避免把纹章识别成“19”。
        auto.take_screenshot((200 / 1920, 325 / 1080, 155 / 1920, 175 / 1080))
        pixels = np.asarray(auto.screenshot.convert("RGB"))
        binary = np.where(pixels.min(axis=2) > 210, 0, 255).astype("uint8")
        result = ocr.recognize_single_line(np.repeat(binary[:, :, None], 3, axis=2))
        number = compact(result[0]) if result and result[1] >= .8 else ""
        return heading, int(number) if number in {"1", "2", "3", "4", "5"} else None

    def choose_level(self):
        """逐次读取当前职级，确认“紫金1”后才允许开始对局。"""
        time.sleep(2)
        down = (936 / 1920, 830 / 1080, 45 / 1920, 31 / 1080)
        up = (936 / 1920, 126 / 1080, 45 / 1920, 42 / 1080)
        previous, unchanged = None, 0
        for _ in range(60):
            heading, number = self.read_rank()
            if "紫金" in heading and number == 1:
                log.info("已确认标准博弈紫金1")
                return True
            match = re.search(r"A(\d+)", heading)
            tier = 5 if "紫金" in heading else int(match.group(1)) if match else None
            if tier is None or number is None:
                raise RuntimeError("无法读取当前职级，停止选择紫金1")
            # 游戏按 A1、A2…分段，A5 是紫金；界面使用上下箭头。
            button = up if tier < 5 else down
            current = (heading, number)
            unchanged = unchanged + 1 if current == previous else 0
            if unchanged >= 3:
                raise RuntimeError("职级选择未发生变化，停止重复点击，请确认已解锁紫金1")
            previous = current
            auto.click_element(button, "crop", press_duration=.10)
            time.sleep(.5)
        raise RuntimeError("未能确认紫金1，停止开始对局")

    def prepare_board(self):
        """免费商店补缺 -> 投入人口 -> 达到人口后刷缺失角色。"""
        war = self.war
        if not war.get_level():
            raise RuntimeError("未确认小队等级，停止花费羁绊预算")
        self.sell_surplus(only_when_full=True)
        self.buy_bond_characters(reroll=False)
        before = war.current_level
        if not self.late_search():
            self.invest_experience()
        if war.current_level >= self.population_target or self.late_search() or war.current_level != before:
            self.buy_bond_characters(reroll=war.current_level >= self.population_target or self.late_search())
        owned = {character.name for character in self.characters() if character.name}
        log.info(f"羁绊收集：{self.target.name}，已拥有{len(self.members & owned)}/{self.target.count}名；"
                 f"Lv{war.current_level}；缺少：{', '.join(sorted(self.members - owned)) or '无'}")

    def characters(self):
        return [character for zone in ("forward", "backward", "prepare")
                for character in getattr(self.war, f"{zone}_characters", []) if character.name]

    def costs(self):
        # 图鉴偶尔漏读费用时按5币预留，不能把未知费用当成1币。
        known = dict(getattr(self.war, "character_purchase_costs", {}))
        known.update(self.member_costs)
        for character in self.characters():
            if 1 <= character.money <= 5:
                known[character.name] = character.money
        return {**known, **{name: known.get(name, 5) for name in self.members}}

    def late_search(self):
        # 尾段已满足上阵人数时保留搜最后一名的机会，避免把最后的钱全投向下一级。
        match = re.fullmatch(r"3-([4-9])", self.war.current_stage)
        return bool(match and self.war.current_level >= max(8, self.target.count))

    def invest_experience(self):
        war = self.war
        for _ in range(10):
            money = self.read_money()
            reserve = max((self.costs()[name] for name in self.members), default=5)
            affordable = max(0, (money - reserve) // 4)
            if not affordable:
                return
            if not war.get_level():
                raise RuntimeError("升级时未确认小队等级，停止花费")
            if war.current_level >= self.population_target:
                return
            value = compact(auto.get_single_line_text((235 / 1920, 930 / 1080, 124 / 1920, 38 / 1080)) or "")
            match = re.fullmatch(r"(\d+)/(\d+)", value)
            if match is None or not 0 <= int(match[1]) < int(match[2]):
                raise RuntimeError("未确认升级经验，保留金币并停止任务")
            times = min(affordable, (int(match[2]) - int(match[1]) + 3) // 4)
            log.info(f"羁绊人口预算：Lv{war.current_level}，金币{money}，预留{reserve}，投入{times * 4}币购买经验")
            for _ in range(times):
                auto.press_key("f")
                time.sleep(.3)
            time.sleep(.8)
            if self.read_money() >= money:
                raise RuntimeError("购买经验后金币未减少，停止重复输入")
        raise RuntimeError("人口预算连续升级未结束，停止重复输入")

    @staticmethod
    def read_money():
        value = compact(auto.get_single_line_text((1559 / 1920, 880 / 1080, 127 / 1920, 82 / 1080),
                                                 blacklist=['V', '?']) or "")
        if not value.isdigit():
            raise RuntimeError("未确认金币余额，停止花费并保留现场")
        money = int(value)
        log.info(f"羁绊预算余额：{money}")
        return money

    def buy_bond_characters(self, reroll=False):
        war = self.war
        shop_button = (1591 / 1920, 958 / 1080, 66 / 1920, 46 / 1080)
        shop_crop = (344 / 1920, 19 / 1080, 1370 / 1920, 526 / 1080)
        fold_crop = (1593 / 1920, 959 / 1080, 100 / 1920, 43 / 1080)

        def open_shop():
            auto.click_element(shop_button, "crop", press_duration=.10)
            time.sleep(.5)
            if auto.find_element("收起", "text", crop=fold_crop, include=True):
                return True
            if auto.find_element("该状态下不可使用", "text", include=True):
                log.info("当前节点商店不可用，保留预算")
                return False
            raise RuntimeError("未确认商店已打开，保留金币并停止任务")

        def close_shop():
            if not auto.click_element("收起", "text", crop=fold_crop, include=True, press_duration=.10):
                raise RuntimeError("未确认商店关闭按钮，停止任务")
            time.sleep(.4)

        if not open_shop():
            return
        opened = True
        unavailable = set()
        purchases = 0
        try:
            for _ in range(20):
                money = self.read_money()
                if money < 1:
                    break
                characters = self.characters()
                owned = {character.name for character in characters}
                missing = self.members - owned
                texts = war._get_ocr_texts_in_crop(shop_crop)
                visible = {compact(text) for text in texts} - unavailable
                name = choose_bond_purchase(visible, characters, self.members, money, self.costs(),
                                           war.current_level, allow_upgrades=reroll)
                if name and purchases < 6:
                    if auto.click_element(name, "text", take_screenshot=False, need_ocr=False, press_duration=.10):
                        time.sleep(.8)
                        balance = self.read_money()
                        if balance < money:
                            log.info(f"羁绊购买：{name}，{'补缺' if name in missing else '合成/补位'}，实际花费{money - balance}")
                            close_shop()
                            opened = False
                            war.check_character_status()
                            purchases += 1
                            if not open_shop():
                                return
                            opened = True
                        else:
                            unavailable.add(name)
                        continue
                    unavailable.add(name)
                    continue
                # 人口未达标时不刷商店；达到后只为缺失成员刷新并留足最高5费购卡钱。
                reserve = max((self.costs()[member] for member in missing), default=5)
                if not reroll or not missing or purchases >= 6 or money < reserve + 2:
                    break
                auto.press_key("d")
                unavailable.clear()
                time.sleep(.6)
                if self.read_money() >= money:
                    log.warning("商店刷新后余额未减少，停止本轮刷新")
                    break
        finally:
            if opened:
                close_shop()

    @property
    def population_target(self):
        rare_member = any(self.costs().get(name, 5) >= 5 for name in self.members)
        return min(10, max(8, self.target.count + (2 if rare_member else 1)))

    def choose_invitation(self, crop):
        visible = {compact(text) for text in self.war._get_ocr_texts_in_crop(crop)}
        owned = {character.name for character in self.characters()}
        candidates = visible & self.members
        if not candidates:
            return False
        name = min(candidates, key=lambda member: (member in owned, -self.costs()[member], member))
        log.info(f"羁绊邀请：优先选择{name}（{'缺失角色' if name not in owned else '已有角色'}）")
        return bool(auto.click_element(name, "text", crop=crop, press_duration=.10))

    def choose_option(self, crops):
        """用于投资环境、补给：目标羁绊及其成员优先。"""
        return self.choose_text_option([self.war._get_ocr_texts_in_crop(crop) for crop in crops])

    def choose_text_option(self, texts_by_option):
        options = []
        owned = {character.name for character in self.characters()}
        for index, texts in enumerate(texts_by_option):
            normalized = {compact(text) for text in texts}
            if any(bad in text for text in normalized for bad in
                   ("现金为王", "降本增效", "大裁员", "人力重组", "全员晋升", "节省工位")):
                continue
            score = sum((100 if name not in owned else 10)
                        for name in self.members if any(name in text for text in normalized))
            score += 20 * sum(self.target.name in text for text in normalized)
            if score:
                options.append((score, index))
        if options:
            return max(options)[1]
        return None

    def sell_surplus(self, only_when_full=False):
        war = self.war
        if only_when_full and sum(bool(char.name) for char in war.prepare_characters) < 7:
            return
        for index, character in enumerate(war.prepare_characters):
            if character.name and character.name not in self.members:
                if war._sell_single_character(index):
                    from tasks.weekly.currency_wars import CurrencyWarsCharacter
                    war.prepare_characters[index] = CurrencyWarsCharacter(None, None)

    def run(self):
        with campaign_lock(self.progress.path):
            return self._run()

    def reconcile_completed_run(self):
        """先复核实际奖励；未完成时仅接续有保存名单且可核验的紫金1旧局。"""
        if not screen.check_screen("currency_wars_homepage"):
            if auto.find_element(("羁绊链路", "羁绊图鉴"), "text", crop=(0, 0, .22, .13)):
                self.return_home()
            elif screen.check_screen("main"):
                screen.change_to("currency_wars_homepage")
            elif screen.check_screen("currency_wars_mode_select"):
                self.return_home()
            else:
                raise RuntimeError("上次羁绊对局中断；请完成结算并返回货币战争首页后重试，保留原局与进度")
        rewards, received, total = self.scan_rewards()
        key = self.progress.data["target"]
        rows = [row for row in rewards if f"{row.category}:{row.name}" == key]
        # 紫金档领奖要求成功通关；全部档位的领取标记与总金额均已由扫描独立复核。
        if not rows or not all(row.claimed for row in rows) or not any(row.purple for row in rows):
            return self.resume_interrupted_run(rewards)
        target = BondTarget(rows[0].category, rows[0].name, max(row.count for row in rows))
        self.progress.finish(target, True, True)
        self.progress.observe(rewards, received, total)
        log.info(f"已复核中断目标{target.name}全部奖励领取，恢复羁绊任务：{received}/{total}")
        return True

    def resume_interrupted_run(self, rewards):
        """核对保存的目标名单与游戏中的紫金1旧局，接续时不重置或计败。"""
        key = self.progress.data["target"]
        rows = [row for row in rewards if f"{row.category}:{row.name}" == key]
        saved_members = self.progress.data.get("members", [])
        if (not rows or not isinstance(saved_members, list)
                or not all(isinstance(name, str) and name for name in saved_members)
                or len(set(saved_members)) < max(row.count for row in rows)):
            raise RuntimeError("上次目标奖励尚未全部确认领取，且缺少原局角色名单；保留中断记录，停止自动重开")
        screen.change_to("currency_wars_mode_select")
        if not auto.click_element("标准博弈", "text", crop=(.02, .10, .26, .50), press_duration=.10):
            raise RuntimeError("未确认原局标准博弈入口，保留中断记录")
        time.sleep(1)
        image, result = self.capture()
        texts = self.texts(result)
        normal_selected = any(compact(text) == "标准博弈" for box, (text, confidence) in result
                              if confidence >= .7 and box[0][0] >= image.width * .30
                              and box[0][1] < image.height * .20)
        footer = "".join(compact(text) for box, (text, confidence) in result
                         if confidence >= .7 and box[0][0] >= image.width * .50
                         and image.height * .76 < box[0][1] < image.height * .87)
        stage = re.search(r"当前进度([1-3]-\d{1,2})(?!\d)", footer)
        if (not normal_selected or "继续进度" not in texts or "结束并结算" not in texts
                or not re.search(r"紫金1(?!\d)", footer) or stage is None):
            raise RuntimeError("未确认可接续的标准博弈紫金1旧局，保留中断记录，不放弃或重开")
        self.target = BondTarget(rows[0].category, rows[0].name, max(row.count for row in rows))
        self.members = set(saved_members)
        self.member_costs = dict(self.progress.data.get("member_costs", {}))
        self.all_rewards = rewards
        self.war.bond_campaign = self
        cfg.set_values({"currencywars_type": "normal", "currencywars_rank_difficulty": "purple1",
                        "currencywars_fast_mode": False})
        log.info(f"已确认原局紫金1、进度{stage[1]}，接续{self.target.name}；保留阵容与连续失败次数")
        if not auto.click_element("继续进度", "text", crop=(.77, .87, .22, .10), press_duration=.10):
            raise RuntimeError("未能点击原局继续进度，保留中断记录")
        map_dismissed = False
        for _ in range(20):
            if auto.find_element("./assets/images/screen/currency_wars/exit.png", "image", .9,
                                 crop=(3 / 1920, 37 / 1080, 104 / 1920, 57 / 1080)):
                self.war.loop()
                return self.finish_run()
            if not map_dismissed:
                _, result = self.capture()
                texts = self.texts(result)
                if "点击空白处继续" in texts and any("位面" in text for text in texts):
                    log.info("接续原局时检测到位面地图，点击空白处继续")
                    self.click(.50, .89)
                    map_dismissed = True
            time.sleep(2)
        raise RuntimeError("接续后未确认进入原局，保留中断记录，不重复点击或重开")

    def finish_run(self):
        """新局和接续局共用结算，只有实际结果与领奖证据可更新计数。"""
        from tasks.base.base import Base
        from module.notification.notification import NotificationLevel
        cleared = self.war.result
        if cleared is None:
            self.progress.finish(self.target, None, False)
        after, new_received, new_total = self.scan_rewards()
        target_rows = [row for row in after if row.name == self.target.name and row.category == self.target.category]
        verified = bool(target_rows) and all(row.claimed for row in target_rows)
        self.progress.finish(self.target, cleared, verified)
        if self.progress.data["paused"]:
            message = f"{self.target.name}连续3局未完成目标，羁绊任务已暂停"
            log.warning(message)
            Base.send_notification_with_screenshot(message, NotificationLevel.ERROR)
            return False
        log.info(f"羁绊进度：{new_received}/{new_total}；当前目标连续未达标{self.progress.data['failures']}局")
        return True

    def _run(self):
        from tasks.base.base import Base
        from module.notification.notification import NotificationLevel
        if self.progress.data["paused"]:
            raise RuntimeError("当前羁绊连续失败3局，已暂停；点击设置中的“继续当前羁绊”后重试")
        if self.progress.data["in_flight"]:
            if self.reconcile_completed_run() is False:
                return False
        while True:
            rewards, received, total = self.scan_rewards()
            self.all_rewards = rewards
            self.target = self.progress.observe(rewards, received, total)
            if self.target is None:
                if received != total:
                    raise RuntimeError("卡片状态与奖励总进度不一致，停止运行")
                log.info(f"羁绊链路奖励已全部领取：{received}/{total}")
                Base.send_notification_with_screenshot("羁绊链路奖励已全部领取", NotificationLevel.ALL)
                return True
            self.load_members()
            cfg.set_values({"currencywars_type": "normal", "currencywars_rank_difficulty": "purple1",
                            "currencywars_fast_mode": False})
            self.war.bond_campaign = self
            self.progress.begin(self.target)
            if not self.war.start_war("normal"):
                raise RuntimeError("未确认开始羁绊对局，停止自动重开")
            self.war.loop()
            if not self.finish_run():
                return False
