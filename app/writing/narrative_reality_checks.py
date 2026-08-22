"""Deterministic, warning-only checks for objective narrative reality errors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re


REALITY_CHECK_VERSION = "narrative-reality-checks-v0.1"


@dataclass(frozen=True)
class RealityWarning:
    code: str
    message: str
    evidence: str
    source_scope: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_SCHEDULE_RE = re.compile(
    r"(?:凌晨|早上|上午)?([零〇一二两三四五六七八九十两\d]{1,3})点(半)?开始(揉面|营业|生产|烘焙)"
)
_CLOCK_RE = re.compile(
    r"(?:凌晨|早上|上午)?([零〇一二两三四五六七八九十\d]{1,3})点"
    r"(?:(半)|([零〇一二两三四五六七八九十\d]{1,3})(?:分)?)?"
)
_WEEKDAY_RE = re.compile(r"(?:今天|当天|那天)?(?:是|为)?周([一二三四五六日天])")
_BUSINESS_DAY_RE = re.compile(r"只(?:在)?周([一二三四五六日天])营业")


def _hour(value: str, half: str | None) -> float | None:
    if value.isdigit():
        base = int(value)
    elif value == "十":
        base = 10
    elif "十" in value:
        left, right = value.split("十", 1)
        base = (_CN_DIGITS.get(left, 1) * 10) + _CN_DIGITS.get(right, 0)
    else:
        base = _CN_DIGITS.get(value)
    if base is None:
        return None
    return float(base) + (0.5 if half else 0.0)


def _clock_hour(match: re.Match[str]) -> float | None:
    value = _hour(match.group(1), match.group(2))
    if value is None or match.group(2) or not match.group(3):
        return value
    minute = _hour(match.group(3), None)
    if minute is None or minute >= 60:
        return None
    return value + minute / 60


def _excerpt(text: str, start: int, width: int = 140) -> str:
    left = max(0, start - 35)
    return re.sub(r"\s+", " ", text[left:left + width]).strip()


def _looks_like_clock_reference(text: str, match: re.Match[str]) -> bool:
    """Reject quantity/texture phrases such as “一点微咸” as clock times."""
    token = match.group(0)
    if any(prefix in token for prefix in ("凌晨", "早上", "上午")):
        return True
    if match.lastindex and match.lastindex >= 3 and match.group(3):
        return True
    following = text[match.end():match.end() + 3]
    if re.match(r"(?:差|整|前|后|左右|钟|，|。|、|；|：|\s|$)", following):
        return True
    # “三点半” is already fully consumed by the time regex.
    return bool(match.group(2))


class NarrativeRealityChecker:
    """Stateful local checker. It records evidence but never edits or retries."""

    def __init__(self, *, enabled: bool = True, allowed_names: list[str] | None = None):
        self.enabled = enabled
        self.allowed_names = {
            str(item).strip() for item in (allowed_names or []) if str(item).strip()
        }
        self._history: list[str] = []
        self.records: list[dict] = []

    def observe(
        self,
        text: str,
        *,
        section: int,
        subsection: int,
        known_context: str = "",
    ) -> dict | None:
        if not self.enabled:
            return None
        current = str(text or "")
        history = "\n".join(self._history)
        known = "\n".join(part for part in (known_context, history) if part)
        warnings: list[RealityWarning] = []
        warnings.extend(self._check_schedule(current, known))
        warning = self._check_closed_business_smell(current, known)
        if warning:
            warnings.append(warning)
        warning = self._check_location_anchor_conflict(current, known)
        if warning:
            warnings.append(warning)
        warnings.extend(self._check_unsupported_names(current, known_context, history))
        warning = self._check_recording_permission(current)
        if warning:
            warnings.append(warning)
        warning = self._check_institutional_completion(current, known)
        if warning:
            warnings.append(warning)
        warning = self._check_information_provenance(current, known)
        if warning:
            warnings.append(warning)
        warning = self._check_process_duration(current, known)
        if warning:
            warnings.append(warning)
        warning = self._check_object_continuity(current, history)
        if warning:
            warnings.append(warning)

        record = {
            "version": REALITY_CHECK_VERSION,
            "section": section,
            "subsection": subsection,
            "text_hash": hashlib.sha256(current.encode("utf-8")).hexdigest(),
            "warning_count": len(warnings),
            "warnings": [item.to_dict() for item in warnings],
            "production_effect": False,
        }
        self.records.append(record)
        self._history.append(current)
        return record

    @staticmethod
    def _check_schedule(current: str, known: str) -> list[RealityWarning]:
        schedules: list[tuple[float, str]] = []
        for match in _SCHEDULE_RE.finditer(f"{known}\n{current}"):
            value = _hour(match.group(1), match.group(2))
            if value is not None:
                schedules.append((value, match.group(3)))
        warnings: list[RealityWarning] = []
        for scheduled, action in schedules:
            clock_matches = [
                match for match in _CLOCK_RE.finditer(current)
                if _looks_like_clock_reference(current, match)
            ]
            for index, match in enumerate(clock_matches):
                actual = _clock_hour(match)
                if actual is None or actual >= scheduled:
                    continue
                # Only inspect the prose governed by this timestamp. Without
                # this boundary, "3:00 arrived; at 3:30 kneading began" would
                # be misread as kneading at 3:00.
                tail = current[match.end():]
                if index + 1 < len(clock_matches):
                    next_time = clock_matches[index + 1]
                    tail = current[match.end():next_time.start()]
                segment = tail[:180]
                if re.search(rf"开始{action}", segment):
                    continue
                if re.search(rf"{action}(?:声|的声音|时|动作|节奏)|正在{action}", segment):
                    warnings.append(RealityWarning(
                        code="activity_before_established_schedule",
                        message=f"正文在既定 {scheduled:g} 点之前呈现“{action}”已经发生。",
                        evidence=_excerpt(current, match.start()),
                        source_scope="current_vs_known_context",
                    ))
                    return warnings
        return warnings

    @staticmethod
    def _check_closed_business_smell(current: str, known: str) -> RealityWarning | None:
        combined = f"{known}\n{current}"
        # A closed day mentioned in an earlier subsection must not leak into a
        # later Saturday scene. Closure evidence is scoped to the current text;
        # only the standing business-day rule may come from known context.
        explicitly_closed = re.search(
            r"(?:今天是)?周[一二三四五].{0,50}(?:不营业|店没开|闭店|店门关着)",
            current,
        )
        business_days = {match.group(1) for match in _BUSINESS_DAY_RE.finditer(combined)}
        current_days = {match.group(1) for match in _WEEKDAY_RE.finditer(current)}
        scheduled_closed = bool(
            business_days
            and current_days
            and all(day not in business_days for day in current_days)
        )
        smell = re.search(
            r"(?:门缝.{0,40}(?:香气|香味|气味)|"
            r"(?:香气|香味|气味).{0,40}门缝|"
            r"(?:新鲜|带着温度).{0,30}(?:香气|香味|气味)|"
            r"(?:香气|香味|气味).{0,30}(?:新鲜|带着温度))",
            current,
        )
        recalled_only = smell and re.search(
            r"(?:想起|记得|回忆).{0,45}" + re.escape(smell.group(0)), current
        )
        if (
            (explicitly_closed or scheduled_closed)
            and smell
            and not recalled_only
            and not re.search(r"(?:试做|备货|加班生产|临时生产|店主解释|因为|提前做|提前烤)", current)
        ):
            return RealityWarning(
                code="closed_business_activity_without_cause",
                message="非营业日出现店内生产迹象或新鲜香气，但正文没有提供原因。",
                evidence=_excerpt(current, smell.start()),
                source_scope="current_vs_known_context",
            )
        return None

    @staticmethod
    def _check_location_anchor_conflict(current: str, known: str) -> RealityWarning | None:
        combined = f"{known}\n{current}"
        venue_match = re.search(r"(?:店叫|名叫)「([^」]{2,12})」", current)
        if not venue_match:
            return None
        venue = venue_match.group(1)
        office_side = re.search(
            rf"(?:写字楼|国贸)[\s\S]{{0,500}}街对面[\s\S]{{0,260}}(?:{re.escape(venue)}|那家店)",
            current,
        )
        home_side = re.search(
            rf"(?:租住的|她住的|家附近的|老)?小区(?:门口|附近)"
            rf"[\s\S]{{0,180}}(?:{re.escape(venue)}|那家店)",
            combined,
        )
        separated = re.search(
            r"(?:打了车|坐车|乘车)[\s\S]{0,300}(?:从)?国贸"
            r"[\s\S]{0,180}(?:老小区|红砖楼|住处|家)",
            current,
        )
        if office_side and home_side and separated:
            return RealityWarning(
                code="location_anchor_conflict",
                message=f"地点“{venue}”同时被锚定在写字楼街对面和乘车抵达的住处附近。",
                evidence=_excerpt(current, venue_match.start(), width=220),
                source_scope="current_vs_known_context",
            )
        return None

    def _check_unsupported_names(
        self, current: str, known_context: str, history: str,
    ) -> list[RealityWarning]:
        patterns = (
            re.compile(r"(?:名字|姓名)[是叫：:\s—-]*([\u4e00-\u9fff]{2,4})"),
            re.compile(r"名叫([\u4e00-\u9fff]{2,4})"),
        )
        warnings: list[RealityWarning] = []
        known = f"{known_context}\n{history}"
        for pattern in patterns:
            for match in pattern.finditer(current):
                name = match.group(1)
                if name in self.allowed_names or name in known:
                    continue
                warnings.append(RealityWarning(
                    code="unsupported_named_entity",
                    message=f"新名字“{name}”未在人物、设定、大纲或前文来源中出现。",
                    evidence=_excerpt(current, match.start()),
                    source_scope="current_vs_sources",
                ))
        return warnings

    @staticmethod
    def _check_recording_permission(current: str) -> RealityWarning | None:
        permission_re = re.compile(
            r"(?:可以拍|你拍吧|同意拍|允许拍|别开闪光灯|拍之前问|征得.{0,8}同意)"
        )
        action_re = re.compile(r"(?:举起相机|举起手机|按下快门|拍了很多张|拍摄)")
        for action in action_re.finditer(current):
            left = max(0, action.start() - 140)
            right = min(len(current), action.end() + 220)
            local = current[left:right]
            action_tail = current[action.start():right]
            # A photograph of a closed window is not a photograph of the
            # person behind it. Continue until the first locally evidenced
            # person/figure shot so the warning points at the repair site.
            window_only = re.search(r"(?:只有|全是).{0,18}(?:窗|百叶窗)", action_tail)
            person_target = re.search(
                r"(?:背影|人影|对准.{0,12}[\u4e00-\u9fff]{2,4}|"
                r"取景框里.{0,35}(?:人|他|她)|拍下.{0,18}(?:他|她|周野))",
                action_tail,
            )
            private_space = re.search(
                r"(?:操作间|工作室|跨过门槛|店门口|门里|侧面的窗)", local
            )
            permission_before_action = permission_re.search(current[:action.end()])
            if window_only and not person_target:
                continue
            if person_target and private_space and not permission_before_action:
                return RealityWarning(
                    code="recording_without_explicit_permission",
                    message="正文在私人工作空间拍摄人物，但拍摄前没有出现明确许可或边界冲突。",
                    evidence=_excerpt(current, action.start(), width=200),
                    source_scope="current",
                )
        return None

    @staticmethod
    def _check_institutional_completion(current: str, known: str) -> RealityWarning | None:
        private_mail = re.search(
            r"收件人.{0,20}(?:自己的私人邮箱|私人邮箱|自己的邮箱)",
            f"{known}\n{current}",
        )
        completed = re.search(r"(?:辞职后|辞职那天|已经辞职|正式离职|裸辞后)", current)
        formal_delivery = re.search(r"(?:发给|送达|抄送).{0,12}(?:主管|老板|HR|人事)", current)
        if private_mail and completed and not formal_delivery:
            return RealityWarning(
                code="institutional_action_marked_complete_without_delivery",
                message="辞职信息仅送达私人邮箱，却被后文表述为已正式完成。",
                evidence=_excerpt(current, completed.start()),
                source_scope="current_vs_history",
            )
        return None

    @staticmethod
    def _check_information_provenance(current: str, known: str) -> RealityWarning | None:
        combined = f"{known}\n{current}"
        private_mail = re.search(
            r"(?:收件人.{0,24}(?:自己的私人邮箱|私人邮箱|自己的邮箱)|"
            r"辞职信.{0,16}(?:发到|发送到|寄到).{0,10}(?:自己的私人邮箱|私人邮箱|自己的邮箱))",
            combined,
        )
        knowledge = re.search(
            r"([\u4e00-\u9fff]{2,4})的消息[：:]?.{0,24}"
            r"(?:你辞职了|你离职了|听说你辞职|听说你离职)",
            current,
        )
        transmission = re.search(
            r"(?:告诉|转告|发给|抄送|群里通知|人事通知|HR通知|主管通知|"
            r"老板通知|听.{0,8}说).{0,30}(?:辞职|离职)|"
            r"(?:辞职|离职).{0,30}(?:告诉|转告|发给|抄送|通知)",
            combined,
        )
        if private_mail and knowledge and not transmission:
            return RealityWarning(
                code="knowledge_without_transmission_path",
                message=f"“{knowledge.group(1)}”得知辞职，但正文没有提供信息传播路径。",
                evidence=_excerpt(current, knowledge.start()),
                source_scope="current_vs_history",
            )
        return None

    @staticmethod
    def _check_process_duration(current: str, known: str) -> RealityWarning | None:
        schedule_match = re.search(
            r"(?:凌晨)?([零〇一二两三四五六七八九十\d]{1,3})点(半)?开始揉面",
            f"{known}\n{current}",
        )
        if not schedule_match:
            return None
        start = _hour(schedule_match.group(1), schedule_match.group(2))
        if start is None:
            return None
        finish = re.search(
            r"(?:把|将)?(?:一炉|一盘|烤好的)?面包.{0,18}(?:从烤箱里|出炉)|"
            r"从烤箱里.{0,18}(?:端出|取出).{0,10}面包",
            current,
        )
        if not finish:
            return None
        preceding_times = [
            match for match in _CLOCK_RE.finditer(current)
            if (
                match.start() <= finish.start()
                and _looks_like_clock_reference(current, match)
            )
        ]
        if not preceding_times:
            return None
        finish_time = _clock_hour(preceding_times[-1])
        if finish_time is None:
            return None
        elapsed = finish_time - start
        carryover = re.search(
            r"(?:提前醒发|前一晚|昨晚|上一批|另一批|已经发好|预先准备|冷藏发酵)",
            current,
        )
        if 0 <= elapsed < 1.5 and not carryover:
            return RealityWarning(
                code="process_duration_without_prior_batch",
                message=(
                    f"从既定揉面时间到成品出炉仅约 {round(elapsed * 60):g} 分钟，"
                    "正文未说明这是预先准备的另一批面团。"
                ),
                evidence=_excerpt(current, finish.start()),
                source_scope="current_vs_known_context",
            )
        return None

    @staticmethod
    def _check_object_continuity(current: str, history: str) -> RealityWarning | None:
        prior_cup_taken = re.search(
            r"(?:手里|端着|拿着).{0,18}(?:搪瓷)?杯[\s\S]{0,420}(?:往家|回家|离开)",
            history,
        )
        same_cup_reappears = re.search(r"(?:还是那只|同一只).{0,8}(?:搪瓷)?杯", current)
        if prior_cup_taken and same_cup_reappears:
            return RealityWarning(
                code="object_location_conflict",
                message="前文人物带走杯子后，同一只杯子又在原地点由他人持有。",
                evidence=_excerpt(current, same_cup_reappears.start()),
                source_scope="current_vs_history",
            )
        return None
