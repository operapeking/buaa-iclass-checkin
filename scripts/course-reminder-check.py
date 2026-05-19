#!/usr/bin/env python3
"""
课程签到提醒检查脚本

读取缓存的当天课表，检查是否有课程在 10-20 分钟前开始但尚未签到。
输出需要提醒的课程列表 (JSON)，供 Hermes Agent cron 发送微信提醒。

用法: python3 course-reminder-check.py [--state-dir /path/to/state]
"""

import argparse
import datetime
import json
import os
import sys


def parse_class_time(value: str):
    """兼容 iClass 返回的两种常见时间格式。"""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default="/home/azureuser/buaa-iclass-checkin/state")
    args = parser.parse_args()

    state_dir = args.state_dir
    today = datetime.datetime.now().strftime("%Y%m%d")
    cache_file = os.path.join(state_dir, f"schedule_{today}.json")

    if not os.path.exists(cache_file):
        # 没有缓存，输出空
        print(json.dumps({"courses": [], "reason": "no_cache"}, ensure_ascii=False))
        return

    with open(cache_file) as f:
        courses = json.load(f)

    now = datetime.datetime.now()
    reminders = []

    for c in courses:
        # 已签到的跳过
        if str(c.get("signStatus")) == "1":
            continue

        begin = parse_class_time(c.get("classBeginTime", ""))
        if begin is None:
            continue

        # 提醒窗口: 课程开始后 10-20 分钟
        minutes_since_start = (now - begin).total_seconds() / 60.0
        if 10 <= minutes_since_start <= 20:
            end = parse_class_time(c.get("classEndTime", ""))
            reminders.append({
                "schedule_id": c.get("id", ""),
                "course_name": c.get("courseName", "未知课程"),
                "begin_time": begin.strftime("%H:%M"),
                "end_time": end.strftime("%H:%M") if end else "??:??",
                "minutes_since_start": round(minutes_since_start),
            })

    result = {
        "courses": reminders,
        "count": len(reminders),
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
