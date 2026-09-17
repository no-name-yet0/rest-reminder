"""配置读写与路径解析。

绿色版约定：config.json 与 exe 放在同一目录，
只有在该目录不可写（只读介质、Program Files 等）时才回退到 %APPDATA%。
全程不写注册表（开机自启除外，见 winapi.autostart）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

CONFIG_NAME = "config.json"
APP_DIR_NAME = "RestReminder"

DEFAULT_TIPS: List[str] = [
    "把手机放到房间另一头，别带上床",
    "去洗漱吧，热水能让身体更快进入睡眠节奏",
    "把明天要做的事写下来，然后就交给明天",
    "关掉屏幕，让眼睛先暗下来",
    "把杯子洗了，给自己一个干净的收尾",
    "灯光调暗一点，身体会跟着放松",
    "定好明天的闹钟，剩下的都可以放下了",
    "睡前这半小时，工作消息明天再看",
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "first_run_done": False,
    "theme": "dark",  # dark | light | system

    # 睡前提醒时段：start 开始提醒，bedtime 目标睡觉时间，end 之后不再安排
    "rules": [
        {
            "id": "r1",
            "enabled": True,
            "name": "早睡提醒",
            "days": [0, 1, 2, 3, 4, 5, 6],  # 0=周一 … 6=周日
            "start": "22:00",
            "bedtime": "23:30",
            "end": "00:30",
            "interval_min": 30,   # 预热期基础间隔
            "ramp": True,         # 越接近 bedtime 间隔越短
            "jitter": 0.15,
        }
    ],

    "reminder": {
        # gentle 温和（最高到弹窗）/ standard 标准（可升到全屏）/ hardcore 硬核（全屏+密码）
        "strength": "standard",
        "channels": {
            "toast": True,
            "popup": True,
            "overlay": True,
            "sound": True,
        },
        "sound_volume": 55,
        "pre_notice": True,
        "pre_notice_min": 5,
        "snooze_options": [5, 15, 30],  # 「再给我 X 分钟」的档位
        "escalate_after_sec": 45,       # 无响应多久自动升一级
        "min_interval_min": 5,          # 超时阶段的最短间隔
    },

    "bedtime": {
        "confirm_idle_min": 10,     # 离开电脑多久算「已去休息」
        "overtime_boost_min": 15,   # 超过 bedtime 多久强度拉满
        "overtime_grace_min": 60,   # 窗口结束后最多再追多久
        "show_countdown": True,     # 提醒里显示距睡觉时间的倒计时
    },

    "smart": {
        "idle_pause": True,
        "idle_threshold_min": 10,
        "fullscreen_defer": True,
        "fullscreen_defer_min": 10,
        "random_jitter": True,
    },

    # 不属于早睡场景，默认关闭；白天想护眼可以自己打开
    "eye_care": {
        "enabled": False,
        "interval_min": 20,
        "duration_sec": 20,
    },

    # 收尾倒计时：点「再给我 10 分钟」后启动，时间到直接进最强提醒
    "wrapup": {
        "enabled": True,
        "minutes": 10,
        "show_tips": True,
    },

    # 点「我去睡了」之后做什么。none 只记录；shutdown 会真的把电脑关掉，
    # 但先给一段可撤销的宽限时间，避免手滑或文件没保存。
    "after_rest": {
        "mode": "none",     # none | shutdown
        "grace_sec": 120,   # 给用户保存文件和反悔的时间
    },

    # 收尾模式：手动点「我要收了」后进入。
    # 按下面的节奏依次提醒，间隔越来越短、强度递增；
    # 走完最后一档就一直用最短间隔催，直到确认收了或离开电脑。
    "winddown": {
        "intervals_min": [5, 2.5, 2, 1],
        # ramp 从轻到重 / from_popup 从弹窗起步 / always_full 全程全屏
        "level_mode": "ramp",
        # 点「延后提醒」之后，隔多久再来（分钟）
        "defer_min": 5,
    },

    "tips": list(DEFAULT_TIPS),

    "general": {
        "autostart": False,
        "hotkey_enabled": True,
        "hotkey": "Ctrl+Alt+R",
        "exit_confirm": True,
        "password_enabled": False,
        "password_salt": "",
        "password_hash": "",
        "reduce_motion": False,
        "last_intensity_hint": "",
    },
}

# ---------------------------------------------------------------- 路径解析


def app_dir() -> str:
    """返回 exe（或源码运行时的项目根）所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_FILE_READ_ONLY_VOLUME = 0x00080000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


def _volume_is_readonly(path: str) -> bool:
    """所在卷是不是只读介质（光盘、写保护的 U 盘）。"""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        flags = ctypes.c_uint32(0)
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive),
            None,
            0,
            None,
            None,
            ctypes.byref(flags),
            None,
            0,
        )
        return bool(ok and (flags.value & _FILE_READ_ONLY_VOLUME))
    except Exception:
        return False


def _dir_has_write_access(path: str) -> bool:
    """用 Windows 原生方式检查目录写权限。

    以写方式打开目录句柄（需要 FILE_FLAG_BACKUP_SEMANTICS），
    由系统直接做 ACL 判定，**不会创建任何文件**。

    这一点很重要：早先用"建临时文件再删"来探测，一旦删除失败
    （杀软或系统索引短暂占用句柄），就会在用户的程序目录里
    留下 .wtest 垃圾文件。
    """
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        handle = kernel32.CreateFileW(
            path,
            _GENERIC_WRITE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            return False
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return True
    except Exception:
        return False


def _probe_writable(path: str) -> bool:
    """兜底方案：真的建一个文件再删掉。只在非 Windows 或系统调用不可用时使用。"""
    import time

    tmp: Optional[str] = None
    ok = False
    try:
        fd, tmp = tempfile.mkstemp(prefix=".wtest", dir=path)
        os.close(fd)
        ok = True
    except Exception:
        ok = False

    if tmp:
        for attempt in range(5):
            try:
                os.remove(tmp)
                break
            except Exception:
                time.sleep(0.05 * (attempt + 1))
    return ok


def _dir_writable(path: str) -> bool:
    """目录是否可写。优先走系统调用，不在用户目录里留任何文件。"""
    if not os.path.isdir(path):
        return False
    if sys.platform.startswith("win"):
        if _volume_is_readonly(path):
            return False
        return _dir_has_write_access(path)
    return _probe_writable(path)


# 探测可写性需要真实创建文件，结果缓存起来，不要每次调用都做一遍
_DATA_DIR_CACHE: Optional[str] = None


def data_dir() -> str:
    """配置目录：优先 exe 同目录，不可写则回退 %APPDATA%。"""
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE and os.path.isdir(_DATA_DIR_CACHE):
        return _DATA_DIR_CACHE

    here = app_dir()
    if _dir_writable(here):
        _DATA_DIR_CACHE = here
        return here

    fallback = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), APP_DIR_NAME
    )
    os.makedirs(fallback, exist_ok=True)
    _DATA_DIR_CACHE = fallback
    return fallback


def config_path() -> str:
    return os.path.join(data_dir(), CONFIG_NAME)


def is_portable() -> bool:
    """配置是否落在 exe 同目录（绿色模式）。"""
    return os.path.normcase(data_dir()) == os.path.normcase(app_dir())


# ---------------------------------------------------------------- 读写


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load() -> Dict[str, Any]:
    """读取配置；缺失字段用默认值补齐，文件损坏时回退默认值。"""
    path = config_path()
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}
    return _deep_merge(DEFAULT_CONFIG, raw)


def save(cfg: Dict[str, Any]) -> bool:
    """原子写入配置，返回是否成功。"""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cfg", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 密码


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + "|" + password).encode("utf-8")).hexdigest()


def make_salt() -> str:
    return os.urandom(12).hex()


def verify_password(cfg: Dict[str, Any], password: str) -> bool:
    general = cfg.get("general", {})
    stored = general.get("password_hash") or ""
    if not stored:
        return True
    return hash_password(password, general.get("password_salt", "")) == stored


def set_password(cfg: Dict[str, Any], password: str) -> None:
    general = cfg.setdefault("general", {})
    if password:
        salt = make_salt()
        general["password_salt"] = salt
        general["password_hash"] = hash_password(password, salt)
        general["password_enabled"] = True
    else:
        general["password_salt"] = ""
        general["password_hash"] = ""
        general["password_enabled"] = False


# ---------------------------------------------------------------- 时间段


def parse_hhmm(text: str) -> int:
    """'21:30' -> 1290（分钟）。解析失败返回 0。"""
    try:
        hh, mm = str(text).strip().split(":")
        return max(0, min(23, int(hh))) * 60 + max(0, min(59, int(mm)))
    except Exception:
        return 0


def fmt_hhmm(minutes: int) -> str:
    minutes = max(0, min(24 * 60 - 1, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def rule_active_now(rule: Dict[str, Any], now) -> bool:
    """判断某条规则在给定时间点是否生效。"""
    if not rule.get("enabled", True):
        return False
    days = rule.get("days") or []
    if days and now.weekday() not in days:
        return False
    start = parse_hhmm(rule.get("start", "00:00"))
    end = parse_hhmm(rule.get("end", "23:59"))
    cur = now.hour * 60 + now.minute
    if start <= end:
        return start <= cur <= end
    # 跨零点，例如 22:30 - 02:00
    return cur >= start or cur <= end


def active_rules(cfg: Dict[str, Any], now) -> List[Dict[str, Any]]:
    return [r for r in cfg.get("rules", []) if rule_active_now(r, now)]


# 收紧期提前量：距离目标睡觉时间不足这么久，就进入间隔逐步缩短的阶段
RAMP_LEAD_MIN = 45


def _normalized(rule: Dict[str, Any], now):
    """把 start / bedtime / end / now 归一到同一条不跨零点的时间轴（单位：分钟）。"""
    start = parse_hhmm(rule.get("start", "22:00"))
    bedtime = parse_hhmm(rule.get("bedtime") or rule.get("end", "23:30"))
    end = parse_hhmm(rule.get("end", "23:59"))
    if bedtime < start:
        bedtime += 1440
    if end < start:
        end += 1440
    cur = now.hour * 60 + now.minute
    if cur < start:
        cur += 1440
    return start, min(bedtime, end), end, cur


def minutes_to_bedtime(rule: Dict[str, Any], now) -> int:
    """距目标睡觉时间还有多少分钟；负数表示已经超过。"""
    _, bedtime, _, cur = _normalized(rule, now)
    return bedtime - cur


def rule_phase(rule: Dict[str, Any], now) -> str:
    """当前阶段：pre 预热 / ramp 收紧 / overtime 超时 / '' 未生效。"""
    if not rule_active_now(rule, now):
        return ""
    remain = minutes_to_bedtime(rule, now)
    if remain <= 0:
        return "overtime"
    if remain <= RAMP_LEAD_MIN:
        return "ramp"
    return "pre"


def overtime_minutes(rule: Dict[str, Any], now) -> int:
    return max(0, -minutes_to_bedtime(rule, now))


def clean_config_for_export(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(cfg)
    out.pop("first_run_done", None)
    general = out.get("general", {})
    general.pop("password_hash", None)
    general.pop("password_salt", None)
    return out


def remove_config_file() -> bool:
    try:
        if os.path.isfile(config_path()):
            os.remove(config_path())
        return True
    except Exception:
        return False
