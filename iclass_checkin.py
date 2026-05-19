#!/usr/bin/env python3
"""
BUAA iClass Checkin - 北航 iClass 自动签到工具 (WebVPN CLI)

两阶段工作流:
  Phase 1 (--query):  查询当天课表，根据配置为每节课添加 cron 定时任务
  Phase 2 (--checkin): 执行签到

签到时机由 config.json 的 auto_checkin.offset_minutes 控制，范围为:
  上课前 10 分钟 (-10) 到下课前 1 分钟。

新增功能:
  - 签到重试: 可恢复错误自动重试 (最多 3 次, 间隔 30 秒)
  - Session 持久化: 避免每次都重新登录 WebVPN/CAS
  - 日志写文件: 所有日志同时输出到控制台和日志文件
"""

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] 缺少依赖，请先运行: pip install requests beautifulsoup4")
    sys.exit(1)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── 常量 ───────────────────────────────────────────────

VPN_BASE = "https://d.buaa.edu.cn"
VPN_SERVICE_ID = "77726476706e69737468656265737421f9f44d9d342326526b0988e29d51367ba018"
API_8347 = f"{VPN_BASE}/https-8347/{VPN_SERVICE_ID}"
API_8081 = f"{VPN_BASE}/http-8081/{VPN_SERVICE_ID}"

COURSE_CRON_MARKER = "buaa-iclass-checkin-course"
DAILY_CRON_MARKER = "buaa-iclass-checkin-daily-query"
SCRIPT_PATH = os.path.abspath(__file__)
DEFAULT_AUTO_CHECKIN_ENABLED = True
DEFAULT_CHECKIN_OFFSET_MINUTES = 10  # 上课后 10 分钟签到
MIN_CHECKIN_OFFSET_MINUTES = -10  # 最早上课前 10 分钟

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

# 签到重试参数
SIGN_MAX_RETRIES = 3       # 最大重试次数
SIGN_RETRY_DELAY = 30      # 每次重试间隔 (秒)

# ─── 日志 ───────────────────────────────────────────────

_logger = None


def setup_logger(log_file: str) -> logging.Logger:
    """配置日志: 同时输出到控制台和文件。"""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("iclass_checkin")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    # 控制台
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _logger = logger
    return logger


def log(msg, level="INFO"):
    """兼容旧用法的日志函数。"""
    if _logger is not None:
        getattr(_logger, level.lower(), _logger.info)(msg)
    else:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [{level}] {msg}")


# ─── 认证 ───────────────────────────────────────────────

class BUASignClient:
    """WebVPN + iClass 认证客户端"""

    def __init__(self, student_id: str, password: str, session_file: str = None):
        self.student_id = student_id
        self.password = password
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = False
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        self.user_id = None
        self.session_id = None
        self.server_time_offset_ms = 0
        self.session_file = session_file

    def login(self) -> bool:
        """CAS 登录 + iClass 登录 (优先恢复缓存 session)"""
        if self.restore_session():
            return True
        if not self._cas_login():
            return False
        return self._iclass_login()

    def _cas_login(self) -> bool:
        """通过统一身份认证登录 WebVPN"""
        log("正在连接 SSO 认证服务...")
        try:
            r = self.session.get(VPN_BASE, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log(f"SSO 连接失败: {e}", "ERROR")
            return False

        soup = BeautifulSoup(r.text, "html.parser")
        execution_input = soup.find("input", {"name": "execution"})
        if not execution_input:
            log("无法解析 SSO 页面", "ERROR")
            return False

        execution = execution_input["value"]
        login_url = r.url

        log("正在登录...")
        try:
            r2 = self.session.post(
                login_url,
                data={
                    "username": self.student_id,
                    "password": self.password,
                    "execution": execution,
                    "_eventId": "submit",
                    "lt": "",
                    "dllt": "userNamePasswordLogin",
                    "csrfToken": "",
                },
                timeout=15,
                allow_redirects=True,
            )
        except Exception as e:
            log(f"CAS 登录请求失败: {e}", "ERROR")
            return False

        # 判断是否登录成功: 成功后应跳转到 VPN 首页
        if VPN_BASE in r2.url and "/login" not in r2.url:
            log("✓ CAS 登录成功")
            return True

        log("CAS 登录失败，请检查账号密码", "ERROR")
        return False

    def _iclass_login(self) -> bool:
        """通过 WebVPN 登录 iClass"""
        log("正在登录 iClass...")
        try:
            r = self.session.get(
                f"{API_8347}/app/user/login.action",
                params={
                    "phone": self.student_id,
                    "userLevel": "1",
                    "verificationType": "2",
                    "verificationUrl": "",
                },
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            log(f"iClass 登录失败: {e}", "ERROR")
            return False

        if str(data.get("STATUS")) != "0":
            log(f"iClass 登录失败: {data.get('ERRMSG', 'unknown')}", "ERROR")
            return False

        self.user_id = data["result"]["id"]
        self.session_id = data["result"]["sessionId"]

        # 同步服务器时间
        try:
            r_ts = self.session.get(f"{API_8081}/app/common/get_timestamp.action", timeout=10)
            ts_data = r_ts.json()
            self.server_time_offset_ms = int(ts_data.get("timestamp", 0)) - int(time.time() * 1000)
        except Exception:
            self.server_time_offset_ms = 0

        log(f"✓ iClass 登录成功 (userId={self.user_id})")
        self.save_session()
        return True

    def save_session(self):
        """持久化 session cookies 到文件。"""
        if not self.session_file:
            return
        data = {
            "cookies": dict(self.session.cookies),
            "user_id": self.user_id,
            "session_id": self.session_id,
            "server_time_offset_ms": self.server_time_offset_ms,
            "saved_at": time.time(),
        }
        try:
            session_dir = os.path.dirname(self.session_file)
            if session_dir:
                os.makedirs(session_dir, exist_ok=True)
            with open(self.session_file, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            log(f"Session 已保存到 {self.session_file}", "DEBUG")
        except Exception as e:
            log(f"保存 session 失败: {e}", "WARN")

    def restore_session(self) -> bool:
        """从缓存文件恢复 session, 若有效则跳过登录。"""
        if not self.session_file or not os.path.exists(self.session_file):
            return False

        try:
            with open(self.session_file) as f:
                data = json.load(f)
        except Exception:
            return False

        saved_at = data.get("saved_at", 0)
        # 超过 6 小时的 session 视为过期
        if time.time() - saved_at > 6 * 3600:
            log("缓存 session 已过期 (超过 6 小时)", "DEBUG")
            return False

        try:
            self.session.cookies.update(data.get("cookies", {}))
            self.user_id = data.get("user_id")
            self.session_id = data.get("session_id")
            self.server_time_offset_ms = data.get("server_time_offset_ms", 0)

            # 验证 session 是否有效: 调用一个轻量 API
            r = self.session.get(
                f"{API_8347}/app/course/get_stu_course_sched.action",
                params={"dateStr": datetime.datetime.now().strftime("%Y%m%d"), "id": self.user_id},
                headers={"sessionId": self.session_id},
                timeout=10,
            )
            data = r.json()
            if str(data.get("STATUS")) == "0":
                log(f"✓ 从缓存恢复 session 成功 (userId={self.user_id})")
                return True
        except Exception:
            pass

        log("缓存 session 无效，重新登录", "DEBUG")
        # 清除无效的缓存
        try:
            os.remove(self.session_file)
        except OSError:
            pass
        return False

    def get_schedule(self, date_str: str) -> list:
        """获取指定日期的课程表"""
        try:
            r = self.session.get(
                f"{API_8347}/app/course/get_stu_course_sched.action",
                params={"dateStr": date_str, "id": self.user_id},
                headers={"sessionId": self.session_id},
                timeout=15,
            )
            data = r.json()
            if str(data.get("STATUS")) == "0":
                return data.get("result", [])
        except Exception as e:
            log(f"获取课表失败: {e}", "ERROR")
        return []

    def sign(self, schedule_id: str) -> tuple[bool, str, bool]:
        """执行签到，返回 (成功, 消息, 可重试)。

        可重试=True 表示失败是暂时的 (网络/服务未就绪), 可以稍后重试。
        可重试=False 表示失败是永久的 (参数错误/已过期), 重试无意义。
        """
        ts = str(int(time.time() * 1000) + self.server_time_offset_ms)
        try:
            r = self.session.post(
                f"{API_8081}/app/course/stu_scan_sign.action",
                params={"id": self.user_id, "courseSchedId": schedule_id, "timestamp": ts},
                headers={"sessionId": self.session_id},
                timeout=15,
            )
            data = r.json()
            status = str(data.get("STATUS"))
            msg = data.get("ERRMSG", "")
            if status == "0":
                return True, msg or "签到成功", False
            if "已签到" in msg:
                return True, "已签到", False
            # 可恢复: 老师还没发起签到 / 服务暂时不可用
            if "不是上课时间" in msg or "网络" in msg or "超时" in msg:
                return False, msg or "签到失败", True
            # 其他失败 (参数错误、签到已过期等)
            return False, msg or "签到失败", False
        except requests.exceptions.RequestException as e:
            # 网络异常: 可重试
            return False, str(e), True
        except Exception as e:
            return False, str(e), False


# ─── 配置 ───────────────────────────────────────────────

def load_config(path: str) -> dict:
    if not os.path.exists(path):
        log(f"配置文件不存在: {path}", "ERROR")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def get_script_dir() -> str:
    return os.path.dirname(SCRIPT_PATH)


def course_cron_managed(line: str) -> bool:
    """判断 crontab 行是否是本工具创建的课程签到任务。"""
    return COURSE_CRON_MARKER in line


def cron_managed(line: str) -> bool:
    """判断 crontab 行是否由本工具创建。"""
    return COURSE_CRON_MARKER in line or DAILY_CRON_MARKER in line


def parse_class_time(value: str) -> datetime.datetime | None:
    """兼容 iClass 返回的两种常见时间格式。"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def get_auto_checkin_config(cfg: dict) -> tuple[bool, int]:
    """读取自动签到配置。

    推荐格式:
      "auto_checkin": {"enabled": true, "offset_minutes": 10}
    """
    auto = cfg.get("auto_checkin", {})
    if auto is None:
        auto = {}
    if not isinstance(auto, dict):
        raise ValueError("auto_checkin 必须是对象，例如 {\"enabled\": true, \"offset_minutes\": 10}")

    enabled = bool(auto.get("enabled", DEFAULT_AUTO_CHECKIN_ENABLED))
    offset = auto.get("offset_minutes", DEFAULT_CHECKIN_OFFSET_MINUTES)
    try:
        offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("auto_checkin.offset_minutes 必须是整数分钟") from exc

    if offset < MIN_CHECKIN_OFFSET_MINUTES:
        raise ValueError("auto_checkin.offset_minutes 不能早于上课前 10 分钟，即不能小于 -10")

    return enabled, offset


def validate_checkin_time(course: dict, offset_minutes: int) -> tuple[datetime.datetime | None, str | None]:
    """计算并校验签到时间。

    offset_minutes 以课程开始时间为基准:
      -10 表示上课前 10 分钟;
       10 表示上课后 10 分钟。

    可用范围是 [上课前 10 分钟, 下课前 1 分钟]。
    """
    begin = parse_class_time(course.get("classBeginTime", ""))
    end = parse_class_time(course.get("classEndTime", ""))
    name = course.get("courseName", "?")

    if begin is None:
        return None, f"[{name}] 无法解析上课时间: {course.get('classBeginTime', '')}"
    if end is None:
        return None, f"[{name}] 无法解析下课时间: {course.get('classEndTime', '')}"

    sign_dt = begin + datetime.timedelta(minutes=offset_minutes)
    earliest = begin + datetime.timedelta(minutes=MIN_CHECKIN_OFFSET_MINUTES)
    latest = end - datetime.timedelta(minutes=1)
    if sign_dt < earliest or sign_dt > latest:
        return None, (
            f"[{name}] 签到时间 {sign_dt.strftime('%H:%M')} 超出允许范围 "
            f"({earliest.strftime('%H:%M')} - {latest.strftime('%H:%M')})"
        )
    return sign_dt, None


# ─── Phase 1: 查询课表 + 注册 cron ─────────────────────

def phase_query(config_path: str, state_dir: str):
    cfg = load_config(config_path)
    student_id = cfg["student_id"]
    password = cfg["password"]
    course_ids = cfg.get("course_ids", [])  # 空列表 = 全部课程

    try:
        auto_enabled, checkin_offset = get_auto_checkin_config(cfg)
    except ValueError as exc:
        log(f"配置错误: {exc}", "ERROR")
        sys.exit(1)

    session_file = os.path.join(state_dir, "session.json")
    client = BUASignClient(student_id, password, session_file=session_file)
    if not client.login():
        sys.exit(1)

    today = datetime.datetime.now().strftime("%Y%m%d")
    log(f"查询 {today} 课表...")

    courses = client.get_schedule(today)
    if not courses:
        log("今天没有课程")
        return

    # 缓存课表到本地 (Phase 2 可用于展示课程名)
    os.makedirs(state_dir, exist_ok=True)
    cache_file = os.path.join(state_dir, f"schedule_{today}.json")
    with open(cache_file, "w") as f:
        json.dump(courses, f, ensure_ascii=False)

    # 读取现有 crontab，并清理本工具当天生成的旧任务
    existing = ""
    try:
        existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
    except subprocess.CalledProcessError:
        pass
    new_lines = [line for line in existing.splitlines() if not course_cron_managed(line)]

    if not auto_enabled:
        log("自动签到已在配置中关闭，仅缓存课表，不注册课程签到任务")
        proc = subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", capture_output=True, text=True)
        if proc.returncode != 0:
            log(f"写入 crontab 失败: {proc.stderr}", "ERROR")
        return

    # 过滤: 只保留目标课程 & 未签到的
    targets = []
    for c in courses:
        if str(c.get("signStatus")) == "1":
            continue
        cid = c.get("courseId", "")
        sid = c.get("id", "")
        name = c.get("courseName", "?")
        if course_ids and cid not in course_ids and sid not in course_ids and name not in course_ids:
            continue
        targets.append(c)

    if not targets:
        log("今天没有需要签到的课程")
        proc = subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", capture_output=True, text=True)
        if proc.returncode != 0:
            log(f"写入 crontab 失败: {proc.stderr}", "ERROR")
        return

    log(f"找到 {len(targets)} 门待签课程，注册 cron 任务...")

    registered = 0
    for c in targets:
        sched_id = c["id"]
        name = c.get("courseName", "?")
        begin_time = c.get("classBeginTime", "")
        end_time = c.get("classEndTime", "")

        sign_dt, error = validate_checkin_time(c, checkin_offset)
        if error:
            log(f"  {error}，跳过", "WARN")
            continue

        cron_expr = f"{sign_dt.minute} {sign_dt.hour} {sign_dt.day} {sign_dt.month} *"
        cmd = f"{sys.executable} {SCRIPT_PATH} --checkin {student_id} {sched_id} --config {config_path}"
        line = f"{cron_expr} {cmd}  # {COURSE_CRON_MARKER}:{student_id}:{sched_id}:{name}"
        new_lines.append(line)
        registered += 1

        offset_text = f"上课前 {abs(checkin_offset)}min" if checkin_offset < 0 else f"上课后 {checkin_offset}min"
        log(f"  [{name}] {sign_dt.strftime('%H:%M')} 签到 ({offset_text}; 课程 {begin_time[11:16]}-{end_time[11:16]})")

    # 写回 crontab
    new_crontab = "\n".join(new_lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, capture_output=True, text=True)
    if proc.returncode == 0:
        log(f"✓ 已注册 {registered} 个签到任务")
    else:
        log(f"写入 crontab 失败: {proc.stderr}", "ERROR")


# ─── Phase 2: 执行签到 (带重试) ─────────────────────────

def phase_checkin(student_id: str, schedule_id: str, config_path: str, state_dir: str):
    cfg = load_config(config_path)
    password = cfg["password"]

    today = datetime.datetime.now().strftime("%Y%m%d")
    cache_file = os.path.join(state_dir, f"schedule_{today}.json")

    # 查缓存获取课程名
    course_name = schedule_id
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            courses = json.load(f)
        for c in courses:
            if c.get("id") == schedule_id:
                course_name = c.get("courseName", schedule_id)
                break

    session_file = os.path.join(state_dir, "session.json")
    client = BUASignClient(student_id, password, session_file=session_file)
    if not client.login():
        log(f"[{course_name}] 登录失败", "ERROR")
        sys.exit(1)

    # 检查是否已签到
    courses = client.get_schedule(today)
    for c in courses:
        if c.get("id") == schedule_id:
            if str(c.get("signStatus")) == "1":
                log(f"[{course_name}] 已签到，跳过")
                return
            break

    # 签到 + 自动重试
    for attempt in range(1, SIGN_MAX_RETRIES + 1):
        ok, msg, retryable = client.sign(schedule_id)
        if ok:
            log(f"[{course_name}] ✓ {msg}")
            return
        if not retryable or attempt == SIGN_MAX_RETRIES:
            log(f"[{course_name}] ✗ {msg} (第 {attempt}/{SIGN_MAX_RETRIES} 次)", "ERROR")
            sys.exit(1)
        log(f"[{course_name}] 第 {attempt}/{SIGN_MAX_RETRIES} 次签到失败: {msg}, {SIGN_RETRY_DELAY}s 后重试...", "WARN")
        time.sleep(SIGN_RETRY_DELAY)


# ─── 主入口 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BUAA iClass Checkin (WebVPN CLI)")
    parser.add_argument("--query", action="store_true", help="查询课表并按配置注册 cron 任务")
    parser.add_argument("--checkin", nargs=2, metavar=("STUDENT_ID", "SCHEDULE_ID"), help="执行签到")
    parser.add_argument("--config", default=os.path.join(get_script_dir(), "config.json"), help="配置文件路径")
    parser.add_argument("--state-dir", default=os.path.join(get_script_dir(), "state"), help="状态缓存目录")
    parser.add_argument("--clear-cron", action="store_true", help="清除所有本工具定时任务")
    parser.add_argument("--show-cron", action="store_true", help="查看已注册的定时任务")

    args = parser.parse_args()

    # 初始化日志 (写入 iclass-checkin.log)
    log_file = os.path.join(args.state_dir, "iclass-checkin.log")
    setup_logger(log_file)

    if args.clear_cron:
        existing = ""
        try:
            existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError:
            pass
        new_lines = [l for l in existing.splitlines() if not cron_managed(l)]
        subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", capture_output=True, text=True)
        log("✓ 已清除所有本工具定时任务")
        return

    if args.show_cron:
        existing = ""
        try:
            existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
        except subprocess.CalledProcessError:
            pass
        jobs = [l for l in existing.splitlines() if cron_managed(l)]
        if jobs:
            print(f"已注册 {len(jobs)} 个定时任务:")
            for j in jobs:
                print(f"  {j}")
        else:
            print("暂无签到任务")
        return

    if args.query:
        phase_query(args.config, args.state_dir)
    elif args.checkin:
        student_id, schedule_id = args.checkin
        phase_checkin(student_id, schedule_id, args.config, args.state_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
